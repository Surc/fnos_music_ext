"""musicdl HTTP 服务：把 musicdl 库包装成共享音源 API.

统一曲目 ID 契约: "<source>:<identifier>"，例如 "kuwo:228908"。
两个消费方（fnmusic-ext 代理、music-box）都以该 ID 串通信。
"""
import asyncio
import html
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

logger = logging.getLogger("musicdl_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("musicdl").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)  # CDN URLs may contain temporary tokens.

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False
    logger.warning("curl_cffi not installed, falling back to standard httpx client")

from musicdl import musicdl  # noqa: E402
from hardening import AdaptiveTimeout, SearchCache, SourceBreaker, SingleFlight, SourceBulkhead, SourceBusy, SearchProgress  # noqa: E402

# 复用仓库根（容器内 /srv）的 proxy.env_merge 解析 .env；独立运行无仓库结构时退回纯环境变量
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from proxy.env_merge import parse_env_file  # noqa: E402
except ImportError:
    parse_env_file = None


def _startup_sources() -> "tuple[list[str], str]":
    """解析 MUSICDL_SOURCES 白名单：.env > 进程环境变量 > 内置默认。

    WebUI 保存只写 bind mount 的 /repo/.env 再 supervisorctl restart；restart 继承的
    是容器启动时刻的 supervisord 环境，新白名单只可能出现在 .env 里，所以 .env
    必须优先于（可能陈旧的）环境变量。
    """
    raw = ""
    origin = ""
    if parse_env_file is not None:
        env_path = Path(os.environ.get("FNMUSIC_ENV_FILE", "/repo/.env"))
        try:
            kv, _ = parse_env_file(env_path)
            source_key = ("FNMUSIC_DOWNLOAD_FALLBACK_SOURCES" if os.environ.get("MUSICDL_FALLBACK_MODE") == "true"
                          else "MUSICDL_SOURCES")
            raw = (dict(kv).get(source_key) or "").strip()
        except Exception:
            raw = ""
        if raw:
            origin = f"env file {env_path}"
    if not raw:
        source_key = ("FNMUSIC_DOWNLOAD_FALLBACK_SOURCES" if os.environ.get("MUSICDL_FALLBACK_MODE") == "true"
                      else "MUSICDL_SOURCES")
        raw = os.environ.get(source_key, "").strip()
        if raw:
            origin = "process env"
    if not raw:
        raw = "KuwoMusicClient,MiguMusicClient"
        origin = "builtin default"
    sources = [s.strip() for s in raw.split(",") if s.strip()]
    return sources, origin


_CONF_SOURCES, _SOURCES_ORIGIN = _startup_sources()


def _startup_timeout(key: str, default: float) -> float:
    """Use current NAS .env on restart, rather than stale Supervisor exports."""
    raw = os.environ.get(key, str(default))
    if parse_env_file is not None:
        try:
            values, _ = parse_env_file(Path(os.environ.get("FNMUSIC_ENV_FILE", "/repo/.env")))
            raw = dict(values).get(key) or raw
        except OSError:
            pass
    try:
        value = float(raw)
        if math.isfinite(value) and value > 0:
            return max(0.1, value)
    except (TypeError, ValueError):
        pass
    logger.warning("Invalid %s; using default timeout %s", key, default)
    return default


CONF = {
    "sources": _CONF_SOURCES,
    "search_timeout": _startup_timeout("MUSICDL_SEARCH_TIMEOUT", 12),
    "limit_per_source": int(os.environ.get("MUSICDL_LIMIT_PER_SOURCE", "10")),
    "url_ttl": int(os.environ.get("MUSICDL_URL_TTL", "1800")),
    "cache_max": int(os.environ.get("MUSICDL_CACHE_MAX", "3000")),
    "work_dir": os.environ.get("MUSICDL_WORK_DIR", "/tmp/musicdl_outputs"),
    "search_cache_ttl": int(os.environ.get("MUSICDL_SEARCH_CACHE_TTL", "300")),
    "search_cache_max": int(os.environ.get("MUSICDL_SEARCH_CACHE_MAX", "200")),
    "breaker_threshold": int(os.environ.get("MUSICDL_BREAKER_THRESHOLD", "4")),
    "breaker_cooldown": int(os.environ.get("MUSICDL_BREAKER_COOLDOWN", "120")),
    # Network inactivity timeout is separate from the async response deadline.
    "request_timeout": _startup_timeout("MUSICDL_REQUEST_TIMEOUT", 5),
    # RID resolution includes several sequential requests, unlike metadata search.
    "resolve_timeout": _startup_timeout("MUSICDL_RESOLVE_TIMEOUT", 30),
    "neg_cache_ttl": int(os.environ.get("MUSICDL_NEG_TTL", "30")),
    # 自适应熔断/降级
    "slow_degrade_s": float(os.environ.get("MUSICDL_SLOW_DEGRADE_S", "8")),
    # 一页 10 首的上游搜索大约要 4 秒，再留出探活。压到 3 秒时搜索函数还没返回，
    # 进度里一首都没有，界面就是 0 条，并且会把下一次超时继续压在地板上。
    "adaptive_min_timeout": float(os.environ.get("MUSICDL_ADAPTIVE_MIN_TIMEOUT", "8")),
    "search_page_cap": max(1, int(os.environ.get("MUSICDL_SEARCH_PAGE_CAP", "10"))),
    "probe_timeout": max(0.2, float(os.environ.get("MUSICDL_PROBE_TIMEOUT", "2"))),
    "fast_return_items": int(os.environ.get("MUSICDL_FAST_RETURN_ITEMS", "12")),
    "slow_grace_s": float(os.environ.get("MUSICDL_SLOW_GRACE_S", "2")),
}

SEARCH_CACHE = SearchCache(
    ttl=CONF["search_cache_ttl"],
    max_entries=CONF["search_cache_max"],
)
SOURCE_BREAKER = SourceBreaker(
    failure_threshold=CONF["breaker_threshold"],
    cooldown=CONF["breaker_cooldown"],
    slow_threshold_s=CONF["slow_degrade_s"],
)
ADAPTIVE = AdaptiveTimeout(
    base_timeout=CONF["search_timeout"],
    min_timeout=CONF["adaptive_min_timeout"],
    slow_latency=CONF["slow_degrade_s"],
)
SINGLE_FLIGHT = SingleFlight()

# id -> {"item": {...}, "keyword": str, "download_headers": dict, "lyric": str, "ts": float}
_SONG_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_STATS = {"searches": 0, "errors": 0}


def _source_short(client_name: str) -> str:
    return (client_name or "").replace("MusicClient", "").lower()


def _source_mapping() -> dict:
    """Resolve names without guessing capitalization (HTQYY/FiveSing/MyFreeMP3)."""
    names = set(CONF["sources"])
    try:
        from musicdl.modules.sources import MusicClientBuilder
        names.update(MusicClientBuilder.REGISTERED_MODULES)
    except ImportError:
        names.update(getattr(musicdl, "SUPPORTED_MUSIC_SOURCES", []) or [])
    return {alias: name for name in sorted(names)
            for alias in (name.casefold(), _source_short(name).casefold())}


def _normalize_startup_sources(raw: list, registered: set | None = None) -> list:
    """MUSICDL_SOURCES 白名单启动归一：短名/别名 → 注册全名，未知项告警丢弃。

    库层 musicdl.MusicClient 只认全名；短名必须在服务入口解析。
    只对照注册表解析（不把白名单自身当别名），打错的平台名才会被丢弃。"""
    if registered is None:
        try:
            from musicdl.modules.sources import MusicClientBuilder
            registered = set(MusicClientBuilder.REGISTERED_MODULES)
        except ImportError:
            registered = set(getattr(musicdl, "SUPPORTED_MUSIC_SOURCES", []) or [])
    alias = {a: n for n in sorted(registered)
             for a in (n.casefold(), _source_short(n).casefold())}
    out: list = []
    for name in raw:
        resolved = alias.get(name.strip().casefold())
        if resolved is None:
            logger.warning("MUSICDL_SOURCES 忽略未知平台 %r（可用平台见 /sources）", name)
            continue
        if resolved not in out:
            out.append(resolved)
    return out or ["KuwoMusicClient", "MiguMusicClient"]


CONF["sources"] = _normalize_startup_sources(CONF["sources"])

SOURCE_NAMES = _source_mapping()
SOURCE_WORKERS = SourceBulkhead(SOURCE_NAMES.values())
# 直链探活共享池：探测跑在 worker 线程之外，单个源不会因为串行 HEAD
# 占住自己的单线程舱壁 N×probe_timeout 秒。生命周期跟随进程（不随
# lifespan 关闭），解释器退出时由 concurrent.futures 的 atexit 钩子回收。
_PROBE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="probe")


