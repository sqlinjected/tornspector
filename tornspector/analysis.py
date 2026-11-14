"""report math"""

import statistics
from dataclasses import asdict, dataclass, field

ENERGY_PER_ATTACK = 25
DAY = 86400
HEAVY_THREAT = 70
REGULAR_PARTICIPATION = 0.6

TREND_VALUE = {"Improving": 1.0, "Stable": 0.5, "Declining": 0.0}

# war threat weights (add up to 1)
THREAT_WEIGHTS = {
    "stats": 0.25,  # estimated battle stats, ranked within the roster
    "volume": 0.25,  # average hits per eligible war vs. the busiest member
    "participation": 0.20,
    "share": 0.15,  # faction hit share vs. the biggest share
    "consistency": 0.10,
    "trend": 0.05,
}


@dataclass
class War:
    id: int
    start: int
    end: int
    opponent_id: int
    opponent_name: str
    our_score: int
    opp_score: int
    our_attacks: int  # every hit the faction made, incl. people who later left
    won: bool | None
    hits: dict[int, int]  # player_id -> war hits

    @property
    def duration_h(self) -> float:
        return max((self.end - self.start) / 3600, 0.01)

    @property
    def result(self) -> str:
        return "Unknown" if self.won is None else ("Win" if self.won else "Loss")


@dataclass
class Member:
    id: int
    name: str
    level: int = 0
    position: str = ""
    days_in_faction: int = 0
    revivable: bool | None = None
    last_action: str = ""
    state: str = ""
    bs_estimate: float | None = None
    fair_fight: float | None = None
    # filled in by analyse()
    per_war: list = field(default_factory=list)  # hits per war, None = not eligible
    eligible: int = 0
    active: int = 0
    participation: float = 0.0
    no_show: float = 0.0
    total_hits: int = 0
    avg_per_war: float = 0.0
    avg_per_active: float = 0.0
    hit_share: float = 0.0
    peak: int = 0
    avg_win: float | None = None
    avg_loss: float | None = None
    trend: str = "n/a"
    consistency: float = 0.0
    threat: float = 0.0
    role: str = ""
    bs_source: str | None = None


def parse_war(report: dict, faction_id: int) -> War | None:
    factions = report.get("factions") or []
    ours = next((f for f in factions if f["id"] == faction_id), None)
    opp = next((f for f in factions if f["id"] != faction_id), None)
    if not ours or not opp or not report.get("end"):
        return None
    winner = report.get("winner")
    won = None if not winner else winner == faction_id
    hits = {m["id"]: m.get("attacks") or 0 for m in ours.get("members") or []}
    return War(
        id=report["id"],
        start=report["start"],
        end=report["end"],
        opponent_id=opp["id"],
        opponent_name=opp.get("name", "?"),
        our_score=ours.get("score") or 0,
        opp_score=opp.get("score") or 0,
        our_attacks=ours.get("attacks") or sum(hits.values()),
        won=won,
        hits=hits,
    )


def parse_member(raw: dict, estimate: dict | None = None) -> Member:
    last_action = raw.get("last_action") or {}
    status = raw.get("status") or {}
    return Member(
        id=int(raw["id"]),
        name=raw.get("name", str(raw["id"])),
        level=int(raw.get("level") or 0),
        position=raw.get("position", ""),
        days_in_faction=int(raw.get("days_in_faction") or 0),
        revivable=raw.get("is_revivable"),
        last_action=last_action.get("relative", ""),
        state=status.get("state", ""),
        bs_estimate=(estimate or {}).get("bs_estimate"),
        fair_fight=(estimate or {}).get("fair_fight"),
        bs_source=(estimate or {}).get("bs_source"),
    )


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _is_eligible(m: Member, war: War, now: int) -> bool:
    # joined before the war started, or has hits in it
    if war.hits.get(m.id, 0) > 0:
        return True
    joined_at = now - m.days_in_faction * DAY
    return joined_at <= war.start


def _trend(series: list[int]) -> str:
    if len(series) < 3:
        return "Too few wars"
    half = len(series) // 2
    older, recent = series[:half], series[-half:]
    old_avg = sum(older) / len(older)
    recent_avg = sum(recent) / len(recent)
    if old_avg == 0:
        return "Improving" if recent_avg > 0 else "Stable"
    ratio = recent_avg / old_avg
    if ratio >= 1.15:
        return "Improving"
    if ratio <= 0.85:
        return "Declining"
    return "Stable"


