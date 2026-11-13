"""polls the roster and logs who was active (last action in the last 15 min)"""

import sqlite3
import time
from pathlib import Path

DAY = 86400
SLOT = 900  # 15 minutes
SLOTS_PER_DAY = 96
SLOTS_PER_BLOCK = 8  # 2-hour blocks
BLOCKS_PER_DAY = 12
MIN_POLLS = 8  # 2 hours of polling before a summary means anything

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    faction_id INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    PRIMARY KEY (faction_id, ts)
);
CREATE TABLE IF NOT EXISTS sightings (
    faction_id  INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    player_id   INTEGER NOT NULL,
    name        TEXT,
    last_action INTEGER,
    online      INTEGER,
    PRIMARY KEY (faction_id, ts, player_id)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def record_poll(con: sqlite3.Connection, faction_id: int, members: list[dict], ts: int | None = None) -> int:
    ts = int(ts or time.time())
    con.execute("INSERT OR IGNORE INTO polls VALUES (?, ?)", (faction_id, ts))
    rows = []
    for m in members:
        last_action = m.get("last_action") or {}
        rows.append(
            (
                faction_id,
                ts,
                int(m["id"]),
                m.get("name"),
                int(last_action.get("timestamp") or 0),
                1 if str(last_action.get("status", "")).lower() == "online" else 0,
            )
        )
    con.executemany("INSERT OR IGNORE INTO sightings VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    return len(rows)


def _block_label(start_slot: int, n_slots: int) -> str:
    def hm(slot):
        minutes = (slot % SLOTS_PER_DAY) * 15
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return f"{hm(start_slot)}-{hm(start_slot + n_slots)} TCT"


def summarise(
    con: sqlite3.Connection,
    faction_id: int,
    roster_ids: list[int],
    days: int = 14,
    now: int | None = None,
) -> dict | None:
    # returns None if theres not enough polls yet
    now = int(now or time.time())
    since = now - days * DAY
    polls = [
        r[0]
        for r in con.execute(
            "SELECT ts FROM polls WHERE faction_id=? AND ts>=? ORDER BY ts", (faction_id, since)
        )
    ]
    if len(polls) < MIN_POLLS:
        return None

    roster = set(roster_ids)
    seen: dict[int, dict[int, bool]] = {}  # ts -> {player: active}
    for ts, pid, last_action, online in con.execute(
        "SELECT ts, player_id, last_action, online FROM sightings WHERE faction_id=? AND ts>=?",
        (faction_id, since),
    ):
        if pid not in roster:
            continue
        active = bool(online) or (last_action and last_action >= ts - SLOT)
        seen.setdefault(ts, {})[pid] = active

    # put each poll in the slot for the middle of its window
    slot_polls = [0] * SLOTS_PER_DAY
    slot_active = [0] * SLOTS_PER_DAY
    player_block_polls = {pid: [0] * BLOCKS_PER_DAY for pid in roster}
    player_block_active = {pid: [0] * BLOCKS_PER_DAY for pid in roster}
    peak_single = 0
    for ts in polls:
        slot = ((ts - SLOT // 2) % DAY) // SLOT
        block = slot // SLOTS_PER_BLOCK
        states = seen.get(ts, {})
        count = sum(1 for a in states.values() if a)
        peak_single = max(peak_single, count)
        slot_polls[slot] += 1
        slot_active[slot] += count
        for pid in roster:
            if pid in states:
                player_block_polls[pid][block] += 1
                if states[pid]:
                    player_block_active[pid][block] += 1

    profile = [
        (slot_active[i] / slot_polls[i]) if slot_polls[i] else None for i in range(SLOTS_PER_DAY)
    ]
    filled = [p for p in profile if p is not None]
    avg_active = sum(filled) / len(filled) if filled else 0
    roster_size = len(roster) or 1

    def rolling(n):
        best = worst = None
        for start in range(SLOTS_PER_DAY):
            values = [profile[(start + k) % SLOTS_PER_DAY] for k in range(n)]
            values = [v for v in values if v is not None]
            if len(values) < n / 2:
                continue
            avg = sum(values) / len(values)
            if best is None or avg > best[1]:
                best = (start, avg)
            if worst is None or avg < worst[1]:
                worst = (start, avg)
        return best, worst

    windows = []
    for label, n in (("1 hour", 4), ("4 hours", 16)):
        best, worst = rolling(n)
        for kind, found in (("Strongest", best), ("Quietest", worst)):
            if found:
                start, avg = found
                windows.append(
                    {
                        "label": f"{kind} {label}",
                        "period": _block_label(start, n),
                        "avg": avg,
                        "pct": avg / roster_size,
                    }
                )

    player_blocks = {
        pid: [
            (player_block_active[pid][b] / player_block_polls[pid][b]) if player_block_polls[pid][b] else None
            for b in range(BLOCKS_PER_DAY)
        ]
        for pid in roster
    }

    span_days = (polls[-1] - polls[0]) / DAY
    return {
        "days": days,
        "span_days": span_days,
        "polls": len(polls),
        "profile": profile,
        "avg_bucket": avg_active,
        "avg_ratio": avg_active / roster_size,
        "peak_single": peak_single,
        "windows": windows,
        "player_blocks": player_blocks,
        "roster_size": len(roster),
    }