def _source_client(name: str) -> str:
    client = SOURCE_NAMES.get(name.strip().casefold())
    if client is None:
        raise HTTPException(400, f"unknown music source {name!r}; see /sources")
    return client


def _cache_put(item: dict, keyword: str, download_headers: dict, lyric: str):
    with _CACHE_LOCK:
        if len(_SONG_CACHE) >= CONF["cache_max"]:
            # 淘汰最旧的一半
            for k in sorted(_SONG_CACHE, key=lambda k: _SONG_CACHE[k]["ts"])[: len(_SONG_CACHE) // 2]:
                _SONG_CACHE.pop(k, None)
        _SONG_CACHE[item["id"]] = {
            "item": item,
            "keyword": keyword,
            "download_headers": download_headers or {},
            "lyric": lyric or "",
            "ts": time.time(),
        }


def _cache_get(song_id: str):
    with _CACHE_LOCK:
        return _SONG_CACHE.get(song_id)


def _search_one_source(source: str, keyword: str, limit: int) -> list:
    """单个源搜索（工作线程内执行，阻塞）。"""
    if source == "KuwoMusicClient":
        return _search_kuwo_metadata(keyword, limit)
    client = musicdl.MusicClient(
        music_sources=[source],
        init_music_clients_cfg={
            source: {
                # 库按这个数量翻页，并且每首都会先解析直链。30 条限额会变成 6 页，
                # 整次 search() 返回前一首都交不出来。封顶一页，够首屏，也放得进超时。
                "search_size_per_source": min(max(int(limit or 1), 1), int(CONF["search_page_cap"])),
                "work_dir": CONF["work_dir"],
                "max_retries": 1,
            },
        },
        clients_threadings={source: 1},
        requests_overrides={source: {"timeout": CONF["request_timeout"]}},
    )
    result = client.search(keyword=keyword)
    return list(result.values())[0] if result else []


def _search_kuwo_metadata(keyword: str, limit: int) -> list:
    """One metadata page, without musicdl's per-song URL parsing/filtering.

    Parameters follow HotDownloader's mobile search; all= is encoded once by
    httpx. Other sources retain their existing musicdl search contract.
    """
    size = min(max(int(limit or 1), 1), 30)
    params = {"client": "kt", "all": keyword, "ft": "music", "encoding": "utf8",
              "rformat": "json", "pcjson": "1", "itemset": "web_2013",
              "vipver": "1", "newver": "1", "mobi": "1", "pn": "0", "rn": size}
    with httpx.Client(timeout=CONF["request_timeout"], follow_redirects=True) as client:
        response = client.get("http://search.kuwo.cn/r.s", params=params,
                              headers={"User-Agent": "kwplayer_ar_9.2.2.1"})
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("abslist"), list):
        raise ValueError("Kuwo search response has no abslist")
    songs = []
    seen = set()
    for row in data["abslist"][:size]:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("MUSICRID") or "").removeprefix("MUSIC_")
        if not rid.isascii() or not rid.isdigit() or int(rid) <= 0 or rid in seen:
            continue
        title = html.unescape(str(row.get("SONGNAME") or row.get("NAME") or "")).strip()
        if not title or any(marker in title for marker in TRIAL_MARKERS):
            continue
        if (row.get("is_trial") or row.get("freeTrialInfo") or row.get("freeTrialPrivilege")
                or str(row.get("is_free_part") or row.get("ISFREEPART") or "0") != "0"
                or str(row.get("fail_process") or "0") == "4"):
            continue
        try:
            duration = float(row.get("DURATION") or 0)
            if not math.isfinite(duration) or duration < 0:
                duration = 0
        except (TypeError, ValueError):
            duration = 0
        seen.add(rid)
        songs.append(SimpleNamespace(
            source="KuwoMusicClient", identifier=rid, song_name=title,
            singers=html.unescape(str(row.get("ARTIST") or "")),
            album=html.unescape(str(row.get("ALBUM") or "")), duration_s=duration,
            cover_url=row.get("hts_MVPIC") or row.get("albumpic") or "",
            playback_status="unresolved",
        ))
    logger.info("Kuwo metadata search: raw=%d retained=%d requested=%d total=%s",
                len(data["abslist"]), len(songs), size, data.get("TOTAL", "unknown"))
    return songs