def _consistency(series: list[int]) -> float:
    # 1 = same hits every war, 0 = all over the place
    if not series:
        return 0.0
    mean = sum(series) / len(series)
    if mean == 0:
        return 0.0
    cv = statistics.pstdev(series) / mean
    return max(0.0, 1 - cv / 1.5)


def threat_level(avg_hits_per_war: float) -> str:
    if avg_hits_per_war >= 500:
        return "SEVERE"
    if avg_hits_per_war >= 250:
        return "HIGH"
    if avg_hits_per_war >= 100:
        return "MODERATE"
    return "LOW"


def _role(m: Member, median_active: float) -> str:
    if m.eligible == 0:
        return "New (no war history)"
    if (
        m.eligible >= 3
        and m.active >= 2
        and m.participation >= REGULAR_PARTICIPATION
        and (
            m.threat >= HEAVY_THREAT
            or (m.hit_share >= 0.10 and m.avg_per_war >= 40)
            or (m.hit_share >= 0.05 and m.avg_per_war >= 70)
        )
    ):
        return "Proven Heavy Hitter"
    if m.eligible <= 2 and m.threat >= HEAVY_THREAT and m.avg_per_war >= 60:
        return "Emerging Heavy Hitter"
    if m.threat >= HEAVY_THREAT:
        return "Heavy Hitter"
    if m.active == 0 or m.participation < 0.25:
        return "Low / No-show"
    if m.participation >= REGULAR_PARTICIPATION:
        return "Core Regular"
    if m.avg_per_active >= max(20, 1.5 * median_active):
        return "Surge Fighter"
    return "Sporadic"


