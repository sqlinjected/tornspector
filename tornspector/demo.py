"""fake faction for the demo report + tests"""

import random
import sqlite3

from . import activity, analysis

NAMES = [
    "IronVesper", "Kettlebell", "NightOwl_7", "Quillon", "RustCoyote", "Saffron",
    "TinMarauder", "Umbra", "Velvetine", "Wraithling", "Xylo", "YarrowJack", "ZincFox",
]
OPPONENTS = [
    "Glass Lantern", "Red Harbor", "The Quiet Ones", "Copper Saints", "Night Market",
    "Static Union", "Paper Tigers", "North Gate", "Velvet Hammer", "Low Tide",
]
FACTION_ID = 99999
NOW = 1_794_000_000  # fixed so the demo is reproducible


def build_demo(seed: int = 7):
    rng = random.Random(seed)
    raw_members, profiles = [], {}
    for i, name in enumerate(NAMES):
        pid = 3_000_000 + i * 1111
        bs = 10 ** rng.uniform(5.2, 7.6)
        attend = rng.choice([1.0, 1.0, 0.95, 0.85, 0.7, 0.45, 0.3])
        rate = rng.uniform(5, 70) * (1.4 if bs > 5e6 else 1)
        days = rng.choice([900, 600, 400, 300, 250, 120, 40])
        profiles[pid] = (attend, rate, rng.randint(0, 23))
        raw_members.append(
            {
                "id": pid, "name": name, "level": rng.randint(30, 100), "position": "Member",
                "days_in_faction": days, "is_revivable": rng.random() < 0.2,
                "last_action": {"status": "Offline", "timestamp": NOW - 3600, "relative": "1 hour ago"},
                "status": {"state": "Okay"},
                "_bs": bs,
            }
        )

    wars = []
    start = NOW - 10 * 9 * 86400
    for n in range(10):
        dur = rng.uniform(15, 100) * 3600
        won = rng.random() < 0.4
        hits = {}
        for m in raw_members:
            joined = NOW - m["days_in_faction"] * 86400
            if joined > start:
                continue
            attend, rate, _ = profiles[m["id"]]
            if rng.random() < attend:
                scale = (1.1 if won else 0.75) * (dur / (60 * 3600)) ** 0.5
                hits[m["id"]] = max(1, int(rng.gauss(rate * scale, rate * 0.25)))
        wars.append(
            analysis.War(
                id=20000 + n, start=int(start), end=int(start + dur),
                opponent_id=50000 + n, opponent_name=OPPONENTS[n],
                our_score=int(sum(hits.values()) * rng.uniform(4, 6) * (1.3 if won else 0.8)),
                opp_score=0, our_attacks=sum(hits.values()) + rng.randint(0, 40),
                won=won, hits=hits,
            )
        )
        w = wars[-1]
        w.opp_score = int(w.our_score * (rng.uniform(0.4, 0.8) if won else rng.uniform(1.2, 2.2)))
        start += 9 * 86400

    members = [
        analysis.parse_member(m, {"bs_estimate": m["_bs"], "fair_fight": None}) for m in raw_members
    ]
    faction = {"id": FACTION_ID, "name": "Example Syndicate", "tag": "EXS"}
    result = analysis.analyse(faction, members, wars, NOW)

    # 14 days of 15-minute polls with each player active around a "home" hour
    con = sqlite3.connect(":memory:")
    con.executescript(activity.SCHEMA)
    ts = NOW - 14 * 86400
    while ts <= NOW:
        hour = (ts % 86400) / 3600
        snap = []
        for m in raw_members:
            _, _, home = profiles[m["id"]]
            dist = min(abs(hour - home), 24 - abs(hour - home))
            p = 0.35 * max(0.0, 1 - dist / 7) + 0.03
            active = rng.random() < p
            snap.append(
                {
                    "id": m["id"], "name": m["name"],
                    "last_action": {"status": "Online" if active else "Offline",
                                    "timestamp": ts - 60 if active else ts - 7200},
                }
            )
        activity.record_poll(con, FACTION_ID, snap, ts)
        ts += activity.SLOT
    act = activity.summarise(con, FACTION_ID, [m.id for m in members], now=NOW)
    return result, act