def _normalize(song, keyword: str) -> dict:
    src = _source_short(getattr(song, "source", "") or "")
    sid = str(getattr(song, "identifier", "") or "")
    item = {
        "id": f"{src}:{sid}",
        "source": src,
        "title": getattr(song, "song_name", "") or "",
        "artist": getattr(song, "singers", "") or "",
        "album": getattr(song, "album", "") or "",
        "duration_s": getattr(song, "duration_s", 0) or 0,
        "ext": getattr(song, "ext", "") or "mp3",
        "file_size": getattr(song, "file_size_bytes", 0) or 0,
        "cover_url": getattr(song, "cover_url", "") or "",
        # qq 曲目的专辑封面主键：封面兜底链用 albummid 直构 gtimg 图床 URL
        "albummid": str(getattr(song, "albummid", "") or getattr(song, "album_mid", "") or ""),
        "download_url": getattr(song, "download_url", "") or "",
    }
    if getattr(song, "playback_status", "") == "unresolved" and src == "kuwo":
        # A discovery candidate is not proof of a usable stream or its quality.
        item.pop("download_url")
        item.update(playback_status="unresolved", verified=False)
    return item


TRIAL_MARKERS = (
    "(试听)",
    "（试听）",
    "试听片段",
    "片段试听",
    "试听版",
    "[试听]",
    "【试听】",
    "- 试听",
    " - 试听",
)


