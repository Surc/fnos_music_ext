"""Check discovery against the real wrapper's rights filter and NEMbox shapes."""
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[2] / "musicbox-service"
sys.path.insert(0, str(SERVICE_DIR))
import netease_ext as ne
import discovery as service

spec = importlib.util.spec_from_file_location("discovery_service_test_app", SERVICE_DIR / "app.py")
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class FakeAPI:
    def get_account_info(self):
        return {"profile": {"userId": 7}}

    def user_playlist(self, uid, offset=0, limit=50):
        assert uid == 7
        return [{"id": 10, "name": "创建", "creator": {"userId": 7}},
                {"id": 11, "name": "收藏", "subscribed": True, "creator": {"userId": 8}}]

    def recommend_resource(self):
        return [{"id": 12, "name": "推荐", "picUrl": "http://img/recommend.jpg"}]

    def fetch_toplists(self):
        return [("榜单", 13), ("坏数据", "invalid")]

    def playlist_songlist(self, pid):
        return [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 1}, None]

    def songs_detail(self, ids):
        return [{"id": i, "name": f"曲{i}", "ar": [{"name": "歌手"}], "dt": 100000} for i in ids]

    def songs_url(self, ids):
        return [{"id": i, "code": 200, "url": None if i == 2 else f"https://audio/{i}",
                 "freeTrialInfo": {"end": 30} if i == 3 else None, "fee": 1} for i in ids]

    def personal_fm(self):
        return [{"id": 1}, {"id": 4}]

    def search(self, name, stype=1, offset=0, limit=5):
        assert stype == 100
        # NEMbox.search already unwraps result. Its real contract is tested here.
        return {"artists": [{"id": 88, "name": "精准歌手"}, {"id": 89, "name": "近似精准歌手"}]}

    def artists(self, aid):
        assert aid == 88
        return [{"id": 1}, {"id": 4}]


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setattr(ne, "_get_api_locked", lambda: FakeAPI())
    monkeypatch.setattr(ne, "check_is_logged_in", lambda: True)


def test_created_and_subscribed_playlists_and_normalized_https_covers():
    with TestClient(wrapper.app) as client:
        playlists = client.get("/api/v1/playlists/user").json()
        assert playlists["account_uid"] == 7
        assert [r["playlist_id"] for r in playlists["data"]] == [10, 11]
        assert playlists["data"][1]["subscribed"] is True
        assert client.get("/api/v1/playlists/recommend").json()["data"][0]["cover_url"].startswith("https:")


def test_public_playlist_tracks_drop_unavailable_and_trial_audio():
    with TestClient(wrapper.app) as client:
        result = client.get("/api/v1/playlist/13/tracks").json()["data"]
        assert [r["song_id"] for r in result] == [1, 4]
        assert len(client.get("/api/v1/playlists/toplists").json()["data"]) == 1


@pytest.mark.parametrize("endpoint", ["/playlists/user", "/playlists/recommend", "/radio/fm"])
def test_personal_discovery_requires_login(monkeypatch, endpoint):
    monkeypatch.setattr(ne, "check_is_logged_in", lambda: False)
    with TestClient(wrapper.app) as client:
        assert client.get("/api/v1" + endpoint).status_code == 401


def test_artist_candidates_require_exact_name_and_actual_search_shape():
    with TestClient(wrapper.app) as client:
        exact = client.get("/api/v1/discovery/artist-tracks", params={"name": "精准歌手"}).json()
        assert [r["song_id"] for r in exact["data"]] == [1, 4]
        assert client.get("/api/v1/discovery/artist-tracks", params={"name": "精准"}).json()["data"] == []


def test_fm_bound_and_deduplicated():
    with TestClient(wrapper.app) as client:
        rows = client.get("/api/v1/radio/fm", params={"limit": 10}).json()
        assert rows["account_uid"] == 7
        assert [r["song_id"] for r in rows["data"]] == [1, 4]
