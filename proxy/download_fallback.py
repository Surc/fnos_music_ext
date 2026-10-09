"""Bounded, whole-file musicdl fallback. Never switches a live playback stream.

Matching requires title, all credited artists, version markers and known duration.
Quality describes the downloaded encoding; it cannot prove a provider never
transcoded its master. No URLs, credentials or private listening data are recorded.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx

PLATFORMS = ("kuwo", "migu", "kugou", "qq")
ENV_PREFIX = "FNMUSIC_DOWNLOAD_FALLBACK_"
HOT_KEYS = ("ENABLED", "SOURCES", "TARGET", "ALLOW_DOWNGRADE", "BUDGET_S", "MAX_MB")
LOSSLESS_CODECS = {"flac": "flac", "alac": "m4a", "pcm_s16le": "wav", "pcm_s24le": "wav",
                   "pcm_s32le": "wav", "wavpack": "wv"}
LOSSY_CODECS = {"mp3": "mp3", "aac": "m4a", "vorbis": "ogg", "opus": "opus"}
_VERSIONS = {
    "live": r"\blive\b|现场|演唱会",
    "instrumental": r"\binstrumental\b|\bkaraoke\b|伴奏|纯音乐",
    "cover": r"\bcover\b|翻唱",
    "remix": r"\bremix\b|\bdj\b|混音",
    "acoustic": r"\bacoustic\b|不插电",
    "remaster": r"\bremaster(?:ed)?\b|重制|重录|重新录制",
    "edit": r"\bradio edit\b|\bextended\b|\bsped up\b|\bslowed\b|加速|慢速",
    "demo": r"\bdemo\b|小样",
}
_TRIAL = re.compile(r"试听|片段|\bpreview\b|\btrial\b|\bsnippet\b", re.I)


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("true", "1", "yes")


def _bounded(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(ENV_PREFIX + name, str(default)))))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class Settings:
    sources: tuple[str, ...] = ("kuwo", "migu")
    target: str = "lossless"
    allow_downgrade: bool = False
    budget_s: int = 180
    max_mb: int = 150

    @classmethod
    def current(cls) -> "Settings":
        names = os.environ.get(ENV_PREFIX + "SOURCES", "kuwo,migu").lower().split(",")
        sources = tuple(dict.fromkeys(s.strip() for s in names if s.strip() in PLATFORMS))
        target = os.environ.get(ENV_PREFIX + "TARGET", "lossless")
        return cls(sources, target if target in ("lossless", "320k") else "lossless",
                   _flag(ENV_PREFIX + "ALLOW_DOWNGRADE"),
                   _bounded("BUDGET_S", 180, 30, 600), _bounded("MAX_MB", 150, 10, 1024))


def enabled_for(guid: str, conf: dict) -> bool:
    return (guid.startswith("online:netease:") and _flag(ENV_PREFIX + "ENABLED")
            and bool(conf.get("netease_enabled")) and not conf.get("musicdl_enabled")
            and not conf.get("lx_enabled"))


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    if isinstance(value, (tuple, list)):
        return "/".join(_text(v) for v in value)
    return str(value or "")


def canonical(value) -> str:
    s = unicodedata.normalize("NFKC", _text(value)).casefold()
    return "".join(c for c in s if c.isalnum())


def artists(value) -> frozenset[str]:
    raw = _text(value)
    return frozenset(canonical(s) for s in re.split(r"\s*(?:/|、|;|；|&|\bfeat\.?|\bft\.?|\bwith\b)\s*", raw, flags=re.I)
                     if canonical(s))


def duration(meta: dict) -> float:
    for key, divisor in (("duration_s", 1), ("duration_ms", 1000), ("dt", 1000), ("duration", 1000)):
        try:
            value = float(meta.get(key) or 0) / divisor
        except (TypeError, ValueError):
            continue
        if value > 0 and math.isfinite(value):
            return value
    return 0.0


def versions(meta: dict) -> frozenset[str]:
    text = unicodedata.normalize("NFKC", _text(meta.get("title")) + " " + _text(meta.get("album"))).casefold()
    return frozenset(name for name, pattern in _VERSIONS.items() if re.search(pattern, text))


def duration_matches(expected: float, actual: float) -> bool:
    return expected > 0 and actual > 0 and abs(actual - expected) <= max(2.0, min(4.0, expected * 0.01))


def match_track(reference: dict, candidate: dict) -> str | None:
    """Return rejection reason, or None. Unknown identity/duration fails closed."""
    if (candidate.get("trial") or candidate.get("is_trial") or candidate.get("preview")
            or candidate.get("free_trial_info") or _TRIAL.search(_text(candidate.get("title")))):
        return "preview"
    title = canonical(reference.get("title"))
    if not title or title != canonical(candidate.get("title")):
        return "title"
    credited = artists(reference.get("artist"))
    if not credited or credited != artists(candidate.get("artist")):
        return "artist"
    if versions(reference) != versions(candidate):
        return "version"
    if not duration_matches(duration(reference), duration(candidate)):
        return "duration"
    if candidate.get("transcoded_from_lossy") or candidate.get("upscaled"):
        return "transcoded"
    return None


def inspect_audio(path: str) -> dict:
    """Inspect actual bytes and decode the complete audio. Suffix is ignored."""
    probe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not ffmpeg:
        raise RuntimeError("audio validation requires ffprobe and ffmpeg")
    result = subprocess.run([probe, "-v", "error", "-protocol_whitelist", "file,pipe",
                             "-show_format", "-show_streams", "-of", "json", path],
                            capture_output=True, timeout=20, check=True)
    data = json.loads(result.stdout)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ValueError("expected exactly one audio stream")
    audio = streams[0]
    codec = str(audio.get("codec_name") or "")
    ext = (LOSSLESS_CODECS | LOSSY_CODECS).get(codec)
    if not ext:
        raise ValueError("unsupported audio encoding")
    seconds = float(audio.get("duration") or data.get("format", {}).get("duration") or 0)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("missing audio duration")
    rate = int(audio.get("bit_rate") or 0)
    # Container bitrate includes pictures/tags. Do not call that an audio bitrate.
    decoded = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-nostdin",
                              "-protocol_whitelist", "file,pipe", "-i", path,
                              "-map", "0:a:0", "-f", "null", "-"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
    if decoded.returncode != 0 or decoded.stderr.strip():
        raise ValueError("complete audio decode failed")
    return {"codec": codec, "ext": ext, "duration_s": seconds, "bitrate": rate,
            "sample_rate": int(audio.get("sample_rate") or 0),
            "bits_per_sample": int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0),
            "lossless_encoding": codec in LOSSLESS_CODECS, "original_lossless_verified": False,
            "complete_decode_checked": True}


def meets_target(audio: dict, target: str) -> bool:
    if audio.get("lossless_encoding"):
        return True
    return target == "320k" and int(audio.get("bitrate") or 0) >= 304000


def quality_rank(audio: dict) -> tuple[int, int]:
    return (int(bool(audio.get("lossless_encoding"))), int(audio.get("bitrate") or 0))


@dataclass
class Download:
    path: str
    info: dict
    audio: dict
    provider: str
    provider_id: str
    owned: bool = True

    def discard(self) -> None:
        if self.owned:
            with suppress(FileNotFoundError):
                os.unlink(self.path)

    def provenance(self, guid: str, settings: Settings, trigger: str) -> dict:
        return {"schema_version": 1, "original_guid": guid, "provider": self.provider,
                "provider_track_id": self.provider_id,
                "matched_track": {k: _text(self.info.get(k)) for k in ("title", "artist", "album")},
                "audio": self.audio, "target": settings.target,
                "target_met": meets_target(self.audio, settings.target),
                "trigger": trigger, "saved_at": int(time.time())}


async def consume_stream(resp, chunks, first: bytes, directory: str, info: dict,
                         provider: str, provider_id: str, settings: Settings) -> Download:
    if resp.status_code != 200 or resp.headers.get("content-range"):
        raise ValueError("whole download requires HTTP 200, no Range")
    if resp.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("encoded stream refused")
    if any(x in resp.headers.get("content-type", "").lower() for x in ("text/", "json", "video/")):
        raise ValueError("non-audio response")
    expected = resp.headers.get("content-length", "")
    expected = int(expected) if expected.isdigit() else None
    cap = settings.max_mb * 1024 * 1024
    if expected is not None and expected > cap:
        raise ValueError("download exceeds size budget")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"fnmusic-download-{uuid4().hex}.part")
    success = False
    try:
        count = 0
        with open(path, "xb") as file:
            for chunk in (first,):
                if chunk:
                    count += len(chunk)
                    if count > cap:
                        raise ValueError("download exceeds size budget")
                    file.write(chunk)
            async for chunk in chunks:
                if chunk:
                    count += len(chunk)
                    if count > cap:
                        raise ValueError("download exceeds size budget")
                    file.write(chunk)
        if count < 1024 or (expected is not None and count != expected):
            raise ValueError("incomplete file")
        # Shield the worker so cancellation never unlinks a file still being validated.
        job = asyncio.create_task(asyncio.to_thread(inspect_audio, path))
        try:
            audio = await asyncio.shield(job)
        except asyncio.CancelledError:
            await job
            raise
        if not duration_matches(duration(info), float(audio["duration_s"])):
            raise ValueError("actual audio duration does not match track")
        success = True
        return Download(path, info, audio, provider, provider_id)
    finally:
        if not success:
            with suppress(FileNotFoundError):
                os.unlink(path)


async def control(socket_path: str, operation: str, lease: str = "") -> dict:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(socket_path), 5)
    try:
        writer.write(json.dumps({"op": operation, "lease": lease}).encode() + b"\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), 50)
        result = json.loads(raw)
        if not result.get("ok"):
            raise RuntimeError("fallback service unavailable")
        return result
    finally:
        writer.close()
        await writer.wait_closed()


@asynccontextmanager
async def standby(socket_path: str):
    lease = (await control(socket_path, "acquire"))["lease"]
    owner = asyncio.current_task()

    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(20)
                await control(socket_path, "renew", lease)
        except asyncio.CancelledError:
            raise
        except Exception:
            if owner:
                owner.cancel()

    task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(Exception):
            await asyncio.shield(control(socket_path, "release", lease))


async def fetch_candidate(client: httpx.AsyncClient, item: dict, directory: str, settings: Settings) -> Download:
    song_id = str(item["id"])
    detail = await client.get("/info", params={"id": song_id}, timeout=10)
    detail.raise_for_status()
    info = detail.json()
    if info.get("ok") is not True:
        raise ValueError("candidate metadata unavailable")
    if info.get("id") != song_id or info.get("source") != item.get("source"):
        raise ValueError("candidate identity changed")
    # The caller matches both the search snapshot and this refreshed metadata.
    if match_track(item, info):
        raise ValueError("candidate metadata changed")
    async with client.stream("GET", "/stream", params={"id": song_id, "proxy": "true"},
                             headers={"Accept-Encoding": "identity"}, timeout=60) as resp:
        chunks = resp.aiter_bytes()
        first = await anext(chunks, b"")
        return await consume_stream(resp, chunks, first, directory, info,
                                    str(info.get("source") or ""), song_id, settings)


async def choose_fallback(client: httpx.AsyncClient, reference: dict, directory: str,
                          settings: Settings, primary: Download | None = None) -> Download | None:
    """Try providers in configured order, at most three matching files per provider."""
    if not canonical(reference.get("title")) or not artists(reference.get("artist")) or not duration(reference):
        return primary if settings.allow_downgrade else None
    best = primary
    transferred = False
    try:
        for provider in settings.sources:
            try:
                response = await client.get("/search", params={
                    "keyword": f"{_text(reference['title'])} {_text(reference['artist'])}",
                    "sources": provider, "limit": 8}, timeout=15)
                response.raise_for_status()
                data = response.json()
                if data.get("ok") is not True:
                    continue
                matched = [i for i in data.get("items", []) if isinstance(i, dict)
                           and i.get("source") == provider
                           and str(i.get("id") or "").startswith(provider + ":")
                           and match_track(reference, i) is None][:3]
            except (httpx.HTTPError, ValueError, TypeError):
                continue
            for item in matched:
                candidate = None
                try:
                    candidate = await fetch_candidate(client, item, directory, settings)
                    if match_track(reference, candidate.info) or not duration_matches(
                            duration(reference), candidate.audio["duration_s"]):
                        continue
                    if meets_target(candidate.audio, settings.target):
                        if best and best is not primary:
                            best.discard()
                        transferred = True
                        return candidate
                    if best is None or quality_rank(candidate.audio) > quality_rank(best.audio):
                        if best and best is not primary:
                            best.discard()
                        best, candidate = candidate, None
                except (httpx.HTTPError, ValueError, RuntimeError, OSError, subprocess.SubprocessError):
                    pass
                finally:
                    if candidate and not transferred:
                        candidate.discard()
        if settings.allow_downgrade:
            transferred = True
            return best
        return None
    finally:
        if best and best is not primary and not transferred:
            best.discard()


def write_provenance(dest: str, record: dict) -> None:
    path = Path(dest + ".fnmusic-source.json")
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with open(temp, "x", encoding="utf-8") as file:
            json.dump(record, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        with suppress(FileNotFoundError):
            temp.unlink()
