"""Additive NetEase discovery APIs, adapted from gzywd/fnos_music_ext.

Keep the upstream musicbox wrapper and entitlement checks intact. All calls use
the upstream session lock; no second account/session or independent URL resolver.
See docs/UPSTREAM.md for the exact source revision and adaptation boundaries.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query

import netease_ext as ne

router = APIRouter(prefix="/api/v1")


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError):
        return 0


def _https(value: Any) -> str:
    url = str(value or "").strip()
    return "https://" + url[7:] if url.startswith("http://") else url


def _call(method: str, *args, **kwargs):
    with ne._api_lock:
        api = ne._get_api_locked()
        return getattr(api, method)(*args, **kwargs)


def account_uid() -> int:
    if not ne.check_is_logged_in():
        return 0
    info = _call("get_account_info")
    if not isinstance(info, dict):
        return 0
    profile = info.get("profile") or {}
    account = info.get("account") or {}
    return _integer(profile.get("userId") or account.get("id"))


def _require_account() -> int:
    uid = account_uid()
    if not uid:
        raise HTTPException(401, "not_logged_in")
    return uid


def normalize_playlist(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pid = _integer(raw.get("id") or raw.get("playlistId"))
    if not pid:
        return None
    creator = raw.get("creator") or {}
    return {
        "playlist_id": pid,
        "name": str(raw.get("name") or raw.get("title") or f"歌单 {pid}"),
        "cover_url": _https(raw.get("coverImgUrl") or raw.get("picUrl")),
        "track_count": _integer(raw.get("trackCount")),
        "subscribed": bool(raw.get("subscribed")),
        "creator_id": _integer(creator.get("userId")) if isinstance(creator, dict) else 0,
    }


def _playlists(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        raise HTTPException(502, "invalid_playlist_response")
    return [row for item in raw if (row := normalize_playlist(item)) is not None]


def _song_ids(raw: Any, limit: int) -> list[int]:
    if not isinstance(raw, list):
        raise HTTPException(502, "invalid_track_response")
    out = []
    for item in raw:
        sid = _integer(item.get("id") if isinstance(item, dict) else item)
        if sid and sid not in out:
            out.append(sid)
        if len(out) >= limit:
            break
    return out


def _tracks(raw: Any, limit: int) -> list[dict]:
    # Reuse upstream batch detail + real URL/non-trial checks. Preserve song order.
    ids = _song_ids(raw, limit)
    out = []
    for offset in range(0, len(ids), 100):
        out.extend(ne.batch_song_details(ids[offset:offset + 100]))
    return out[:limit]


@router.get("/discovery/account")
def discovery_account():
    uid = account_uid()
    return {"ok": True, "logged_in": bool(uid), "account_uid": uid}


@router.get("/playlists/user")
def user_playlists(limit: int = Query(100, ge=1, le=200)):
    uid = _require_account()
    rows = _playlists(_call("user_playlist", uid, offset=0, limit=limit))
    return {"ok": True, "data": rows[:limit], "account_uid": uid}


@router.get("/playlists/recommend")
def recommend_playlists():
    uid = _require_account()
    return {"ok": True, "data": _playlists(_call("recommend_resource")), "account_uid": uid}


@router.get("/playlists/toplists")
def toplists():
    raw = _call("fetch_toplists")
    if not isinstance(raw, (list, tuple)):
        raise HTTPException(502, "invalid_toplist_response")
    rows = []
    for pair in raw:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            row = normalize_playlist({"name": pair[0], "id": pair[1]})
            if row:
                rows.append(row)
    return {"ok": True, "data": rows}


@router.get("/playlists/category")
def category_playlists(cat: str = Query("华语", max_length=80),
                       order: str = Query("hot", pattern="^(hot|new)$"),
                       limit: int = Query(20, ge=1, le=50)):
    raw = _call("top_playlists", cat, order, 0, limit)
    return {"ok": True, "data": _playlists(raw)[:limit]}


@router.get("/playlists/categories")
def categories():
    with ne._api_lock:
        api = ne._get_api_locked()
        raw = api.playlist_catelogs()
        parsed = api._parse_playlist_classes(raw) if isinstance(raw, dict) else {}
        if not parsed:
            parsed = api._get_playlist_classes() or {}
    return {"ok": True, "data": parsed}


@router.get("/playlists/newalbums")
def new_albums(limit: int = Query(20, ge=1, le=50)):
    raw = _call("new_albums", offset=0, limit=limit)
    if not isinstance(raw, list):
        raise HTTPException(502, "invalid_album_response")
    rows = []
    for item in raw[:limit]:
        if not isinstance(item, dict) or not _integer(item.get("id")):
            continue
        artist = item.get("artist") or {}
        rows.append({"album_id": _integer(item["id"]), "name": str(item.get("name") or ""),
                     "cover_url": _https(item.get("picUrl") or item.get("blurPicUrl")),
                     "artist": str(artist.get("name") or "") if isinstance(artist, dict) else ""})
    return {"ok": True, "data": rows}


@router.get("/playlist/{playlist_id}/tracks")
def playlist_tracks(playlist_id: int = Path(..., ge=1),
                    limit: int = Query(300, ge=1, le=1000)):
    # Public charts/categories work without login; upstream entitlement filtering
    # still restricts each returned track to this session's real playback rights.
    raw = _call("playlist_songlist", playlist_id)
    return {"ok": True, "data": _tracks(raw, limit)}


@router.get("/album/{album_id}/tracks")
def album_tracks(album_id: int = Path(..., ge=1), limit: int = Query(300, ge=1, le=1000)):
    return {"ok": True, "data": _tracks(_call("album", album_id), limit)}


@router.get("/radio/fm")
def personal_fm(limit: int = Query(10, ge=1, le=20)):
    uid = _require_account()
    songs = []
    seen = set()
    # Bound upstream requests; FM is presented as a five-minute playlist snapshot.
    for _ in range(4):
        raw = _call("personal_fm")
        for row in _tracks(raw, limit):
            if row["song_id"] not in seen:
                seen.add(row["song_id"])
                songs.append(row)
        if len(songs) >= limit or not raw:
            break
    return {"ok": True, "data": songs[:limit], "account_uid": uid}


@router.get("/discovery/artist-tracks")
def artist_tracks(name: str = Query(..., min_length=1, max_length=120),
                  limit: int = Query(30, ge=1, le=50)):
    result = _call("search", name, stype=100, offset=0, limit=5)
    result = result.get("result", result) if isinstance(result, dict) else {}
    artists = result.get("artists") or []
    # Never silently match a similarly named artist when using a user's profile.
    hit = next((a for a in artists if isinstance(a, dict)
                and str(a.get("name") or "").strip().casefold() == name.strip().casefold()), None)
    aid = _integer((hit or {}).get("id"))
    rows = _tracks(_call("artists", aid), limit) if aid else []
    return {"ok": True, "data": rows}
