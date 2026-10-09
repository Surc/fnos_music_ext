"""User-local taste ranking. No credentials or cloud listening-history writes."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter


def enabled() -> bool:
    # Absent key keeps legacy upstream semantics; new installs opt in via template.
    return os.environ.get("FNMUSIC_PERSONALIZATION_ENABLED", "false").lower() in ("true", "1", "yes")


def int_setting(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name) or default)))
    except (ValueError, TypeError):
        return default


def artists_of(item: dict) -> list[str]:
    track = item.get("track") if isinstance(item.get("track"), dict) else item
    names = track.get("artist") or track.get("artistName") or ""
    if not names and isinstance(track.get("artists"), list):
        names = "/".join(str(a.get("name") or "") if isinstance(a, dict) else str(a)
                         for a in track["artists"])
    return list(dict.fromkeys(a.strip() for a in re.split(r"\s*(?:/|、|;| feat\. )\s*", str(names)) if a.strip()))


def build_profile(plays: list[dict], favorites: list[dict], now: float | None = None) -> dict[str, float]:
    now = time.time() if now is None else now
    scores: Counter = Counter()
    for item in plays:
        age = max(0.0, now - float(item.get("playedAt") or now)) / 86400
        try:
            count = max(1, min(1000, int(item.get("play_count") or item.get("playCount") or 1)))
        except (ValueError, TypeError):
            count = 1
        weight = (1.0 + math.log1p(count)) * math.exp(-age / 30.0)
        for artist in artists_of(item):
            scores[artist.casefold()] += weight
    for item in favorites:
        for artist in artists_of(item):
            scores[artist.casefold()] += 5.0
    return dict(scores)


def top_artists(plays: list[dict], favorites: list[dict], count: int = 2) -> list[str]:
    profile = build_profile(plays, favorites)
    names = {a.casefold(): a for row in plays + favorites for a in artists_of(row)}
    return [names[key] for key in sorted(profile, key=profile.get, reverse=True)[:count]]


def rank_candidates(items: list[dict], profile: dict[str, float], limit: int) -> list[dict]:
    """Rank by familiar artists while reserving one in four slots for discovery.

    Artist repetition carries a growing penalty. Ties preserve source order, so
    empty profiles keep the original recommendation order.
    """
    remaining = list(enumerate(items))
    out = []
    counts: Counter = Counter()
    while remaining and len(out) < limit:
        explore = bool(profile) and len(out) % 4 == 3

        def score(pair):
            index, row = pair
            artists = [a.casefold() for a in artists_of(row)]
            affinity = sum(profile.get(a, 0.0) for a in artists)
            novelty = 1.0 if not affinity else 0.0
            repeat = sum(counts[a] for a in artists)
            return ((100.0 * novelty if explore else math.log1p(affinity)) - 1.5 * repeat, -index)

        chosen = max(remaining, key=score)
        remaining.remove(chosen)
        row = chosen[1]
        out.append(row)
        counts.update(a.casefold() for a in artists_of(row))
    return out


def fingerprint(user_guid: str, db_path: str, history_dir: str, favorite_dir: str) -> str:
    """A lightweight, per-user revision; never use global DB mtime for isolation."""
    state = []
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(user_guid or "shared"))
    for directory in (history_dir, favorite_dir):
        try:
            stat = os.stat(os.path.join(directory, safe + ".json"))
            state.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            state.append(None)
    if db_path and os.path.isfile(db_path):
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
                uid = con.execute("SELECT id FROM user WHERE guid = ?", (user_guid,)).fetchone()
                if uid:
                    for table in ("play_history", "favorite_track"):
                        # Table names are constants, never user input.
                        columns = "COUNT(*), MAX(updated_at)" + (", SUM(play_count)" if table == "play_history" else "")
                        state.append(con.execute(f"SELECT {columns} FROM {table} WHERE user_id = ?", (uid[0],)).fetchone())
        except sqlite3.Error:
            state.append("unavailable")
    return hashlib.sha256(json.dumps(state, default=str).encode()).hexdigest()[:24]


def cache_is_fresh(data: dict, revision: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    age = max(0.0, now - float(data.get("builtAt") or 0))
    ttl = int_setting("FNMUSIC_PERSONALIZATION_REFRESH_S", 21600, 300, 86400)
    cooldown = int_setting("FNMUSIC_PERSONALIZATION_MIN_REFRESH_S", 1800, 60, 86400)
    # Checkpoints from an active build stay readable, even if another play arrives.
    return age < ttl and (data.get("profileRevision") == revision or age < cooldown)
