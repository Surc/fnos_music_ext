"""Download identity, real audio validation, process leases and caller isolation."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
from contextlib import asynccontextmanager

import httpx
import pytest

from proxy import app as proxy
from proxy import download_fallback as df
from proxy.fallback_control import LeaseManager

REF = {"title": "晴天", "artist": "周杰伦", "album": "叶惠美", "duration_s": 240}


def track(provider="kuwo", **updates):
    return {**REF, "id": provider + ":123", "source": provider, **updates}


def audio(lossless=True, **updates):
    return {"ext": "flac" if lossless else "mp3", "codec": "flac" if lossless else "mp3",
            "lossless_encoding": lossless, "bitrate": 0 if lossless else 320000,
            "duration_s": 240, "complete_decode_checked": True, **updates}


@pytest.mark.parametrize("changes,reason", [
    ({"title": "晴天 Live"}, "title"), ({"title": "晴天（试听）"}, "preview"),
    ({"artist": "其他歌手"}, "artist"), ({"artist": "周杰伦 / 其他歌手"}, "artist"),
    ({"album": "Live 演唱会"}, "version"), ({"album": "2020 Remastered"}, "version"),
    ({"duration_s": 30}, "duration"), ({"duration_s": 0}, "duration"),
    ({"duration_s": float("nan")}, "duration"), ({"duration_s": 244}, "duration"),
    ({"is_trial": True}, "preview"), ({"preview": True}, "preview"),
    ({"transcoded_from_lossy": True}, "transcoded"),
], ids=["live", "preview_title", "cover_artist", "extra_artist", "live_album", "remaster",
        "preview_duration", "unknown_duration", "nan_duration", "wrong_duration", "trial", "preview", "transcoded"])
def test_reject_wrong_identity_or_partial_version(changes, reason):
    assert df.match_track(REF, track(**changes)) == reason


def test_match_normalizes_punctuation_requires_all_artists_and_versions():
    ref = {**REF, "title": "A Song!", "artist": "A / B"}
    assert df.match_track(ref, {**ref, "title": "Ａ Ｓｏｎｇ", "artist": "B & A"}) is None
    assert df.match_track(ref, {**ref, "artist": "A"}) == "artist"
    assert df.match_track({**REF, "artist": ""}, track()) == "artist"
    assert df.match_track({**REF, "title": "晴天 (Live)"}, track(title="晴天 (Live)", album="演唱会")) is None


def test_quality_uses_audio_codec_and_audio_bitrate_not_file_suffix():
    assert df.meets_target(audio(), "lossless")
    assert not df.meets_target(audio(False), "lossless")
    assert df.meets_target(audio(False), "320k")
    assert not df.meets_target(audio(False, bitrate=128000), "320k")
    assert not df.meets_target(audio(False, bitrate=0), "320k")


@pytest.fixture
def encoded_audio(tmp_path):
    encoder = shutil.which("ffmpeg")
    if not encoder:
        pytest.skip("audio tools unavailable locally; CI installs ffmpeg")
    paths = {}
    for name, opts in (("flac", ["-c:a", "flac"]), ("mp3", ["-c:a", "libmp3lame", "-b:a", "320k"])):
        path = tmp_path / ("tone." + name)
        subprocess.run([encoder, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        *opts, str(path)], check=True, capture_output=True, timeout=10)
        paths[name] = path
    return paths


def test_inspect_decodes_actual_bytes_with_wrong_extension(encoded_audio, tmp_path):
    path = tmp_path / "mislabelled.mp3"
    path.write_bytes(encoded_audio["flac"].read_bytes())
    value = df.inspect_audio(str(path))
    assert value["codec"] == "flac" and value["ext"] == "flac"
    assert value["complete_decode_checked"] and not value["original_lossless_verified"]
    assert df.meets_target(df.inspect_audio(str(encoded_audio["mp3"])), "320k")


def test_corrupt_audio_and_missing_validator_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "fake.flac"
    path.write_bytes(b"fLaC" + b"broken" * 400)
    with pytest.raises((ValueError, subprocess.SubprocessError)):
        df.inspect_audio(str(path))
    monkeypatch.setattr(df.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="requires"):
        df.inspect_audio(str(path))


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,status,payload", [
    ({"content-length": "2048"}, 200, b"a" * 1024),
    ({"content-type": "application/json"}, 200, b"a" * 2048),
    ({"content-range": "bytes 0-2047/4096"}, 206, b"a" * 2048),
    ({"content-encoding": "gzip"}, 200, b"a" * 2048),
    ({"content-length": str(151 * 1024 * 1024)}, 200, b"a" * 2048),
], ids=["incomplete", "json", "range", "gzip", "oversize"])
async def test_stream_integrity_rejections_leave_no_partial_file(tmp_path, headers, status, payload):
    response = httpx.Response(status, content=payload)
    response.headers.update(headers)
    chunks = response.aiter_bytes()
    with pytest.raises(ValueError):
        await df.consume_stream(response, chunks, await anext(chunks, b""), str(tmp_path),
                                REF, "kuwo", "kuwo:123", df.Settings())
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_actual_preview_duration_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(df, "inspect_audio", lambda _: audio(duration_s=30))
    response = httpx.Response(200, content=b"a" * 4096)
    chunks = response.aiter_bytes()
    with pytest.raises(ValueError, match="duration"):
        await df.consume_stream(response, chunks, await anext(chunks, b""), str(tmp_path),
                                REF, "kuwo", "kuwo:123", df.Settings())
    assert not list(tmp_path.glob("*.part"))


def transport_handler(calls, lossless=True):
    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/search":
            provider = request.url.params["sources"]
            items = [track(title="晴天 (Live)")] if provider == "kuwo" and lossless else [track(provider)]
            return httpx.Response(200, json={"ok": True, "items": items})
        if request.url.path == "/info":
            provider = request.url.params["id"].split(":")[0]
            return httpx.Response(200, json={"ok": True, **track(provider)})
        return httpx.Response(200, content=b"a" * 4096)
    return handler


@pytest.mark.asyncio
async def test_provider_order_matching_and_actual_source_record(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(df, "inspect_audio", lambda _: audio())
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_handler(calls)), base_url="http://standby") as client:
        result = await df.choose_fallback(client, REF, str(tmp_path), df.Settings())
    assert result and result.provider == "migu"
    assert [p["sources"] for route, p in calls if route == "/search"] == ["kuwo", "migu"]
    assert [p["id"] for route, p in calls if route == "/stream"] == ["migu:123"]
    marker = result.provenance("online:netease:1", df.Settings(), "netease_unavailable")
    df.write_provenance(result.path, marker)
    assert marker["provider_track_id"] == "migu:123" and marker["target_met"]
    assert "url" not in json.dumps(marker).lower() and "cookie" not in json.dumps(marker).lower()
    result.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [False, True])
async def test_strict_or_downgrade_policy(tmp_path, monkeypatch, allow):
    monkeypatch.setattr(df, "inspect_audio", lambda _: audio(False))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_handler([], False)), base_url="http://standby") as client:
        result = await df.choose_fallback(client, REF, str(tmp_path), df.Settings(("kuwo",), allow_downgrade=allow))
    assert bool(result) == allow
    if result:
        assert not result.provenance("online:netease:1", df.Settings(), "below_target")["target_met"]
        result.discard()
    assert not list(tmp_path.glob("*.part"))


@pytest.fixture
def active_fallback(monkeypatch, tmp_path):
    for key, value in (("ENABLED", "true"), ("TARGET", "lossless"), ("ALLOW_DOWNGRADE", "false")):
        monkeypatch.setenv(df.ENV_PREFIX + key, value)
    for key, value in (("netease_enabled", True), ("musicdl_enabled", False), ("lx_enabled", False),
                       ("auto_cover", False), ("lyric_auto_dl", False),
                       ("cache_dir", str(tmp_path / "cache")), ("tee_save_dir", str(tmp_path / "library"))):
        monkeypatch.setitem(proxy.CONF, key, value)
    monkeypatch.setattr(proxy, "_fallback_download_jobs", {})
    monkeypatch.setattr(proxy, "_fallback_download_slots", None)
    monkeypatch.setattr(proxy, "_full_fetch_tasks", {})
    monkeypatch.setattr(proxy, "_full_fetch_failed", {})
    async def reference(*args, **kwargs):
        return dict(REF)
    monkeypatch.setattr(proxy, "_download_reference", reference)
    monkeypatch.setattr(proxy, "_auto_lyric_after_finalize", reference)
    monkeypatch.setattr(proxy, "_schedule_library_scan", lambda _: None)
    monkeypatch.setattr(proxy, "_dispatch_official_binding", lambda *_: None)
    return tmp_path


@pytest.mark.asyncio
async def test_netease_meets_target_without_starting_standby(active_fallback, monkeypatch):
    async def primary(*_):
        path = active_fallback / "primary.part"
        path.write_bytes(b"a" * 4096)
        return df.Download(str(path), REF, audio(), "netease", "1")
    monkeypatch.setattr(proxy, "_download_netease_candidate", primary)
    @asynccontextmanager
    async def forbidden(*_):
        pytest.fail("NetEase already meets target")
        yield
    monkeypatch.setattr(df, "standby", forbidden)
    result = await proxy._full_fetch_download("online:netease:1", {"cookie": "user-a"})
    assert result and os.path.isfile(result["dest"])
    with open(result["dest"] + ".fnmusic-source.json", encoding="utf-8") as file:
        marker = json.load(file)
    assert marker["provider"] == "netease" and marker["provider_track_id"] == "1"


@pytest.mark.asyncio
async def test_unavailable_netease_uses_backup_keeps_original_identity(active_fallback, monkeypatch):
    async def unavailable(*_):
        return None
    monkeypatch.setattr(proxy, "_download_netease_candidate", unavailable)
    @asynccontextmanager
    async def standby(*_):
        yield
    monkeypatch.setattr(df, "standby", standby)
    async def backup(*_):
        path = active_fallback / "fallback.part"
        path.write_bytes(b"b" * 4096)
        return df.Download(str(path), track("migu"), audio(), "migu", "migu:123")
    monkeypatch.setattr(df, "choose_fallback", backup)
    result = await proxy._full_fetch_download("online:netease:1", {"cookie": "user-a"})
    assert result and proxy.find_cache_file("online:netease:1") == result["dest"]
    with open(result["dest"] + ".fnmusic-source.json", encoding="utf-8") as file:
        marker = json.load(file)
    assert marker["provider"] == "migu" and marker["original_guid"] == "online:netease:1"
    assert "user-a" not in json.dumps(marker)


@pytest.mark.asyncio
async def test_concurrent_downloads_share_bytes_but_bind_each_caller(active_fallback, monkeypatch):
    ready = asyncio.Event()
    calls, bindings = [], []
    async def download(*args):
        calls.append(args[0])
        await ready.wait()
        return {"dest": "shared.flac"}
    monkeypatch.setattr(proxy, "_download_with_fallback", download)
    monkeypatch.setattr(proxy, "_dispatch_official_binding", lambda guid, headers, meta: bindings.append(headers))
    jobs = [asyncio.create_task(proxy._full_fetch_download("online:netease:1", {"cookie": user}))
            for user in ("a", "b")]
    await asyncio.sleep(0)
    ready.set()
    await asyncio.gather(*jobs)
    assert calls == ["online:netease:1"]
    assert bindings == [{"cookie": "a"}, {"cookie": "b"}]


@pytest.mark.asyncio
async def test_configuration_change_or_stop_cancels_work(active_fallback, monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    async def waiting(*_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(proxy, "_download_with_fallback", waiting)
    caller = asyncio.create_task(proxy._full_fetch_download("online:netease:1", {}))
    await started.wait()
    await proxy._cancel_fallback_downloads()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert stopped.is_set() and not proxy._fallback_download_jobs


@pytest.mark.asyncio
async def test_strict_download_never_delivers_old_lower_quality_file(active_fallback, monkeypatch):
    old = active_fallback / "old.mp3"
    old.write_bytes(b"old")
    monkeypatch.setattr(proxy, "find_cache_file", lambda _: str(old))
    async def failed(*_):
        return None
    monkeypatch.setattr(proxy, "_full_fetch_download", failed)
    task = {"guid": "online:netease:1", "quality": "original"}
    await proxy._dl_produce(task, {})
    assert task["state"] == "failed" and old.read_bytes() == b"old"


async def fake_manager(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FNMUSIC_NETEASE_ENABLED=true\nFNMUSIC_DOWNLOAD_FALLBACK_ENABLED=true\n")
    manager = LeaseManager(str(env), str(tmp_path / "control.sock"))
    calls, state = [], {"running": False}
    async def ctl(op):
        calls.append(op)
        if op == "status":
            return state["running"]
        state["running"] = op == "start"
        return True
    async def healthy():
        return True
    monkeypatch.setattr(manager, "ctl", ctl)
    monkeypatch.setattr(manager, "healthy", healthy)
    return manager, env, calls, state


@pytest.mark.asyncio
async def test_leases_share_process_expire_and_stop_on_switch(tmp_path, monkeypatch):
    manager, env, calls, state = await fake_manager(tmp_path, monkeypatch)
    first = await manager.handle("acquire", "")
    second = await manager.handle("acquire", "")
    assert first["ok"] and second["ok"] and calls.count("start") == 1
    assert (await manager.handle("renew", first["lease"]))["ok"]
    manager.leases = {key: -1 for key in manager.leases}
    manager.idle_s = 0
    await manager.reap()
    assert not manager.started and calls[-1] == "stop"
    assert not (await manager.handle("renew", first["lease"]))["ok"]
    await manager.handle("acquire", "")
    env.write_text("FNMUSIC_LX_ENABLED=true\nFNMUSIC_DOWNLOAD_FALLBACK_ENABLED=true\n")
    await manager.reap()
    assert not state["running"] and not manager.leases
    assert not (await manager.handle("acquire", ""))["ok"]


@pytest.mark.asyncio
async def test_real_private_socket_lease_and_shutdown(tmp_path, monkeypatch):
    try:
        test_socket = socket.socket(socket.AF_UNIX)
        test_socket.close()
    except PermissionError:
        pytest.skip("local sandbox denies sockets; GitHub Linux CI exercises this")
    manager, env, calls, state = await fake_manager(tmp_path, monkeypatch)
    stop_event = asyncio.Event()
    job = asyncio.create_task(manager.run(stop_event))
    try:
        for _ in range(50):
            if manager.socket_path.exists():
                break
            await asyncio.sleep(0.01)
        assert manager.socket_path.stat().st_mode & 0o777 == 0o600
        lease = (await df.control(str(manager.socket_path), "acquire"))["lease"]
        assert state["running"]
        await df.control(str(manager.socket_path), "release", lease)
    finally:
        stop_event.set()
        await job
    assert not manager.socket_path.exists() and not state["running"]


def test_new_exact_reference_prefers_upgraded_file_keeps_legacy_refs(active_fallback):
    guid = "online:netease:42"
    low, high = active_fallback / "song.mp3", active_fallback / "song.flac"
    low.write_bytes(b"low")
    high.write_bytes(b"high")
    proxy.remember_media_path(guid, str(low))
    assert proxy.find_cache_file(guid) == str(low)
    proxy.remember_media_path(guid, str(high), exact=True)
    assert proxy.find_cache_file(guid) == str(high)
    assert proxy.recalled_media_stem(guid) == str(active_fallback / "song")


@pytest.mark.asyncio
async def test_all_sources_fail_respects_primary_downgrade_without_deleting_existing(tmp_path):
    path = tmp_path / "old.mp3"
    path.write_bytes(b"old")
    primary = df.Download(str(path), REF, audio(False), "unknown_existing", "", owned=False)
    def failed(_):
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(failed), base_url="http://standby") as client:
        assert await df.choose_fallback(client, REF, str(tmp_path), df.Settings(), primary) is None
        result = await df.choose_fallback(client, REF, str(tmp_path),
                                          df.Settings(allow_downgrade=True), primary)
    assert result is primary and path.read_bytes() == b"old"


@pytest.mark.asyncio
async def test_low_netease_quality_triggers_backup(active_fallback, monkeypatch):
    calls = []
    async def primary(*_):
        path = active_fallback / "low.part"
        path.write_bytes(b"low" * 1400)
        return df.Download(str(path), REF, audio(False), "netease", "1")
    monkeypatch.setattr(proxy, "_download_netease_candidate", primary)
    @asynccontextmanager
    async def standby(*_):
        calls.append("start")
        yield
        calls.append("release")
    monkeypatch.setattr(df, "standby", standby)
    async def backup(*args):
        path = active_fallback / "high.part"
        path.write_bytes(b"high" * 1100)
        return df.Download(str(path), track("migu"), audio(), "migu", "migu:123")
    monkeypatch.setattr(df, "choose_fallback", backup)
    result = await proxy._full_fetch_download("online:netease:1", {})
    assert result and calls == ["start", "release"]
    with open(result["dest"] + ".fnmusic-source.json", encoding="utf-8") as file:
        marker = json.load(file)
    assert marker["trigger"] == "below_target" and marker["audio"]["lossless_encoding"]
    assert not list(active_fallback.glob("*.part"))


@pytest.mark.asyncio
async def test_failed_fallback_has_cooldown(active_fallback, monkeypatch):
    calls = []
    async def failing(guid, headers, settings):
        calls.append(guid)
        proxy._full_fetch_failed[guid] = __import__("time").monotonic()
        return None
    monkeypatch.setattr(proxy, "_download_with_fallback", failing)
    assert await proxy._full_fetch_download("online:netease:1", {}) is None
    assert await proxy._full_fetch_download("online:netease:1", {}) is None
    assert len(calls) == 1
    proxy._full_fetch_failed["online:qq:2"] = __import__("time").monotonic()
    await proxy._cancel_fallback_downloads(reset_cooldown=True)
    assert "online:qq:2" in proxy._full_fetch_failed
    assert await proxy._full_fetch_download("online:netease:1", {}) is None
    assert len(calls) == 2, "a changed policy must be allowed a new attempt"