def _is_candidate_playable(song, item: dict) -> bool:
    """初筛：检查 identifier、标题试听标记、download_url 存在且非错误页。"""
    sid = item.get("id") or ""
    if not sid or sid.endswith(":"):
        return False
    title = str(item.get("title") or "")
    if not title:
        return False
    if any(marker in title for marker in TRIAL_MARKERS):
        return False
    url = str(item.get("download_url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return False
    if "404/error.html" in url or "error.html" in url:
        return False
    return True


def _probe_playable_sync(url: str, headers: dict) -> bool:
    """探测直链是否有效且真实可播放（过滤无效/试听/404直链/HTML错误页）。"""
    if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    if "404/error.html" in url or "error.html" in url:
        return False
    hdrs = dict(headers or {})
    if HAS_CURL_CFFI:
        try:
            r = curl_requests.head(
                url,
                headers=hdrs,
                impersonate="chrome",
                allow_redirects=True,
                timeout=CONF["probe_timeout"],
            )
            if r.status_code == 405:
                hdrs["Range"] = "bytes=0-1"
                r = curl_requests.get(
                    url,
                    headers=hdrs,
                    impersonate="chrome",
                    allow_redirects=True,
                    stream=True,
                    timeout=CONF["probe_timeout"],
                )
            if r.status_code in (200, 206):
                ct = (r.headers.get("content-type") or "").lower()
                if "text/html" in ct:
                    return False
                return True
            return False
        except Exception:
            return False
    else:
        try:
            with httpx.Client(follow_redirects=True, timeout=CONF["probe_timeout"]) as cx:
                r = cx.head(url, headers=hdrs)
                if r.status_code == 405:
                    hdrs["Range"] = "bytes=0-1"
                    r = cx.get(url, headers=hdrs)
                if r.status_code in (200, 206):
                    ct = (r.headers.get("content-type") or "").lower()
                    if "text/html" in ct:
                        return False
                    return True
                return False
        except Exception:
            return False


_head_probe_sync = _probe_playable_sync


def _search_playable(source: str, keyword: str, fetch_size: int, limit: int,
                     song_id: str | None = None, deadline: float | None = None,
                     progress: SearchProgress | None = None) -> list:
    """Keep search and bounded probes inside the same source admission slot.

    Return metadata only; a timed-out worker must not update shared caches.
    Probes fan out on the shared pool; confirmed entries are handed to
    `progress` immediately with library rank. Both the bounded snapshot and
    the return value select the same earliest confirmed candidates.
    """
    songs = _search_one_source(source, keyword, fetch_size)
    candidates = []
    for song in songs[:fetch_size]:
        if not song:
            continue
        item = _normalize(song, keyword)
        if song_id is not None and item["id"] != song_id:
            continue
        if item.get("playback_status") == "unresolved" and song_id is None:
            if (progress is not None and progress.stopped()) or (
                    deadline is not None and time.monotonic() >= deadline):
                if progress is not None:
                    progress.partial = True
                break
            candidates.append((item, {}, ""))
            if progress is not None:
                progress.append(candidates[-1], rank=len(candidates) - 1)
            continue
        if not _is_candidate_playable(song, item):
            continue
        headers = getattr(song, "default_download_headers", {}) or {}
        lyric = str(getattr(song, "lyric", "") or "")
        candidates.append((item, headers, lyric))
    if not candidates:
        return []
    if all(item.get("playback_status") == "unresolved" for item, _, _ in candidates):
        return candidates[:limit]
    if (progress is not None and progress.stopped()) or (
            deadline is not None and time.monotonic() >= deadline):
        if progress is not None:
            progress.partial = True
        return []

    valid = set()
    completed = set()
    futures = {}
    for idx, (item, headers, _) in enumerate(candidates):
        futures[_PROBE_POOL.submit(_probe_playable_sync, item["download_url"], headers)] = idx
    try:
        wait_budget = None if deadline is None else max(0.1, deadline - time.monotonic())
        for fut in as_completed(futures, timeout=wait_budget):
            idx = futures[fut]
            completed.add(idx)
            if fut.result():
                valid.add(idx)
                if progress is not None:
                    progress.append(candidates[idx], rank=idx)
            if len(valid) >= limit:
                # A later fast probe must not displace an earlier valid song.
                # Stop once every candidate up to the nth valid result is known.
                cutoff = sorted(valid)[limit - 1]
                if all(i in completed for i in range(cutoff + 1)):
                    break
    except FuturesTimeoutError:
        if progress is not None:
            progress.partial = True
    finally:
        for fut in futures:
            fut.cancel()

    return [entry for idx, entry in enumerate(candidates) if idx in valid][:limit]


async def _refresh_by_keyword(song_id: str) -> dict | None:
    """URL 过期或下载失败后按缓存的关键词重搜一次，找回同 ID 的曲目。"""
    entry = _cache_get(song_id)
    if not entry or not entry.get("keyword"):
        return None
    try:
        src_client = _source_client(entry["item"].get("source", ""))
        limit = max(CONF["limit_per_source"], 10)
        timeout = ADAPTIVE.timeout_for(src_client)
        if src_client == "KuwoMusicClient" and "playback_status" in entry["item"]:
            timeout = CONF["resolve_timeout"]
            # Resolve exactly this RID, even if it is now outside the first page.
            pending = dict(entry["item"])
            pending.pop("download_url", None)
            pending.update(playback_status="unresolved", verified=False, file_size=0)
            _cache_put(pending, entry["keyword"], {}, entry.get("lyric", ""))
            resolved = await SOURCE_WORKERS.run(
                src_client, _resolve_kuwo_entry_sync, entry, timeout=timeout)
            entries = [resolved] if resolved else []
        else:
            entries = await SOURCE_WORKERS.run(
                src_client, _search_playable, src_client, entry["keyword"], limit, 1, song_id,
                time.monotonic() + timeout, timeout=timeout,
            )
    except Exception as exc:
        logger.warning("Refresh failed for %s: %s", song_id, type(exc).__name__)
        return None
    for item, headers, lyric in entries:
        _cache_put(item, entry["keyword"], headers, lyric)
        return _cache_get(song_id)
    return None


class _AudioSample(io.BytesIO):
    """Use origin length for CBR MP3 duration without downloading the full file."""

    def __init__(self, data: bytes, full_size: int):
        super().__init__(data)
        self.full_size = full_size

    def seek(self, offset, whence=0):
        if whence == 2:
            return super().seek(self.full_size + offset)
        return super().seek(offset, whence)


def _kuwo_audio_matches_sync(url: str, headers: dict, ext: str, expected_s: float) -> bool:
    """Check audio headers and actual duration; HTTP 200 alone proves nothing.

    FLAC STREAMINFO and MP3 Xing/VBRI carry duration. CBR MP3 uses the origin's
    full length, never the range sample length. Unknown duration fails closed.
    This checks a header sample, not the integrity of the entire recording;
    lossless decoding and full-download validation retain their existing gates.
    """
    if not math.isfinite(expected_s) or expected_s <= 0 or ext not in ("flac", "mp3"):
        return False
    response = client = None
    sample_size = 256 * 1024
    hdrs = {k: v for k, v in headers.items() if k.lower() not in ("range", "accept-encoding")}
    hdrs.update({"Range": f"bytes=0-{sample_size - 1}", "Accept-Encoding": "identity"})
    started = time.monotonic()
    stage = "audio_open"
    try:
        if HAS_CURL_CFFI:
            response = curl_requests.get(url, headers=hdrs, impersonate="chrome",
                                         allow_redirects=True, stream=True,
                                         timeout=CONF["request_timeout"])
            chunks = response.iter_content(16 * 1024)
        else:
            client = httpx.Client(timeout=CONF["request_timeout"], follow_redirects=True)
            response = client.send(client.build_request("GET", url, headers=hdrs), stream=True)
            chunks = response.iter_bytes(16 * 1024)
        content_type = response.headers.get("content-type", "").lower()
        if (response.status_code not in (200, 206)
                or any(x in content_type for x in ("text/", "json"))
                or response.headers.get("content-encoding", "identity").lower() != "identity"):
            logger.warning("Kuwo audio gate: stage=audio_http status=%s outcome=rejected",
                           response.status_code)
            return False
        if response.status_code == 206:
            match = re.fullmatch(r"bytes 0-\d+/(\d+)", response.headers.get("content-range", ""))
            if not match:
                logger.warning("Kuwo audio gate: stage=audio_range outcome=invalid_range")
                return False
            full_size = int(match[1])
        else:
            full_size = int(response.headers.get("content-length") or 0)
        stage = "audio_read"
        data = bytearray()
        for chunk in chunks:
            data.extend(chunk[:sample_size - len(data)])
            if len(data) >= sample_size:
                break
        stage = "audio_header"
        if ext == "flac":
            from mutagen.flac import FLAC
            actual_s = FLAC(io.BytesIO(data)).info.length
        else:
            from mutagen.mp3 import MPEGInfo
            audio = MPEGInfo(_AudioSample(bytes(data), full_size))
            if audio.sketchy:
                logger.warning("Kuwo audio gate: stage=audio_header outcome=sketchy_mp3")
                return False
            actual_s = audio.length
        matches = (math.isfinite(actual_s) and actual_s > 0
                   and abs(actual_s - expected_s) <= max(3.0, expected_s * 0.02))
        logger.info("Kuwo audio gate: stage=audio_duration outcome=%s ext=%s expected_s=%.3f actual_s=%.3f origin_bytes=%d elapsed_s=%.3f",
                    "accepted" if matches else "mismatch", ext, expected_s, actual_s,
                    full_size, time.monotonic() - started)
        return matches
    except Exception as exc:
        logger.warning("Kuwo audio gate: stage=%s outcome=%s elapsed_s=%.3f",
                       stage, type(exc).__name__, time.monotonic() - started)
        return False
    finally:
        if response is not None:
            response.close()
        if client is not None:
            client.close()


def _resolve_kuwo_entry_sync(entry: dict) -> tuple | None:
    """Reuse musicdl's official plain-audio resolver for one cached Kuwo RID.

    No keyword search, third-party resolver, credential injection or encrypted
    format support is added. Its internal API is covered by the real-dependency
    offline production contract. Timeout overrides only reach the official path
    which does not also supply timeout=, avoiding duplicate keyword arguments.
    """
    from musicdl.modules.sources.kuwo import KuwoMusicClient
    expected = entry["item"]
    song_id = expected["id"]
    started = time.monotonic()
    deadline = started + CONF["resolve_timeout"]

    def request_budget():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Kuwo resolution deadline exceeded")
        return min(CONF["request_timeout"], remaining)

    class PlaybackClient(KuwoMusicClient):
        def get(self, url, **kwargs):
            if urlsplit(url).hostname == "newlyric.kuwo.cn":
                # The parser fetches optional lyrics AFTER obtaining valid audio.
                # Do not spend the playback deadline on an unrelated endpoint.
                return None
            logger.info("Kuwo playback: id=%s stage=rid_request outcome=start", song_id)
            kwargs["timeout"] = request_budget()
            response = super().get(url, **kwargs)
            logger.info("Kuwo playback: id=%s stage=rid_response status=%s has_url=%s elapsed_s=%.3f",
                        song_id, getattr(response, "status_code", None),
                        bool(re.search(r'http[^\s$\"]+', getattr(response, "text", ""))),
                        time.monotonic() - started)
            return response

    # musicdl's default logger writes raw request URLs/exceptions to its own file
    # even with disable_print=True. Only our stage summaries may be persisted.
    def safe_library_error(message, **kwargs):
        outcome = "transport_timeout" if re.search(r"timeout|timed out", str(message), re.I) else "transport_error"
        logger.warning("Kuwo playback: id=%s stage=rid_request outcome=%s", song_id, outcome)

    quiet_log = SimpleNamespace(error=safe_library_error,
                               **{level: lambda *a, **kw: None
                                  for level in ("info", "warning", "debug")})
    client = PlaybackClient(work_dir=CONF["work_dir"], max_retries=1, disable_print=True,
                            logger_handle=quiet_log, maintain_session=True)
    tester = getattr(client, "audio_link_tester", None)
    verified_audio = set()
    if tester is not None:
        original_request = tester.request
        original_test = tester.test

        def checked_request(method, *args, **kwargs):
            stage = "link_" + method.lower()
            logger.info("Kuwo playback: id=%s stage=%s outcome=start", song_id, stage)
            try:
                kwargs["request_overrides"] = {**(kwargs.get("request_overrides") or {}),
                                               "timeout": request_budget()}
                result = original_request(method, *args, **kwargs)
                logger.info("Kuwo playback: id=%s stage=%s status=%s elapsed_s=%.3f",
                            song_id, stage, result.get("status_code"), time.monotonic() - started)
                return result
            except Exception as exc:
                logger.warning("Kuwo playback: id=%s stage=%s outcome=%s elapsed_s=%.3f",
                               song_id, stage, type(exc).__name__, time.monotonic() - started)
                raise
        tester.request = checked_request

        def checked_test(url, **kwargs):
            result = dict(original_test(url=url, **kwargs))
            if result.get("ok"):
                audio_url = result.get("download_url") or url
                ext = result.get("ext")
                logger.info("Kuwo playback: id=%s stage=audio_validate ext=%s", song_id, ext)
                result["ok"] = _kuwo_audio_matches_sync(
                    audio_url, client.default_download_headers, ext,
                    float(expected.get("duration_s") or 0))
                if result["ok"]:
                    verified_audio.add((audio_url, ext))
            # Reject a short/bad FLAC BEFORE musicdl stops trying quality tiers;
            # a complete MP3 for the same RID may still be available.
            return result
        tester.test = checked_test
    raw = {"MUSICRID": "MUSIC_" + expected["id"].split(":", 1)[1],
           "SONGNAME": expected["title"], "ARTIST": expected.get("artist", ""),
           "ALBUM": expected.get("album", ""), "DURATION": expected.get("duration_s", 0)}
    try:
        logger.info("Kuwo playback: id=%s stage=rid_resolve outcome=start request_timeout_s=%s resolve_timeout_s=%s",
                    song_id, CONF["request_timeout"], CONF["resolve_timeout"])
        song = client._parsewithofficialapiv1(
            search_result=raw, request_overrides={"timeout": CONF["request_timeout"]})
        item = _normalize(song, entry["keyword"])
        if item["id"] != expected["id"] or not _is_candidate_playable(song, item):
            logger.warning("Kuwo playback: id=%s stage=rid_resolve outcome=no_candidate elapsed_s=%.3f",
                           song_id, time.monotonic() - started)
            return None
        headers = getattr(song, "default_download_headers", {}) or client.default_download_headers
        if ((item["download_url"], item["ext"]) not in verified_audio
                and not _kuwo_audio_matches_sync(item["download_url"], headers, item["ext"],
                                                float(expected.get("duration_s") or 0))):
            return None
        if time.monotonic() >= deadline:
            return None
        item.update(playback_status="verified", verified=True)
        logger.info("Kuwo playback: id=%s stage=verified elapsed_s=%.3f",
                    song_id, time.monotonic() - started)
        # Retain original discovery identity/cover rather than inferred quality.
        for key in ("title", "artist", "album", "duration_s", "cover_url"):
            if expected.get(key):
                item[key] = expected[key]
        return item, dict(headers), str(getattr(song, "lyric", "") or entry.get("lyric", ""))
    finally:
        for session in (getattr(client, "session", None),
                        getattr(getattr(client, "audio_link_tester", None), "session", None),
                        getattr(getattr(client, "quark_audio_link_tester", None), "session", None)):
            if session is not None:
                session.close()


# --- 损坏流防护（2026-09-29 实测）-----------------------------------------
# kuwo 官方 CDN 的无损档开始交付不可解码字节（文件头仍是合法 fLaC、大小与
# Content-Length 一致，musicdl 库只验 URL/扩展名发现不了），代理侧会把这种
# 流原样落进曲库，官方转码器编到坏点即报"transcoding error"。取流前对无损
# 档做头部解码探针：坏流自动降级到 kuwo 官方 convert_url2 的 mp3 档（实测
# 干净、无需登录）；上层也可以用 quality=mp3 主动要求有损档。
_PROBE_EXTS = {"flac", "wav", "ape", "m4a", "alac", "ogg", "opus"}
_PROBE_BYTES = 3 * 1024 * 1024
_PROBE_MIN_DECODED_S = 1.0
_PROBE_CACHE: dict[str, tuple[float, bool]] = {}
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_TTL = int(os.environ.get("MUSICDL_PROBE_TTL", "600"))


def _decode_probe_ok(path: str) -> bool:
    """ffmpeg 解码探针样本文件，以实际解出的时长判断好坏。

    截断的完好样本也能解出数十秒（探针只取头部字节）；从头就坏的样本
    time≈0。报错文本不可用作判据——截断与损坏的报错完全相同。
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
        if not times:
            return False
        h, mnt, s = times[-1]
        return int(h) * 3600 + int(mnt) * 60 + float(s) >= _PROBE_MIN_DECODED_S
    except Exception:
        return False


def _head_probe_sync(url: str, headers: dict) -> bool:
    probe_headers = {k: v for k, v in headers.items() if k.lower() != "range"}
    probe_headers["Range"] = f"bytes=0-{_PROBE_BYTES - 1}"
    tmp_path = ""
    try:
        if HAS_CURL_CFFI:
            resp = curl_requests.get(url, headers=probe_headers, impersonate="chrome",
                                     stream=True, timeout=(10, 60))
        else:
            client = httpx.Client(follow_redirects=True, timeout=30)
            resp = client.build_request("GET", url, headers=probe_headers)
            resp = client.send(resp, stream=True)
        buf = b""
        for chunk in resp.iter_content(256 * 1024):
            buf += chunk
            if len(buf) >= _PROBE_BYTES:
                break
        if hasattr(resp, "close"):
            resp.close()
        if not buf:
            return False
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".probe", delete=False) as f:
            f.write(buf)
            tmp_path = f.name
        return _decode_probe_ok(tmp_path)
    except Exception:
        return False
    finally:
        if tmp_path:
            with suppress(Exception):
                os.unlink(tmp_path)


async def _head_probe(url: str, headers: dict, song_id: str) -> bool:
    with _PROBE_CACHE_LOCK:
        cached = _PROBE_CACHE.get(song_id)
    if cached and (time.time() - cached[0]) < _PROBE_TTL:
        return cached[1]
    ok = await asyncio.to_thread(_head_probe_sync, url, headers)
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[song_id] = (time.time(), ok)
    if not ok:
        logger.warning("stream head probe FAILED for %s (%s) — likely corrupt upstream bytes", song_id, url[:120])
    return ok


def _kuwo_force_mp3_url_sync(song_id: str) -> "tuple[str, dict] | None":
    """kuwo 官方 convert_url2 直取 320k mp3 档（无需登录，实测干净）。"""
    try:
        from musicdl.modules.sources.kuwo import KuwoMusicClientUtils
    except Exception:
        return None
    rid = song_id.split(":", 1)[1] if ":" in song_id else song_id
    query = (f"user=0&corp=kuwo&source=kwplayer_ar_5.1.0.0_B_jiakong_vh.apk"
             f"&p2p=1&type=convert_url2&sig=0&format=mp3&rid={rid}")
    try:
        enc = KuwoMusicClientUtils.encryptquery(query)
        if HAS_CURL_CFFI:
            resp = curl_requests.get(f"http://mobi.kuwo.cn/mobi.s?f=kuwo&q={enc}",
                                     headers={"User-Agent": "okhttp/3.10.0"}, timeout=10)
        else:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"http://mobi.kuwo.cn/mobi.s?f=kuwo&q={enc}",
                                  headers={"User-Agent": "okhttp/3.10.0"})
        m = re.search(r'http[^\s$\"]+', resp.text or "")
        if not m:
            return None
        return m.group(0), {"User-Agent": "okhttp/3.10.0"}
    except Exception as e:
        logger.warning("kuwo force-mp3 resolve failed for %s: %s", song_id, type(e).__name__)
        return None


async def _maybe_downgrade(id: str, entry: dict, url: str, quality: str,
                           range_header: "str | None") -> "tuple[str, dict]":
    """无损档坏流探测与 mp3 降级；返回最终 (url, 源站请求头)。"""
    ext = str(entry["item"].get("ext") or "").lower()
    want_mp3 = str(quality or "").strip().lower() == "mp3"
    if not want_mp3 and ext in _PROBE_EXTS:
        probe_headers = dict(entry.get("download_headers") or {})
        if not await _head_probe(url, probe_headers, id):
            logger.warning("lossless stream corrupt for %s (ext=%s), downgrading to mp3", id, ext)
            want_mp3 = True
    if not want_mp3:
        src_headers = dict(entry.get("download_headers") or {})
    else:
        forced = None
        if str(id).startswith("kuwo:"):
            forced = await asyncio.to_thread(_kuwo_force_mp3_url_sync, id)
            if forced:
                logger.warning("stream for %s downgraded to official mp3 tier", id)
        if forced:
            url, base_headers = forced
        else:
            base_headers = dict(entry.get("download_headers") or {})
        src_headers = base_headers
    if "playback_status" in entry["item"] and url != entry["item"].get("download_url"):
        if not await asyncio.to_thread(_kuwo_audio_matches_sync, url, src_headers, "mp3",
                                       float(entry["item"].get("duration_s") or 0)):
            raise HTTPException(502, f"replacement audio failed duration/stream validation for {id}")
    if range_header:
        src_headers["Range"] = range_header
    return url, src_headers


async def _resolve_entry(song_id: str, auto_refresh: bool = True):
    entry = _cache_get(song_id)
    if entry is None:
        raise HTTPException(404, f"unknown song id {song_id!r}: cache expired or service restarted; re-search keyword and source once, then retry")
    fresh = entry["item"].get("download_url") and (time.time() - entry["ts"]) < CONF["url_ttl"]
    if fresh:
        return entry
    # URL 可能仍有效，探测一下
    if entry["item"].get("download_url") and "playback_status" not in entry["item"]:
        head_headers = dict(entry.get("download_headers") or {})
        valid = await asyncio.to_thread(_head_probe_sync, entry["item"]["download_url"], head_headers)
        if valid:
            entry["ts"] = time.time()
            return entry
    if auto_refresh:
        refreshed = await _refresh_by_keyword(song_id)
        if refreshed:
            return refreshed
    raise HTTPException(502, f"playable URL expired for {song_id!r}; bounded refresh failed, re-search keyword and source")


def _fetch_upstream_stream_sync(url: str, headers: dict):
    """通过 curl_cffi 同步流式请求上游音频源（模拟 Chrome TLS 指纹）。"""
    return curl_requests.get(
        url,
        headers=headers,
        impersonate="chrome",
        stream=True,
        timeout=(10, 60),
    )


class _HttpxStreamWrapper:
    """包装 httpx 响应以对齐 curl_cffi 响应接口。"""
    def __init__(self, resp, client):
        self._resp = resp
        self._client = client
        self.status_code = resp.status_code
        self.headers = resp.headers

    def iter_content(self, chunk_size=64 * 1024):
        return self._resp.iter_bytes(chunk_size)

    def close(self):
        try:
            self._resp.close()
        finally:
            self._client.close()


async def _fetch_upstream_stream(url: str, src_headers: dict):
    """请求源站流。优先走 curl_cffi 工作线程，fallback 走 httpx。"""
    src_headers = {k: v for k, v in src_headers.items() if k.lower() != "accept-encoding"}
    src_headers["Accept-Encoding"] = "identity"
    if HAS_CURL_CFFI:
        resp = await asyncio.to_thread(_fetch_upstream_stream_sync, url, src_headers)
    else:
        def _fetch_httpx_sync():
            client = httpx.Client(follow_redirects=True, timeout=30)
            try:
                req = client.build_request("GET", url, headers=src_headers)
                response = client.send(req, stream=True)
                return _HttpxStreamWrapper(response, client)
            except BaseException:
                client.close()
                raise
        resp = await asyncio.to_thread(_fetch_httpx_sync)
    encoding = resp.headers.get("Content-Encoding") or resp.headers.get("content-encoding") or "identity"
    if encoding.strip().lower() != "identity":
        resp.close()
        raise HTTPException(502, "source ignored identity encoding; refusing decoded bytes with encoded length/range")
    return resp


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时打印一次配置摘要
    logger.info("=== musicdl-service configuration ===")
    for k, v in CONF.items():
        logger.info("  %s = %s", k, v)
    logger.info("  MUSICDL_SOURCES origin = %s", _SOURCES_ORIGIN)
    logger.info("  HAS_CURL_CFFI = %s", HAS_CURL_CFFI)
    logger.info("=====================================")

    app.state.http = httpx.AsyncClient(follow_redirects=True, timeout=30)
    try:
        yield
    finally:
        await app.state.http.aclose()
        SOURCE_WORKERS.shutdown(wait=False)


app = FastAPI(title="musicdl-service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "sources": CONF["sources"],
        "cache_size": len(_SONG_CACHE),
        "cache_entries": len(SEARCH_CACHE),
        "breaker_open": SOURCE_BREAKER.get_open_sources(),
        "adaptive_timeouts": ADAPTIVE.stats(),
        "timeouts": {k: CONF[k] for k in ("request_timeout", "search_timeout", "resolve_timeout")},
        "stats": _STATS,
    }


@app.get("/sources")
async def sources():
    registered: list[str] = []
    try:
        from musicdl.modules.sources import MusicClientBuilder

        registered = sorted(MusicClientBuilder.REGISTERED_MODULES.keys())
    except Exception as exc:
        logger.warning("Failed to list registered musicdl sources: %s", exc)
        try:
            from musicdl import musicdl as _mdl

            registered = sorted(getattr(_mdl, "SUPPORTED_MUSIC_SOURCES", []) or [])
        except Exception:
            registered = list(CONF["sources"])

    return {
        "enabled": CONF["sources"],
        "registered": registered,
    }


@app.get("/search")
async def search(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(None, ge=1, le=30),
    sources: str = Query("", description="逗号分隔的源名(短名或全名)，空=用默认白名单"),
):
    if limit is None:
        limit = CONF["limit_per_source"]

    raw_src_list = CONF["sources"]
    if sources:
        raw_src_list = [_source_client(s) for s in sources.split(",") if s.strip()]
    raw_src_list = list(dict.fromkeys(raw_src_list))
    sources_key = ",".join(sorted(raw_src_list))
    cache_key = f"{sources_key}|limit={limit}"
    _STATS["searches"] += 1

    async def _do_search() -> dict:
        # 1. 过滤熔断中的源
        active_src_list = [s for s in raw_src_list if not SOURCE_BREAKER.is_open(s)]

        # 2. 查缓存（命中直接返回）
        cached = SEARCH_CACHE.get(keyword, cache_key)
        # Search results alone cannot serve playback after song-cache eviction.
        if cached is not None and all(_cache_get(item["id"]) for item in cached):
            return {
                "ok": True,
                "cached": True,
                "keyword": keyword,
                "items": cached,
                "errors": {},
            }

        # 全被熔断且无缓存，直接返回空结果（不报错）
        if not active_src_list:
            return {
                "ok": True,
                "cached": False,
                "keyword": keyword,
                "items": [],
                "errors": {s: "circuit breaker open" for s in raw_src_list},
            }

        progress_by_source = {}

        def collect(source):
            progress = progress_by_source.get(source)
            items = []
            for item, headers, lyric in progress.finish() if progress else []:
                _cache_put(item, keyword, headers, lyric)
                items.append(item)
            return items

        async def one(source: str):
            timeout = ADAPTIVE.timeout_for(source)
            started = time.monotonic()
            progress = SearchProgress(limit, started + timeout)
            progress_by_source[source] = progress
            fetch_size = max(limit * 2, 10)
            try:
                await SOURCE_WORKERS.run(
                    source, _search_playable, source, keyword, fetch_size, limit,
                    None, started + timeout - min(0.1, timeout * 0.1), progress,
                    timeout=timeout,
                )
                latency = time.monotonic() - started
                items = collect(source)
                if progress.partial:
                    # 已经交出歌就不要再把超时往下压。压到搜不完一页后，下次还是 0 条。
                    if not items:
                        ADAPTIVE.record_failure(source)
                    return source, items, "partial results (probe deadline reached)"
                ADAPTIVE.record_success(source, latency)
                SOURCE_BREAKER.record_success(source, latency)
                return source, items, None
            except SourceBusy as exc:
                # Admission rejection is not another upstream failure.
                return source, collect(source), str(exc)
            except asyncio.TimeoutError:
                items = collect(source)
                if not items:
                    SOURCE_BREAKER.record_failure(source)
                    ADAPTIVE.record_failure(source)
                suffix = " (partial results)" if items else ""
                return source, items, f"timeout after {timeout:.1f}s{suffix}"
            except asyncio.CancelledError:
                # The aggregate owner collects this closed snapshot on its
                # global/grace deadline, never the still-running worker.
                progress.finish()
                raise
            except Exception as e:  # 单源失败不影响其他源
                _STATS["errors"] += 1
                SOURCE_BREAKER.record_failure(source)
                return source, collect(source), f"{type(e).__name__}: {e}"

        results: list = []
        pending = {asyncio.create_task(one(s), name=s): s for s in active_src_list}
        overall = max(float(CONF["search_timeout"]), 4.0)
        overall_deadline = time.monotonic() + overall
        deadline = overall_deadline
        skipped_fast_return = False
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, still = await asyncio.wait(
                pending.keys(), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                pending.pop(task, None)
                try:
                    results.append(task.result())
                except Exception as e:  # noqa: BLE001
                    _STATS["errors"] += 1
                    results.append((task.get_name(), [], f"{type(e).__name__}: {e}"))
            if not pending:
                break
            total_items = sum(len(items) for _, items, _ in results)
            # 结果已足够优质：不再等待慢源，立即返回
            if total_items >= CONF["fast_return_items"]:
                skipped_fast_return = True
                break
            # 已有任意源出结果：再给其余源最多 slow_grace_s 秒，避免慢源拖死整页
            if total_items > 0:
                deadline = min(deadline, time.monotonic() + CONF["slow_grace_s"])
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            grace_return = deadline < overall_deadline
            skip_reason = ("skipped (fast return)" if skipped_fast_return else
                           "skipped (slow-source grace reached)" if grace_return else
                           f"timeout after {overall}s")
            for task, src in pending.items():
                items = collect(src)
                results.append((src, items, skip_reason + (" (partial results)" if items else "")))
                # 没等到歌才收紧。已经有结果、或因为别的源够了而提前返回，都不是这个源坏了。
                if not items and not skipped_fast_return and not grace_return:
                    ADAPTIVE.record_failure(src)
                    SOURCE_BREAKER.record_failure(src)

        all_items = []
        seen_ids = set()
        for _, items, _ in results:
            for item in items:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    all_items.append(item)

        errors = {src: err for src, _, err in results if err}
        for s in raw_src_list:
            if s not in active_src_list:
                errors.setdefault(s, "circuit breaker open")
        # Partial/error results must remain retryable, not become a long-lived
        # apparently complete cache hit with errors silently removed.
        if not errors:
            if all_items:
                SEARCH_CACHE.put(keyword, cache_key, all_items)
            else:
                SEARCH_CACHE.put(keyword, cache_key, [], ttl=CONF["neg_cache_ttl"])

        return {
            "ok": True,
            "cached": False,
            "keyword": keyword,
            "items": all_items,
            "errors": errors,
        }

    return await SINGLE_FLIGHT.run((keyword, cache_key), _do_search)


@app.get("/info")
async def info(id: str = Query(..., min_length=1)):
    entry = _cache_get(id)
    if entry is None:
        raise HTTPException(404, f"song id {id!r} not found in cache: cache expired or service restarted; re-search keyword and source once, then retry")
    item = entry["item"]
    return {
        "ok": True,
        "id": item.get("id"),
        "source": item.get("source"),
        "title": item.get("title"),
        "artist": item.get("artist"),
        "album": item.get("album"),
        "duration_s": item.get("duration_s"),
        "ext": item.get("ext"),
        "file_size": item.get("file_size"),
        "cover_url": item.get("cover_url"),
        "lyric": entry.get("lyric", ""),
        **({"playback_status": item["playback_status"], "verified": item.get("verified") is True}
           if "playback_status" in item else {}),
    }


@app.get("/stream")
async def stream(
    id: str = Query(...),
    proxy: bool = Query(False, description="true=字节流透传而非302"),
    quality: str = Query("", description="mp3=跳过无损档直接取有损直链（上游无损流损坏时上层降级用）"),
    range_header: str | None = Header(None, alias="Range", description="客户端请求头中的 Range"),
):
    entry = await _resolve_entry(id)
    url = entry["item"].get("download_url")
    if not url:
        raise HTTPException(502, f"no playable url for {id}")
    url, src_headers = await _maybe_downgrade(id, entry, url, quality, range_header)
    if not proxy:
        return RedirectResponse(url, status_code=302)

    try:
        resp = await _fetch_upstream_stream(url, src_headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Stream request failed for %s, attempting refresh: %s", id, type(e).__name__)
        refreshed_entry = await _refresh_by_keyword(id)
        if refreshed_entry and refreshed_entry["item"].get("download_url"):
            entry = refreshed_entry
            url = entry["item"]["download_url"]
            url, src_headers = await _maybe_downgrade(id, entry, url, quality, range_header)
            resp = await _fetch_upstream_stream(url, src_headers)
        else:
            raise HTTPException(502, f"failed to fetch stream from source for {id}: {type(e).__name__}")

    # 若源站返回 4xx/5xx，尝试刷新一次
    if resp.status_code >= 400:
        if hasattr(resp, "close"):
            resp.close()
        logger.warning("Source returned %s for %s, attempting refresh...", resp.status_code, id)
        refreshed_entry = await _refresh_by_keyword(id)
        if refreshed_entry and refreshed_entry["item"].get("download_url") and refreshed_entry["item"]["download_url"] != url:
            entry = refreshed_entry
            url = entry["item"]["download_url"]
            url, src_headers = await _maybe_downgrade(id, entry, url, quality, range_header)
            resp = await _fetch_upstream_stream(url, src_headers)
            if resp.status_code >= 400:
                status = resp.status_code
                if hasattr(resp, "close"):
                    resp.close()
                raise HTTPException(502, f"source returned {status} for {id} even after refresh")
        else:
            raise HTTPException(502, f"source returned {resp.status_code} for {id}")

    content_type = resp.headers.get("content-type", "").lower()
    if resp.status_code not in (200, 206) or any(x in content_type for x in ("text/", "json")):
        resp.close()
        raise HTTPException(502, f"source did not return audio for {id}")

    out_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for k in ("Content-Type", "Content-Length", "Content-Range"):
        val = resp.headers.get(k) or resp.headers.get(k.lower())
        if val:
            out_headers[k] = val

    def gen():
        try:
            for chunk in resp.iter_content(64 * 1024):
                if chunk:
                    yield chunk
        finally:
            if hasattr(resp, "close"):
                resp.close()

    return StreamingResponse(
        gen(),
        status_code=206 if "Content-Range" in out_headers or resp.status_code == 206 else 200,
        headers=out_headers,
    )
