"""Native playlist contracts, account changes and stale-cache recovery."""
import asyncio
import time

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from proxy import discovery as d
from proxy import app as p


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch):
    await d.shutdown()
    monkeypatch.setenv("FNMUSIC_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("FNMUSIC_DISCOVERY_DIR", str(tmp_path / "discovery_cache"))
    monkeypatch.setenv("FNMUSIC_NETEASE_CHANNELS", d.DEFAULT_CHANNELS)
    monkeypatch.setenv("FNMUSIC_HOME", str(tmp_path))
    monkeypatch.setitem(p.CONF, "netease_enabled", True)
    monkeypatch.setitem(p.CONF, "netease_my_playlists", False)
    monkeypatch.setitem(p.CONF, "recommend_daily", False)
    monkeypatch.setitem(p.CONF, "recommend_hot", False)
    monkeypatch.setitem(p.CONF, "plt_dir", str(tmp_path / "playlist_tracks"))
    monkeypatch.setitem(p.CONF, "fav_dir", str(tmp_path / "online_favorites"))
    monkeypatch.setitem(p.CONF, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(p, "_REGISTRY_WARMED", False)
    yield
    await d.shutdown()


def sources():
    state = {"uid": 99, "fail": False, "calls": []}
    songs = [{"song_id": 101, "name": "候选一", "artist": "甲", "duration_ms": 120000},
             {"song_id": 102, "name": "候选二", "artist": "乙", "duration_ms": 180000}]

    def handler(request):
        path = request.url.path
        state["calls"].append(path)
        if path == "/api/v1/discovery/account":
            return httpx.Response(200, json={"ok": True, "logged_in": bool(state["uid"]), "account_uid": state["uid"]})
        if state["fail"]:
            return httpx.Response(503)
        rows = {
            "/api/v1/playlists/user": [{"playlist_id": 1, "name": "自建", "track_count": 2},
                                       {"playlist_id": 2, "name": "他人的歌单", "subscribed": True}],
            "/api/v1/playlists/recommend": [{"playlist_id": 3, "name": "推荐", "cover_url": "http://img/3.jpg"}],
            "/api/v1/playlists/toplists": [{"playlist_id": 4, "name": "热歌"}],
            "/api/v1/playlists/category": [{"playlist_id": 5, "name": "怀旧"}],
            "/api/v1/playlists/newalbums": [{"album_id": 6, "name": "新专辑"}],
        }
        data = rows.get(path, songs if path.endswith("/tracks") or path == "/api/v1/radio/fm" else [])
        return httpx.Response(200, json={"ok": True, "data": data, "account_uid": state["uid"]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://musicbox"), state


def official(authed=True):
    def handler(request):
        if request.url.path.endswith("/user/me"):
            return httpx.Response(200, json={"code": 0 if authed else 1001,
                                           "data": {"guid": "fn-user"} if authed else None})
        if request.url.path.endswith("/playlist/list"):
            return httpx.Response(200, json={"code": 0, "data": {"list": [{"guid": "native", "name": "本地"}], "total": 1}})
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://unix")


def test_native_list_tracks_detail_batch_and_readonly():
    mb, state = sources()
    p.app.state.musicbox_client = mb
    p.app.state.upstream_client = official()
    with TestClient(p.app) as client:
        response = client.get("/music/api/v1/playlist/list").json()["data"]
        cards = response["list"]
        assert response["total"] == 8
        assert cards[-1]["guid"] == "native"
        assert any(c["name"] == "网易云·收藏他人的歌单" for c in cards)
        assert any(c["guid"] == d.FM_PREFIX + "99" for c in cards)
        guid = d.PLAYLIST_PREFIX + "3"
        detail = client.get("/music/api/v1/playlist/detail", params={"guid": guid}).json()
        assert detail["data"]["name"] == "推荐｜推荐"
        assert detail["data"]["createdAt"] == next(c["createdAt"] for c in cards if c["guid"] == guid)
        batch = client.get("/music/api/v1/playlist/batch-detail", params={"guids": guid + ",native"}).json()
        assert any(c["guid"] == guid for c in batch["data"]["list"])
        page = client.get("/music/api/v1/track/playlist-detail/list", params={"playlistGUID": guid, "page": 2, "size": 1}).json()
        assert page["data"]["total"] == 2
        assert p.resolve_real_guid(page["data"]["list"][0]["guid"]) == "online:netease:102"
        for endpoint in ("add-track", "remove-track", "delete"):
            assert client.post("/music/api/v1/playlist/" + endpoint,
                               json={"guid": guid, "trackGUIDs": ["online:netease:101"]}).json()["code"] == 0
        assert not any("like" in path for path in state["calls"])


@pytest.mark.asyncio
async def test_logout_hides_personal_lists_and_account_change_separates_cache():
    mb, state = sources()
    async with mb:
        first = await d.summaries(mb)
        assert any(c["account_uid"] == 99 for c in first)
        state["uid"] = 0
        d._account_state.clear()
        logged_out = await d.summaries(mb)
        assert {c["channel"] for c in logged_out} == {"toplist", "category", "newalbum"}
        assert await d.load_tracks(mb, d.FM_PREFIX + "99", p.build_online_track) == []
        state["uid"] = 100
        d._account_state.clear()
        second = await d.summaries(mb)
        assert any(c["guid"] == d.FM_PREFIX + "100" for c in second)
        assert all(c["account_uid"] in (0, 100) for c in second)
        assert len(await d.load_tracks(mb, d.PLAYLIST_PREFIX + "1", p.build_online_track)) == 2


@pytest.mark.asyncio
async def test_stale_tracks_survive_network_failure_and_retry_is_cooled(monkeypatch):
    mb, state = sources()
    guid = d.PLAYLIST_PREFIX + "4"
    async with mb:
        await d.summaries(mb)
        tracks = await d.load_tracks(mb, guid, p.build_online_track)
        assert len(tracks) == 2
        key = d._track_key(guid, 99)
        d._memory[d._path(key)]["savedAt"] = time.time() - 300
        monkeypatch.setenv("FNMUSIC_PLAYLIST_TRACK_CACHE_TTL", "60")
        state["fail"] = True
        assert await d.load_tracks(mb, guid, p.build_online_track) == tracks
        await asyncio.gather(*d._tasks.values())
        calls = len(state["calls"])
        assert await d.load_tracks(mb, guid, p.build_online_track) == tracks
        assert len(state["calls"]) == calls


@pytest.mark.asyncio
async def test_restart_recovers_cover_guid_and_tracks_without_refetch(monkeypatch):
    mb, state = sources()
    guid = d.PLAYLIST_PREFIX + "3"
    async with mb:
        await d.summaries(mb)
        tracks = await d.load_tracks(mb, guid, p.build_online_track)
        d._memory.clear()
        p._FAKE_GUID_REVERSE.clear()
        monkeypatch.setattr(p, "_REGISTRY_WARMED", False)
        p.ensure_registry_warm()
        assert p.resolve_real_guid(d.fake_guid(guid)) == guid
        assert d.cover_for("track_" + d.fake_guid(guid)) == "https://img/3.jpg"
        count = len(state["calls"])
        assert await d.load_tracks(mb, guid, p.build_online_track) == tracks
        assert len(state["calls"]) == count


def test_channel_order_and_daily_token_survive_date_change(monkeypatch):
    monkeypatch.setenv("FNMUSIC_NETEASE_PLAYLIST_ORDER", "daily," + d.PLAYLIST_PREFIX + "4")
    rows = [{"guid": d.PLAYLIST_PREFIX + "4", "channel": "toplist"},
            {"guid": "online:playlist:daily:20991010:user"},
            {"guid": d.PLAYLIST_PREFIX + "1", "channel": "mine"}]
    result = d.sort_cards(rows)
    assert result[0]["guid"].startswith("online:playlist:daily:")
    assert result[1]["guid"] == d.PLAYLIST_PREFIX + "4"
    assert [r["createdAt"] for r in result] == [1, 2, 3]
