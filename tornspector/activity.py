"""polls the roster and logs who was active (last action in the last 15 min)"""

import csv
import sqlite3
import time
from pathlib import Path

DAY = 86400
SLOT = 900  # 15 minutes
SLOTS_PER_DAY = DAY // SLOT
SLOTS_PER_BLOCK = 8  # 2-hour blocks
BLOCKS_PER_DAY = 12
MIN_POLLS = 8  # need 2 hours of polls before the summary means anything
AWAY_STATES = {"Traveling", "Abroad"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    faction_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (faction_id, ts)
);
CREATE TABLE IF NOT EXISTS sightings (
    faction_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    name TEXT,
    last_action INTEGER,
    online INTEGER,
    state TEXT,
    until INTEGER,
    PRIMARY KEY (faction_id, ts, player_id)
);
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    # logs from before v0.3 dont have the status columns
    cols = {row[1] for row in con.execute("PRAGMA table_info(sightings)")}
    if "state" not in cols:
        con.execute("ALTER TABLE sightings ADD COLUMN state TEXT")
        con.execute("ALTER TABLE sightings ADD COLUMN until INTEGER")
    return con


def record_poll(con, faction_id, members, ts=None):
    ts = int(ts or time.time())
    con.execute("INSERT OR IGNORE INTO polls VALUES (?, ?)", (faction_id, ts))
    rows = []
    for m in members:
        la = m.get("last_action") or {}
        st = m.get("status") or {}
        online = str(la.get("status", "")).lower() == "online"
        rows.append(
            (faction_id, ts, int(m["id"]), m.get("name"), int(la.get("timestamp") or 0), int(online),
             st.get("state") or None, st.get("until") or None)
        )
    con.executemany("INSERT OR IGNORE INTO sightings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    return len(rows)


CSV_COLUMNS = ["faction_id", "ts", "time_tct", "player_id", "name", "last_action", "online", "state", "until"]


def export_csv(con, faction_id, path):
    # time_tct is just so it reads ok in a spreadsheet, import uses ts
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = con.execute(
        "SELECT ts, player_id, name, last_action, online, state, until FROM sightings "
        "WHERE faction_id=? ORDER BY ts, player_id",
        (faction_id,),
    )
    n = 0
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_COLUMNS)
        for ts, pid, name, last_action, online, state, until in rows:
            tct = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))
            writer.writerow([faction_id, ts, tct, pid, name or "", last_action, online, state or "", until or ""])
            n += 1
    return n


def import_csv(con, path):
    # for merging someone elses watch log into yours. dupes get skipped
    polls = set()
    sightings = []
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        rows = csv.DictReader(file)
        missing = {"faction_id", "ts", "player_id", "last_action", "online"} - set(rows.fieldnames or [])
        if missing:
            raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
        for row in rows:
            try:
                fid = int(row["faction_id"])
                ts = int(row["ts"])
                pid = int(row["player_id"])
                last_action = int(row["last_action"] or 0)
                online = int(row["online"] or 0)
                # csvs from before v0.3 dont have these two
                until = int(row["until"]) if row.get("until") else None
            except (TypeError, ValueError) as exc:
                raise ValueError(f"line {rows.line_num}: use whole numbers") from exc
            polls.add((fid, ts))
            state = row.get("state") or None
            sightings.append((fid, ts, pid, row.get("name") or None, last_action, 1 if online else 0, state, until))

    con.executemany("INSERT OR IGNORE INTO polls VALUES (?, ?)", sorted(polls))
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO sightings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", sightings)
    added = con.total_changes - before
    con.commit()
    return added


def _block_label(start_slot, n_slots):
    def hm(slot):
        minutes = (slot % SLOTS_PER_DAY) * 15
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return f"{hm(start_slot)}-{hm(start_slot + n_slots)} TCT"


def summarise(con, faction_id, roster_ids, days=14, now=None):
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
    seen = {}  # ts -> {player: (active, state)}
    for ts, pid, last_action, online, state in con.execute(
        "SELECT ts, player_id, last_action, online, state FROM sightings WHERE faction_id=? AND ts>=?",
        (faction_id, since),
    ):
        if pid not in roster:
            continue
        active = bool(online) or bool(last_action and last_action >= ts - SLOT)
        seen.setdefault(ts, {})[pid] = (active, state)

    # put each poll in the slot for the middle of its window
    slot_polls = [0] * SLOTS_PER_DAY
    slot_active = [0] * SLOTS_PER_DAY
    slot_ready = [0] * SLOTS_PER_DAY
    blk_polls = {pid: [0] * BLOCKS_PER_DAY for pid in roster}
    blk_active = {pid: [0] * BLOCKS_PER_DAY for pid in roster}
    # TODO jail/federal time isnt broken out yet, only hosp + away.
    # also cant tell a self hosp from getting hosped with just the state
    status_polls = {pid: 0 for pid in roster}
    hosp_polls = {pid: 0 for pid in roster}
    away_polls = {pid: 0 for pid in roster}
    peak_single = 0
    for ts in polls:
        slot = ((ts - SLOT // 2) % DAY) // SLOT
        block = slot // SLOTS_PER_BLOCK
        states = seen.get(ts, {})
        count = sum(1 for active, _ in states.values() if active)
        # ready = active and not in hospital, jail or out of the country.
        # rows from before v0.3 have no state so they count as ready
        ready = sum(1 for active, state in states.values() if active and state in (None, "Okay"))
        peak_single = max(peak_single, count)
        slot_polls[slot] += 1
        slot_active[slot] += count
        slot_ready[slot] += ready
        for pid in roster:
            if pid in states:
                active, state = states[pid]
                blk_polls[pid][block] += 1
                if active:
                    blk_active[pid][block] += 1
                if state is not None:
                    status_polls[pid] += 1
                    if state == "Hospital":
                        hosp_polls[pid] += 1
                    elif state in AWAY_STATES:
                        away_polls[pid] += 1

    profile = [
        (slot_active[i] / slot_polls[i]) if slot_polls[i] else None for i in range(SLOTS_PER_DAY)
    ]
    filled = [p for p in profile if p is not None]
    avg_active = sum(filled) / len(filled) if filled else 0
    profile_ready = [
        (slot_ready[i] / slot_polls[i]) if slot_polls[i] else None for i in range(SLOTS_PER_DAY)
    ]
    filled_ready = [p for p in profile_ready if p is not None]
    avg_ready = sum(filled_ready) / len(filled_ready) if filled_ready else 0
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

    player_blocks = {}
    for pid in roster:
        player_blocks[pid] = [
            (blk_active[pid][b] / blk_polls[pid][b]) if blk_polls[pid][b] else None for b in range(BLOCKS_PER_DAY)
        ]

    # share of polls each member spent in hospital / out of the country
    player_status = {
        pid: {"hosp": hosp_polls[pid] / status_polls[pid], "away": away_polls[pid] / status_polls[pid]}
        for pid in roster
        if status_polls[pid]
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
        "profile_ready": profile_ready,
        "avg_ready": avg_ready,
        "ready_ratio": avg_ready / roster_size,
        "player_status": player_status,
        "windows": windows,
        "player_blocks": player_blocks,
        "roster_size": len(roster),
    }
