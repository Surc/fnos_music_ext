"""Deterministic Kuwo discovery/playback regressions; no real network in pytest."""
import subprocess
import sys
import types

import httpx
import pytest
from fastapi.testclient import TestClient

from test_app_search import app_module, clean_state, _FakeSong, ffmpeg_path

KEYWORD = "DAYBREAK'S BELL"
RID = "142276943"


def row(rid=RID, **overrides):
    return {"MUSICRID": "MUSIC_" + rid, "SONGNAME": KEYWORD,
            "ARTIST": "L'Arc〜en〜Ciel", "ALBUM": "デイブレイクズベル",
            "DURATION": "252", **overrides}


def mock_search(monkeypatch, rows, *, status=200, payload=None):
    requests = []
    original = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=payload if payload is not None else {
            "TOTAL": "72", "abslist": rows})

    monkeypatch.setattr(app_module.httpx, "Client", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    return requests


def discover(monkeypatch):
    mock_search(monkeypatch, [row()])
    song = app_module._search_one_source("KuwoMusicClient", KEYWORD, 10)[0]
    item = app_module._normalize(song, KEYWORD)
    app_module._cache_put(item, KEYWORD, {}, "")
    return item


def mock_resolver(monkeypatch, *, url="http://audio.invalid/full.mp3", title=KEYWORD,
                  sid=RID, valid=True):
    calls = []
    closed = []
    song = _FakeSong(sid, "KuwoMusicClient")
    song.song_name, song.download_url = title, url
    song.duration_s = 252

    class Client:
        default_download_headers = {"User-Agent": "fixture"}
        session = types.SimpleNamespace(close=lambda: closed.append(True))

        def __init__(self, **kwargs):
            calls.append(kwargs)

        def _parsewithofficialapiv1(self, **kwargs):
            calls.append(kwargs)
            return song

    module = types.ModuleType("musicdl.modules.sources.kuwo")
    module.KuwoMusicClient = Client
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(app_module, "_kuwo_audio_matches_sync", lambda *args: valid)
    return calls, closed


def test_metadata_keeps_target_beyond_old_cap_without_resolving_urls(clean_state, monkeypatch):
    rows = [row(str(100 + i), SONGNAME=f"other {i}") for i in range(14)] + [row()]
    requests = mock_search(monkeypatch, rows)
    monkeypatch.setattr(app_module.musicdl, "MusicClient", lambda **kw: pytest.fail("metadata used musicdl"))
    monkeypatch.setattr(app_module, "_probe_playable_sync", lambda *a: pytest.fail("search probed URL"))
    with TestClient(app_module.app) as client:
        response = client.get("/search", params={"keyword": KEYWORD, "sources": "kuwo", "limit": 30})
        data = response.json()
        target = next(item for item in data["items"] if item["id"] == "kuwo:" + RID)
        assert target["playback_status"] == "unresolved" and target["verified"] is False
        assert "download_url" not in target
        assert data["errors"] == {}
        info = client.get("/info", params={"id": target["id"]}).json()
        assert info["title"] == KEYWORD and info["verified"] is False
        assert client.get("/search", params={"keyword": KEYWORD, "sources": "kuwo", "limit": 30}).json()["cached"]
    assert len(requests) == 1
    assert requests[0].url.host == "search.kuwo.cn"
    assert requests[0].url.params["all"] == KEYWORD
    assert requests[0].url.params["pn"] == "0"
    assert requests[0].url.params["rn"] == "30"
    assert requests[0].headers["User-Agent"] == "kwplayer_ar_9.2.2.1"


def test_metadata_filters_invalid_ids_duplicates_and_trial_fields(clean_state, monkeypatch):
    rows = [row(), row(), row("0"), row("bad"), row("２"), row("2", SONGNAME=""),
            row("3", SONGNAME=KEYWORD + " (试听版)"), row("4", is_trial=True),
            row("5", ISFREEPART=1), row("6", freeTrialInfo={"start": 0}),
            row("7", fail_process=4), row("8", DURATION="nan")]
    mock_search(monkeypatch, rows)
    songs = app_module._search_one_source("KuwoMusicClient", KEYWORD, 30)
    assert [song.identifier for song in songs] == [RID, "8"]
    assert songs[1].duration_s == 0


@pytest.mark.parametrize("status,payload", [(503, {}), (200, {}), (200, {"abslist": None})])
def test_search_transport_and_schema_failure_remain_retryable(clean_state, monkeypatch, status, payload):
    requests = mock_search(monkeypatch, [], status=status, payload=payload)
    with TestClient(app_module.app) as client:
        for _ in range(2):
            data = client.get("/search", params={"keyword": KEYWORD, "sources": "kuwo"}).json()
            assert not data["items"] and "KuwoMusicClient" in data["errors"]
            assert data["cached"] is False
    assert len(requests) == 2 and len(app_module.SEARCH_CACHE) == 0


def test_valid_empty_metadata_search_resets_previous_source_failures(clean_state, monkeypatch):
    for _ in range(app_module.CONF["breaker_threshold"] - 1):
        app_module.SOURCE_BREAKER.record_failure("KuwoMusicClient")
    mock_search(monkeypatch, [])
    with TestClient(app_module.app) as client:
        data = client.get("/search", params={"keyword": KEYWORD, "sources": "kuwo"}).json()
    assert data["items"] == [] and data["errors"] == {}
    app_module.SOURCE_BREAKER.record_failure("KuwoMusicClient")
    assert not app_module.SOURCE_BREAKER.is_open("KuwoMusicClient")


def test_aggregate_grace_does_not_trip_healthy_source_breaker(clean_state, monkeypatch):
    import threading
    monkeypatch.setitem(app_module.CONF, "slow_grace_s", 0.02)
    monkeypatch.setitem(app_module.CONF, "fast_return_items", 99)
    release = threading.Event()
    def search(source, *args):
        if source == "MiguMusicClient":
            assert release.wait(2)
        return [_FakeSong(1, source)]
    monkeypatch.setattr(app_module, "_search_one_source", search)
    for _ in range(app_module.CONF["breaker_threshold"] - 1):
        app_module.SOURCE_BREAKER.record_failure("MiguMusicClient")
    try:
        with TestClient(app_module.app) as client:
            data = client.get("/search", params={"keyword": KEYWORD, "sources": "kuwo,migu"}).json()
            assert "grace" in data["errors"]["MiguMusicClient"]
            assert not app_module.SOURCE_BREAKER.is_open("MiguMusicClient")
            assert "MiguMusicClient" not in app_module.ADAPTIVE.stats()
    finally:
        release.set()


def test_play_resolves_same_id_without_research_and_marks_only_verified_result(clean_state, monkeypatch):
    item = discover(monkeypatch)
    calls, closed = mock_resolver(monkeypatch)
    monkeypatch.setattr(app_module, "_search_one_source", lambda *a: pytest.fail("play re-searched keyword"))
    with TestClient(app_module.app) as client:
        response = client.get("/stream", params={"id": item["id"]}, follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"].endswith("full.mp3")
        info = client.get("/info", params={"id": item["id"]}).json()
        assert info["verified"] is True and info["playback_status"] == "verified"
        assert client.get("/stream", params={"id": item["id"]}, follow_redirects=False).status_code == 302
    assert len(calls) == 2  # constructor + one RID parse, next stream uses checked URL
    assert calls[1]["search_result"]["MUSICRID"] == "MUSIC_" + RID
    assert calls[1]["request_overrides"] == {"timeout": app_module.CONF["request_timeout"]}
    assert closed


@pytest.mark.parametrize("changes", [
    {"url": ""}, {"url": "http://audio.invalid/404/error.html"},
    {"title": KEYWORD + " 试听片段"}, {"sid": "999"}, {"valid": False},
])
def test_unresolved_bad_link_trial_wrong_id_or_duration_never_redirects(clean_state, monkeypatch, changes):
    item = discover(monkeypatch)
    mock_resolver(monkeypatch, **changes)
    with TestClient(app_module.app) as client:
        response = client.get("/stream", params={"id": item["id"]}, follow_redirects=False)
        assert response.status_code == 502 and "location" not in response.headers
        assert client.get("/info", params={"id": item["id"]}).json()["verified"] is False
    assert "download_url" not in app_module._cache_get(item["id"])["item"]
    assert not app_module.SOURCE_BREAKER.is_open("KuwoMusicClient")


def test_resolver_timeout_cannot_publish_late_playable_cache(clean_state, monkeypatch):
    import threading
    from hardening import AdaptiveTimeout
    item = discover(monkeypatch)
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(app_module, "ADAPTIVE", AdaptiveTimeout(0.05, 0.05))

    def resolve(entry):
        started.set()
        assert release.wait(2)
        return {**item, "verified": True, "download_url": "http://audio.invalid/late"}, {}, ""

    monkeypatch.setattr(app_module, "_resolve_kuwo_entry_sync", resolve)
    try:
        with TestClient(app_module.app) as client:
            response = client.get("/stream", params={"id": item["id"]}, follow_redirects=False)
            assert started.is_set() and response.status_code == 502
            release.set()
            app_module.SOURCE_WORKERS.shutdown(wait=True)
            assert app_module._cache_get(item["id"])["item"]["verified"] is False
    finally:
        release.set()


def test_expired_verified_kuwo_url_rechecks_duration_and_clears_claim_on_failure(clean_state, monkeypatch):
    item = discover(monkeypatch)
    mock_resolver(monkeypatch)
    with TestClient(app_module.app) as client:
        assert client.get("/stream", params={"id": item["id"]}, follow_redirects=False).status_code == 302
        app_module._cache_get(item["id"])["ts"] = 0
        monkeypatch.setattr(app_module, "_head_probe_sync", lambda *a: pytest.fail("decode-only probe cannot confirm full duration"))
        monkeypatch.setattr(app_module, "_kuwo_audio_matches_sync", lambda *a: False)
        response = client.get("/stream", params={"id": item["id"]}, follow_redirects=False)
        assert response.status_code == 502
        assert client.get("/info", params={"id": item["id"]}).json()["verified"] is False
    assert "download_url" not in app_module._cache_get(item["id"])["item"]


@pytest.mark.skipif(ffmpeg_path is None, reason="ffmpeg required for real audio header fixtures")
@pytest.mark.parametrize("ext,xing", [("flac", None), ("mp3", True), ("mp3", False)])
def test_actual_duration_rejects_preview_and_uses_full_origin_size(tmp_path, monkeypatch, ext, xing):
    path = tmp_path / ("fixture." + ext)
    command = [ffmpeg_path, "-v", "error", "-f", "lavfi", "-i", "sine=duration=12", "-c:a",
               "flac" if ext == "flac" else "libmp3lame"]
    if ext == "mp3":
        command += ["-b:a", "320k", "-write_xing", "1" if xing else "0"]
    subprocess.run(command + [str(path)], check=True, timeout=20)
    full = path.read_bytes()
    sample = full[:32 * 1024]
    closed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield sample

        def close(self):
            closed.append(True)

    def handler(request):
        assert request.headers["Range"].startswith("bytes=0-")
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(206, headers={"Content-Type": "audio/" + ext,
            "Content-Range": f"bytes 0-{len(sample)-1}/{len(full)}"}, stream=Body())

    original = httpx.Client
    monkeypatch.setattr(app_module, "HAS_CURL_CFFI", False)
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert app_module._kuwo_audio_matches_sync("http://audio.invalid/full", {}, ext, 12)
    assert not app_module._kuwo_audio_matches_sync("http://audio.invalid/preview", {}, ext, 252)
    assert not app_module._kuwo_audio_matches_sync("http://audio.invalid/unknown", {}, ext, 0)
    assert len(closed) == 2


@pytest.mark.parametrize("use_curl", [False, True])
@pytest.mark.parametrize("status,headers,body", [
    (200, {"content-type": "text/html"}, b"error page"),
    (200, {"content-type": "application/json"}, b"{}"),
    (404, {}, b"missing"), (200, {"content-encoding": "gzip"}, b"encoded"),
    (206, {"content-range": "bytes 20-30/40"}, b"wrong offset"),
    (200, {"content-type": "audio/mpeg", "content-length": "100"}, b"corrupt"),
])
def test_audio_gate_rejects_error_pages_encoded_or_corrupt_audio(monkeypatch, use_curl, status, headers, body):
    closed = []
    monkeypatch.setattr(app_module, "HAS_CURL_CFFI", use_curl)
    if use_curl:
        response = types.SimpleNamespace(status_code=status, headers=headers,
            iter_content=lambda size: iter([body]), close=lambda: closed.append(True))
        monkeypatch.setattr(app_module, "curl_requests", types.SimpleNamespace(get=lambda *a, **kw: response))
    else:
        original = httpx.Client
        def handler(request):
            return httpx.Response(status, headers=headers, content=body)
        monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert not app_module._kuwo_audio_matches_sync("http://audio.invalid/test", {}, "mp3", 252)
    if use_curl:
        assert closed


@pytest.mark.anyio
async def test_lossless_replacement_preview_is_rejected(clean_state, monkeypatch):
    entry = {"item": {"ext": "flac", "download_url": "http://audio.invalid/full.flac",
             "duration_s": 252, "playback_status": "verified"}, "download_headers": {}}
    async def bad_lossless(*args):
        return False
    monkeypatch.setattr(app_module, "_head_probe", bad_lossless)
    monkeypatch.setattr(app_module, "_kuwo_force_mp3_url_sync", lambda *a: ("http://audio.invalid/preview.mp3", {}))
    monkeypatch.setattr(app_module, "_kuwo_audio_matches_sync", lambda *a: False)
    with pytest.raises(app_module.HTTPException) as error:
        await app_module._maybe_downgrade("kuwo:" + RID, entry, entry["item"]["download_url"], "", None)
    assert error.value.status_code == 502
