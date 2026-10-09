"""User-specific ranking, updating preferences and native/AI fallback behavior."""
import time
import sqlite3

import httpx
import pytest

from proxy import personalization as taste
from proxy import recommend as rec
from proxy.app import build_online_track


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FNMUSIC_HOME", str(tmp_path))
    monkeypatch.setenv("FNMUSIC_PERSONALIZATION_ENABLED", "true")
    monkeypatch.setenv("FNMUSIC_MUSIC_DB", str(tmp_path / "missing.db"))
    monkeypatch.setenv("FNMUSIC_LLM_BASE_URL", "")
    monkeypatch.setenv("FNMUSIC_LLM_API_KEY", "")
    monkeypatch.setattr(rec, "_SOURCE_SLOT_LOCK", __import__("asyncio").Lock())


def test_favorites_recency_and_repeat_counts_change_taste():
    now = time.time()
    recent = {"artist": "甲", "play_count": 2, "playedAt": now}
    old = {"artist": "乙", "play_count": 2, "playedAt": now - 180 * 86400}
    profile = taste.build_profile([recent, old], [{"artist": "丙"}], now)
    assert profile["丙"] > profile["甲"] > profile["乙"]
    pool = [{"artist": "乙"}, {"artist": "甲"}, {"artist": "丙"}]
    assert taste.rank_candidates(pool, profile, 3)[0]["artist"] == "丙"
    assert taste.rank_candidates(pool, taste.build_profile([], [{"artist": "乙"}], now), 3)[0]["artist"] == "乙"


def test_discovery_slots_and_singer_diversity():
    pool = [{"artist": "熟悉", "title": str(i)} for i in range(10)]
    pool += [{"artist": "新歌手" + str(i)} for i in range(5)]
    result = taste.rank_candidates(pool, {"熟悉": 1000}, 8)
    assert result[0]["artist"] == "熟悉"
    assert result[3]["artist"].startswith("新歌手")
    assert result[7]["artist"].startswith("新歌手")


def test_online_play_counts_and_revision_are_user_scoped():
    revision = rec.profile_revision("bob")
    rec.record_online_play("alice", "online:netease:7", {"title": "一", "artist": "甲"})
    rec.record_online_play("alice", "online:netease:7", {"title": "一", "artist": "甲"})
    seeds = rec.seeds_from_online_history("alice")
    assert len(seeds) == 1 and seeds[0]["play_count"] == 2
    assert rec.profile_revision("bob") == revision
    assert rec.profile_revision("alice") != revision


def test_preference_changes_invalidate_cache_after_cooldown():
    now = time.time()
    assert taste.cache_is_fresh({"builtAt": now, "profileRevision": "old"}, "new", now)
    assert not taste.cache_is_fresh({"builtAt": now - 1801, "profileRevision": "old"}, "new", now)
    assert not taste.cache_is_fresh({"builtAt": now - 21601, "profileRevision": "same"}, "same", now)


def test_official_database_revisions_are_user_scoped(tmp_path):
    database = tmp_path / "music.db"
    with sqlite3.connect(database) as con:
        con.executescript("""
            CREATE TABLE user (id INTEGER, guid TEXT);
            CREATE TABLE play_history (user_id INTEGER, updated_at INTEGER, play_count INTEGER);
            CREATE TABLE favorite_track (user_id INTEGER, updated_at INTEGER);
            INSERT INTO user VALUES (1, 'alice'), (2, 'bob');
            INSERT INTO play_history VALUES (1, 1, 1), (2, 1, 1);
        """)
    def revision(user):
        return taste.fingerprint(user, str(database), str(tmp_path / "history"), str(tmp_path / "favorites"))
    original = revision("alice")
    with sqlite3.connect(database) as con:
        con.execute("UPDATE play_history SET play_count=10, updated_at=2 WHERE user_id=2")
        con.execute("INSERT INTO favorite_track VALUES (2, 3)")
    assert revision("alice") == original
    with sqlite3.connect(database) as con:
        con.execute("INSERT INTO favorite_track VALUES (1, 3)")
    assert revision("alice") != original


@pytest.mark.asyncio
async def test_all_users_get_native_candidates_ranked_by_own_favorites(monkeypatch):
    monkeypatch.setattr(rec, "PLAYLIST_SIZE", 3)
    monkeypatch.setattr(rec, "RECOMMEND_COUNT", 3)
    rows = [{"song_id": i, "name": "歌" + str(i), "artist": a, "duration_ms": 120000}
            for i, a in enumerate(["甲", "乙", "丙", "丁"], 1)]

    def handler(request):
        return httpx.Response(200, json={"ok": True, "data": rows if request.url.path == "/api/v1/recommend/songs" else []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://musicbox") as mb:
        a = await rec.get_or_build_daily("alice", None, mb, None, build_online_track, True,
                                        favorite_items=[{"guid": "seed:a", "title": "旧歌", "artist": "甲"}])
        b = await rec.get_or_build_daily("bob", None, mb, None, build_online_track, True,
                                        favorite_items=[{"guid": "seed:b", "title": "旧歌", "artist": "乙"}])
    assert a["tracks"][0]["guid"] == "online:netease:1"
    assert b["tracks"][0]["guid"] == "online:netease:2"
    assert "netease-daily" in a["tiers"] and "netease-daily" in b["tiers"]
    assert not rec.source_slot_claimed()


@pytest.mark.asyncio
async def test_configured_ai_supplements_a_full_native_candidate_pool(monkeypatch):
    monkeypatch.setenv("FNMUSIC_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FNMUSIC_LLM_API_KEY", "test-placeholder")
    monkeypatch.setattr(rec, "PLAYLIST_SIZE", 6)
    async def llm(*args):
        return [{"title": "AI候选", "artist": "新歌手"}]
    async def resolved(*args, **kwargs):
        return [build_online_track({"id": "netease:99", "source": "netease", "title": "AI候选", "artist": "新歌手"})]
    monkeypatch.setattr(rec, "call_llm", llm)
    monkeypatch.setattr(rec, "resolve_recommendations", resolved)
    rows = [{"song_id": i, "name": "歌" + str(i), "artist": str(i), "duration_ms": 120000} for i in range(1, 10)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "data": rows})), base_url="http://musicbox") as mb:
        result = await rec.get_or_build_daily("alice", None, mb, mb, build_online_track, True)
    assert "llm" in result["tiers"]
    assert any(t["guid"] == "online:netease:99" for t in result["tracks"])