def analyse(faction: dict, members: list[Member], wars: list[War], now: int) -> dict:
    wars = sorted(wars, key=lambda w: w.start)  # War 1 = oldest
    roster_ids = {m.id for m in members}

    for m in members:
        series, wins, losses = [], [], []
        m.per_war = []
        for w in wars:
            if not _is_eligible(m, w, now):
                m.per_war.append(None)
                continue
            h = w.hits.get(m.id, 0)
            m.per_war.append(h)
            series.append(h)
            if w.won is True:
                wins.append(h)
            elif w.won is False:
                losses.append(h)
        m.eligible = len(series)
        m.active = sum(1 for h in series if h > 0)
        m.total_hits = sum(series)
        m.participation = m.active / m.eligible if m.eligible else 0.0
        m.no_show = 1 - m.participation if m.eligible else 0.0
        m.avg_per_war = m.total_hits / m.eligible if m.eligible else 0.0
        m.avg_per_active = m.total_hits / m.active if m.active else 0.0
        m.peak = max(series) if series else 0
        m.avg_win = _mean(wins)
        m.avg_loss = _mean(losses)
        m.trend = _trend(series) if series else "No wars"
        m.consistency = _consistency(series)

    roster_total = sum(m.total_hits for m in members)
    for m in members:
        m.hit_share = m.total_hits / roster_total if roster_total else 0.0

    with_bs = sorted((m for m in members if m.bs_estimate), key=lambda m: m.bs_estimate)
    stat_rank = {}
    for i, m in enumerate(with_bs):
        stat_rank[m.id] = (i + 1) / len(with_bs)
    max_avg = max((m.avg_per_war for m in members), default=0) or 1
    max_share = max((m.hit_share for m in members), default=0) or 1
    for m in members:
        scores = {
            "stats": stat_rank.get(m.id, 0.25),
            "volume": m.avg_per_war / max_avg,
            "participation": m.participation,
            "share": m.hit_share / max_share,
            "consistency": m.consistency,
            "trend": TREND_VALUE.get(m.trend, 0.5),
        }
        if m.eligible == 0:  # no history: stats are all we know
            scores = {k: (v if k == "stats" else 0.0) for k, v in scores.items()}
        m.threat = round(100 * sum(THREAT_WEIGHTS[k] * v for k, v in scores.items()), 1)

    active_avgs = [m.avg_per_active for m in members if m.active]
    median_active = statistics.median(active_avgs) if active_avgs else 0
    for m in members:
        m.role = _role(m, median_active)

    war_rows = []
    for i, w in enumerate(wars, 1):
        roster_hits = sum(h for pid, h in w.hits.items() if pid in roster_ids)
        war_rows.append(
            {
                "n": i,
                "id": w.id,
                "start": w.start,
                "opponent": w.opponent_name,
                "opponent_id": w.opponent_id,
                "result": w.result,
                "duration_h": round(w.duration_h, 1),
                "roster_hits": roster_hits,
                "all_hits": w.our_attacks,
                "hits_per_hour": roster_hits / w.duration_h,
                "energy": roster_hits * ENERGY_PER_ATTACK,
                "our_score": w.our_score,
                "opp_score": w.opp_score,
                "active_members": sum(1 for pid, h in w.hits.items() if pid in roster_ids and h > 0),
            }
        )

    hits_per_war = [r["roster_hits"] for r in war_rows]
    win_rows = [r for r in war_rows if r["result"] == "Win"]
    loss_rows = [r for r in war_rows if r["result"] == "Loss"]
    avg_win = _mean([r["roster_hits"] for r in win_rows])
    avg_loss = _mean([r["roster_hits"] for r in loss_rows])
    hph_win = _mean([r["hits_per_hour"] for r in win_rows])
    hph_loss = _mean([r["hits_per_hour"] for r in loss_rows])

    ranked = sorted(members, key=lambda m: m.total_hits, reverse=True)

    def top_share(n):
        if not roster_total:
            return None
        return sum(m.total_hits for m in ranked[:n]) / roster_total

    heavy = [m for m in members if "Heavy Hitter" in m.role]
    heavy.sort(key=lambda m: m.threat, reverse=True)
    # cutoff for the top third by est. stats
    bs_cut = None
    if len(with_bs) >= 3:
        bs_cut = with_bs[len(with_bs) * 2 // 3].bs_estimate
    elif with_bs:
        bs_cut = with_bs[-1].bs_estimate
    # strong on paper but skips wars
    unreliable = [
        m
        for m in members
        if bs_cut
        and m.bs_estimate
        and m.bs_estimate >= bs_cut
        and m.eligible >= 3
        and m.participation < REGULAR_PARTICIPATION
    ]

    avg_hits = _mean(hits_per_war) or 0.0
    summary = {
        "threat_level": threat_level(avg_hits),
        "wars_analysed": len(wars),
        "roster_size": len(members),
        "expected_active": round(_mean([r["active_members"] for r in war_rows]) or 0),
        "avg_hits": avg_hits,
        "peak_hits": max(hits_per_war) if hits_per_war else 0,
        "avg_hits_win": avg_win,
        "avg_hits_loss": avg_loss,
        "hph_win": hph_win,
        "hph_loss": hph_loss,
        "wins": len(win_rows),
        "losses": len(loss_rows),
        "persistence_raw": (avg_loss / avg_win) if avg_win and avg_loss is not None else None,
        "persistence_adj": (hph_loss / hph_win) if hph_win and hph_loss is not None else None,
        "top5": top_share(5),
        "top10": top_share(10) if len(members) > 10 else None,
        "top15": top_share(15) if len(members) > 20 else None,
        "heavy_count": len(heavy),
        "heavy_attendance": _mean([m.participation for m in heavy]),
        "heavy_reliable": sum(1 for m in heavy if m.participation >= 0.8),
        "surge_count": sum(1 for m in members if m.role == "Surge Fighter"),
        "low_count": sum(1 for m in members if m.role == "Low / No-show"),
        "new_count": sum(1 for m in members if m.eligible == 0),
        "revivable": sum(1 for m in members if m.revivable),
        "has_estimates": bool(with_bs),
    }

    return {
        "faction": faction,
        "generated_at": now,
        "summary": summary,
        "members": members,
        "heavy": heavy,
        "unreliable": unreliable,
        "wars": war_rows,
    }


def to_jsonable(result: dict) -> dict:
    jsonable = dict(result)
    jsonable["members"] = [asdict(m) for m in result["members"]]
    jsonable["heavy"] = [m.id for m in result["heavy"]]
    jsonable["unreliable"] = [m.id for m in result["unreliable"]]
    return jsonable
