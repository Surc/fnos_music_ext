"""NetEase playlist discovery adapted from the gzywd fork, isolated from playback.

Summary/track caches use stale-while-refresh. Personal lists are account-scoped;
fnOS user taste stays in recommend.py and personalization.py, not these caches.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from uuid import uuid4

try:
    from . import recommend as rec
except ImportError:
    import recommend as rec

logger = logging.getLogger("fnmusic_proxy.discovery")
PLAYLIST_PREFIX = "online:playlist:ne:"
ALBUM_PREFIX = "online:playlist:nealbum:"
FM_PREFIX = "online:playlist:nefm:"
CHANNELS = ("mine", "nrec", "toplist", "category", "newalbum", "fm")
PRIVATE = {"mine", "nrec", "fm"}
DEFAULT_CHANNELS = "mine,nrec,toplist,category,newalbum,fm"
DEFAULT_ORDER = "daily,hot,mine,nrec,toplist,category,newalbum,fm"
_tasks: dict[tuple, asyncio.Task] = {}
_account_state: dict[tuple, tuple[float, int]] = {}
_memory: dict[str, dict] = {}


def enabled() -> bool:
    return os.environ.get("FNMUSIC_DISCOVERY_ENABLED", "false").lower() in ("true", "1", "yes")


def cache_dir() -> str:
    return os.environ.get("FNMUSIC_DISCOVERY_DIR") or os.path.join(rec.home_dir(), "discovery_cache")


def _integer(name: str, default: int, low: int = 1, high: int = 1000) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name) or default)))
    except (ValueError, TypeError):
        return default


def channels() -> list[str]:
    raw = os.environ.get("FNMUSIC_NETEASE_CHANNELS", DEFAULT_CHANNELS)
    return list(dict.fromkeys(c.strip() for c in raw.split(",") if c.strip() in CHANNELS))


def is_guid(guid: str | None) -> bool:
    return bool(re.fullmatch(r"online:playlist:(?:ne|nealbum|nefm):[1-9][0-9]*", str(guid or "")))


def fake_guid(guid: str) -> str:
    return hashlib.md5(f"fnmusic-ext::{guid}".encode()).hexdigest()


def _path(key: str) -> str:
    prefix = "catalog-" if key.startswith("catalog:") else "tracks-" if key.startswith("tracks:") else "state-"
    return os.path.join(cache_dir(), prefix + hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def _load(key: str) -> dict | None:
    path = _path(key)
    if path in _memory:
        if time.time() - _memory[path].get("savedAt", 0) <= 7 * 86400:
            _memory[path]["_used"] = time.monotonic()
            return _memory[path]
        _memory.pop(path, None)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or time.time() - data.get("savedAt", 0) > 7 * 86400:
            return None
        for field in ("items", "tracks"):
            if field in data and not isinstance(data[field], list):
                return None
            if field in data:
                data[field] = [row for row in data[field] if isinstance(row, dict)]
        _remember_memory(path, data)
        return data
    except (OSError, ValueError, TypeError):
        return None


def _remember_memory(path: str, data: dict) -> None:
    if len(_memory) >= 24:
        oldest = min(_memory, key=lambda p: _memory[p].get("_used", 0))
        _memory.pop(oldest, None)
    _memory[path] = {**data, "_used": time.monotonic()}


def _save(key: str, payload: dict) -> None:
    data = {**payload, "savedAt": int(time.time())}
    path = _path(key)
    part = path + "." + uuid4().hex[:8] + ".part"
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(part, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.chmod(part, 0o600)
        os.replace(part, path)
        _remember_memory(path, data)
    except OSError as exc:
        logger.warning("discovery cache write failed: %s", type(exc).__name__)
    finally:
        if os.path.exists(part):
            os.unlink(part)


async def _get(client, path: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    response = await client.get(path, params=params or {}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("ok") is False:
        raise ValueError("unavailable discovery response")
    return data


async def account(client) -> int:
    key = (cache_dir(), id(client))
    hit = _account_state.get(key)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    try:
        body = await _get(client, "/api/v1/discovery/account", timeout=2.0)
        uid = int(body.get("account_uid") or 0) if body.get("logged_in") else 0
    except Exception:
        uid = 0
    _account_state[key] = (time.monotonic(), uid)
    return uid


def _summary_key(channel: str, uid: int) -> str:
    cat = os.environ.get("FNMUSIC_NETEASE_CATEGORY", "华语") if channel == "category" else ""
    return f"catalog:{channel}:{uid if channel in PRIVATE else 0}:{cat}"


def _card(guid: str, name: str, cover: str, count: int, channel: str, uid: int) -> dict:
    if cover.startswith("http://"):
        cover = "https://" + cover[7:]
    return {"guid": guid, "name": name, "coverId": "track_" + fake_guid(guid),
            "cover_url": cover, "trackCount": count, "createdAt": 1, "updatedAt": 1,
            "isDaily": True, "channel": channel,
            "account_uid": uid if channel in PRIVATE else 0}


def _task(key: tuple, factory) -> asyncio.Task:
    old = _tasks.get(key)
    # Completed failures retry after 60s, avoiding requests on each UI refresh.
    if old is not None and not old.done():
        return old
    cooldown = _retry_after.get(key, 0)
    if old is not None and time.monotonic() < cooldown:
        return old

    async def run():
        try:
            return await factory()
        except Exception as exc:
            _retry_after[key] = time.monotonic() + 60
            logger.warning("discovery refresh failed: %s", type(exc).__name__)
            return None

    task = asyncio.create_task(run())
    _tasks[key] = task
    return task


_retry_after: dict[tuple, float] = {}


async def _fetch_catalog(client, channel: str, uid: int) -> list[dict]:
    limit = _integer("FNMUSIC_NETEASE_CHANNEL_LIMIT", 8, 1, 50)
    routes = {"mine": "/api/v1/playlists/user", "nrec": "/api/v1/playlists/recommend",
              "toplist": "/api/v1/playlists/toplists", "category": "/api/v1/playlists/category",
              "newalbum": "/api/v1/playlists/newalbums"}
    if channel == "fm":
        body = await _get(client, "/api/v1/radio/fm", {"limit": 10})
        if int(body.get("account_uid") or 0) != uid:
            raise ValueError("account changed during FM refresh")
        rows = body.get("data") or []
        cards = [_card(FM_PREFIX + str(uid), "私人FM｜网易云", "", len(rows), channel, uid)] if rows else []
    else:
        params = {"limit": min(limit, 50)}
        if channel == "category":
            params["cat"] = os.environ.get("FNMUSIC_NETEASE_CATEGORY", "华语")
        body = await _get(client, routes[channel], params)
        if channel in PRIVATE and int(body.get("account_uid") or 0) != uid:
            raise ValueError("account changed during refresh")
        rows = body.get("data")
        if not isinstance(rows, list):
            raise ValueError("invalid catalog")
        cards = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            target = str(row.get("album_id" if channel == "newalbum" else "playlist_id") or "")
            guid = (ALBUM_PREFIX if channel == "newalbum" else PLAYLIST_PREFIX) + target
            if not is_guid(guid):
                continue
            prefix = {"mine": "网易云·收藏" if row.get("subscribed") else "网易云·",
                      "nrec": "推荐｜", "toplist": "榜｜",
                      "category": params.get("cat", "华语") + "｜", "newalbum": "新碟｜"}[channel]
            cards.append(_card(guid, prefix + str(row.get("name") or target),
                               str(row.get("cover_url") or ""), int(row.get("track_count") or 0), channel, uid))
    _save(_summary_key(channel, uid), {"items": cards, "account_uid": uid})
    return cards


async def summaries(client) -> list[dict]:
    uid = await account(client)
    selected = [ch for ch in channels() if ch not in PRIVATE or uid]
    pending = []
    ttl = _integer("FNMUSIC_PLAYLIST_SUMMARY_TTL", 300, 30, 86400)
    for ch in selected:
        key = _summary_key(ch, uid)
        cached = _load(key)
        if cached is None or time.time() - cached.get("savedAt", 0) >= ttl:
            pending.append(_task((cache_dir(), key), lambda ch=ch: _fetch_catalog(client, ch, uid)))
    if pending:
        # Shield preserves refresh tasks if the user leaves the page early.
        await asyncio.wait([asyncio.shield(t) for t in pending], timeout=0.8)
    cards, seen = [], set()
    for ch in selected:
        for row in (_load(_summary_key(ch, uid)) or {}).get("items", []):
            if row.get("guid") not in seen:
                seen.add(row["guid"])
                cards.append(dict(row))
    return cards


def all_cards() -> list[dict]:
    cards = []
    try:
        # Cover/detail lookup must never parse every large track-cache file.
        paths = [os.path.join(cache_dir(), name) for name in os.listdir(cache_dir())
                 if name.startswith("catalog-") and name.endswith(".json")]
    except OSError:
        return []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                continue
            if time.time() - data.get("savedAt", 0) <= 7 * 86400:
                cards.extend(row for row in data.get("items", []) if isinstance(row, dict))
        except (OSError, ValueError, TypeError):
            continue
    return cards


def card_for(guid: str, uid: int | None = None) -> dict | None:
    card = next((dict(c) for c in all_cards() if c.get("guid") == guid
                 and (uid is None or not c.get("account_uid") or c.get("account_uid") == uid)), None)
    return stamp_card(card) if card else None


def stamp_card(card: dict) -> dict:
    if not enabled():
        return card
    position = ((_load("display-order") or {}).get("order") or {}).get(card.get("guid"))
    if isinstance(position, int) and position > 0:
        return {**card, "createdAt": position, "updatedAt": position}
    return card


def cover_for(raw_guid: str) -> str:
    fake = raw_guid[6:] if raw_guid.startswith("track_") else raw_guid
    return next((c.get("cover_url") or "" for c in all_cards()
                 if c.get("guid") == raw_guid or fake_guid(c.get("guid", "")) == fake), "")


def _track_key(guid: str, uid: int) -> str:
    # An ID can be both a public recommendation and a private saved playlist.
    return f"tracks:{uid}:{guid}"


async def _fetch_tracks(client, guid: str, build_track, uid: int) -> list[dict]:
    limit = _integer("FNMUSIC_PLAYLIST_TRACK_LIMIT", 300, 1, 1000)
    tid = guid.rsplit(":", 1)[-1]
    if guid.startswith(FM_PREFIX):
        route, limit = "/api/v1/radio/fm", min(limit, 20)
    elif guid.startswith(ALBUM_PREFIX):
        route = f"/api/v1/album/{tid}/tracks"
    else:
        route = f"/api/v1/playlist/{tid}/tracks"
    data = await _get(client, route, {"limit": limit}, timeout=60.0)
    if guid.startswith(FM_PREFIX) and int(data.get("account_uid") or 0) != uid:
        raise ValueError("account changed during FM refresh")
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ValueError("invalid tracks")
    tracks = rec.resolve_source_candidates([rec._musicbox_recommend_item(r) for r in rows if isinstance(r, dict)],
                                          build_track, limit)
    card = card_for(guid, uid) or _card(guid, "网易云歌单", "", len(tracks), "toplist", 0)
    _save(_track_key(guid, uid), {"playlist": card, "tracks": tracks, "account_uid": uid})
    return tracks


async def load_tracks(client, guid: str, build_track) -> list[dict]:
    if not is_guid(guid):
        return []
    uid = await account(client)
    card = card_for(guid, uid) or {}
    if not card and any(c.get("guid") == guid and c.get("account_uid") for c in all_cards()):
        return []
    if (guid.startswith(FM_PREFIX) and int(guid.rsplit(":", 1)[-1]) != uid
            or card.get("channel") in PRIVATE and card.get("account_uid") != uid):
        return []
    key = _track_key(guid, uid)
    cached = _load(key)
    ttl = 300 if guid.startswith(FM_PREFIX) else _integer("FNMUSIC_PLAYLIST_TRACK_CACHE_TTL", 21600, 60, 86400)
    if cached is not None and time.time() - cached.get("savedAt", 0) < ttl:
        return list(cached.get("tracks") or [])
    task = _task((cache_dir(), key), lambda: _fetch_tracks(client, guid, build_track, uid))
    if cached is not None:
        return list(cached.get("tracks") or [])
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=15.0) or []
    except asyncio.TimeoutError:
        return []


def sort_cards(cards: list[dict]) -> list[dict]:
    order = os.environ.get("FNMUSIC_NETEASE_CHANNEL_ORDER", DEFAULT_ORDER).split(",")
    order = list(dict.fromkeys(c.strip() for c in order + DEFAULT_ORDER.split(",") if c.strip()))
    tokens = [t.strip() for t in os.environ.get("FNMUSIC_NETEASE_PLAYLIST_ORDER", "").split(",") if t.strip()]

    def channel(card):
        guid = card.get("guid", "")
        return rec.online_playlist_kind(guid) or card.get("channel", "mine")

    def key(card):
        ch = channel(card)
        g = card.get("guid", "")
        explicit = next((i for i, t in enumerate(tokens) if t == g or t in ("daily", "hot") and t == ch), len(tokens))
        return explicit, order.index(ch) if ch in order else len(order)

    result = sorted(cards, key=key)
    for index, card in enumerate(result, 1):
        card["createdAt"] = card["updatedAt"] = index
    positions = {c["guid"]: c["createdAt"] for c in result if c.get("guid")}
    if (_load("display-order") or {}).get("order") != positions:
        _save("display-order", {"order": positions})
    return result


def prewarm(client, build_track, cards: list[dict]) -> None:
    async def run():
        for card in cards[:8]:
            if enabled():
                await load_tracks(client, card["guid"], build_track)
    _task((cache_dir(), "prewarm"), run)


async def refresh_loop(client, build_track, source_enabled) -> None:
    """Refresh cached playlists at a configurable NAS-local time, default 04:30."""
    while True:
        raw = os.environ.get("FNMUSIC_PLAYLIST_REFRESH_AT", "04:30")
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", raw):
            await asyncio.sleep(60)
            continue
        now = datetime.now()
        hour, minute = map(int, raw.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        # Short sleeps allow hot settings changes to take effect without restart.
        if (target - now).total_seconds() > 60:
            await asyncio.sleep(min(60, (target - now).total_seconds()))
            continue
        await asyncio.sleep(max(1, (target - now).total_seconds()))
        if enabled() and source_enabled():
            cards = await summaries(client)
            for card in cards:
                _memory.pop(_path(_track_key(card["guid"], await account(client))), None)
                await _task((cache_dir(), "scheduled", card["guid"]),
                            lambda card=card: _fetch_tracks(client, card["guid"], build_track, _account_state.get((cache_dir(), id(client)), (0, 0))[1]))
        await asyncio.sleep(61)


async def shutdown() -> None:
    tasks = [t for t in _tasks.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _retry_after.clear()
    _account_state.clear()
    _memory.clear()
