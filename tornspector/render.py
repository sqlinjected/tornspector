"""builds the html report. print -> save as pdf for a pdf (A4 landscape)"""

import datetime as dt
from html import escape

LEVEL_COLOR = {"LOW": "#2f855a", "MODERATE": "#b7791f", "HIGH": "#c53030", "SEVERE": "#742a2a"}


def fmt_bs(n: float | None) -> str:
    if not n:
        return "-"
    for unit, size in (("b", 1e9), ("m", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.2f}{unit}"
    return f"{n:.0f}"


def fmt_member_bs(member) -> str:
    total = fmt_bs(member.bs_estimate)
    if member.bs_source == "spy" and total != "-":
        return f"{total} (spy)"
    return total


def _pct(x, digits=1):
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _num(x, digits=1):
    return "-" if x is None else f"{x:,.{digits}f}"


def _yn(x):
    return "-" if x is None else ("Yes" if x else "No")


def _name(m):
    return (
        f'<a href="https://www.torn.com/profiles.php?XID={m.id}">{escape(m.name)}</a>'
        f'<span class="pid">[{m.id}]</span>'
    )


def _kpi(label, value, note=""):
    note_html = f'<div class="kpi-note">{escape(note)}</div>' if note else ""
    return f'<div class="kpi"><div class="kpi-label">{escape(label)}</div><div class="kpi-value">{value}</div>{note_html}</div>'


def _heat(v):
    if v is None:
        return '<td class="heat na">·</td>'
    alpha = min(1.0, v / 0.35)  # 35%+ observed = full color
    return f'<td class="heat" style="background:rgba(197,48,48,{alpha:.2f});color:{"#fff" if alpha > .55 else "#1a202c"}">{v * 100:.0f}%</td>'


def _bar_chart(profile):
    width, height, pad = 960, 170, 28
    values = [v or 0 for v in profile]
    top = max(values) or 1
    bar_w = (width - pad) / len(values)
    bars = []
    for i, v in enumerate(values):
        bar_h = (v / top) * (height - 30)
        x = pad + i * bar_w
        bars.append(
            f'<rect x="{x:.1f}" y="{height - 20 - bar_h:.1f}" width="{bar_w - 1.2:.1f}" height="{bar_h:.1f}" fill="#c53030" opacity="0.85"><title>{i // 4:02d}:{(i % 4) * 15:02d} TCT - {v:.2f}</title></rect>'
        )
    labels = "".join(
        f'<text x="{pad + i * 4 * bar_w:.1f}" y="{height - 5}" font-size="10" fill="#4a5568">{i:02d}</text>'
        for i in range(0, 24, 2)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Average active members per 15-minute slot">'
        f'<line x1="{pad}" y1="{height - 20}" x2="{width}" y2="{height - 20}" stroke="#a0aec0"/>'
        f'<text x="0" y="12" font-size="10" fill="#4a5568">{top:.1f}</text>'
        + "".join(bars)
        + labels
        + "</svg>"
    )


def render(result: dict, activity: dict | None = None, author: str = "") -> str:
    faction = result["faction"]
    summary = result["summary"]
    members = result["members"]
    generated = dt.datetime.fromtimestamp(result["generated_at"], dt.timezone.utc)
    title = f"{faction.get('name', 'Faction')} - Enemy Historical Intelligence - {generated:%Y-%m-%d}"
    level_color = LEVEL_COLOR.get(summary["threat_level"], "#c53030")

    top15_note = "" if summary["top15"] is not None else "roster too small"
    kpis = "".join(
        [
            _kpi("Expected active roster", str(summary["expected_active"])),
            _kpi("Avg faction hits / war", _num(summary["avg_hits"])),
            _kpi("Peak faction hits", f"{summary['peak_hits']:,}"),
            _kpi("Avg hits, winning wars", _num(summary["avg_hits_win"])),
            _kpi("Avg hits, losing wars", _num(summary["avg_hits_loss"])),
            _kpi("Loss persistence", _pct(summary["persistence_raw"])),
            _kpi("Loss persistence (per hour)", _pct(summary["persistence_adj"])),
            _kpi("Top 5 hit share", _pct(summary["top5"])),
            _kpi("Top 10 hit share", _pct(summary["top10"]) if summary["top10"] is not None else "-",
                 "" if summary["top10"] is not None else "roster too small"),
            _kpi("Top 15 hit share", _pct(summary["top15"]) if summary["top15"] is not None else "Not shown", top15_note),
            _kpi("Heavy hitters", str(summary["heavy_count"])),
            _kpi("Heavy hitter attendance", _pct(summary["heavy_attendance"], 0)),
            _kpi("Low / no-show members", str(summary["low_count"])),
            _kpi("Revivable now", f"{summary['revivable']}/{summary['roster_size']}"),
        ]
    )

    summary_lines = [
        f"{summary['wars_analysed']} wars ({summary['wins']}W / {summary['losses']}L). Usually about {summary['expected_active']} people hitting.",
        f"Peak {summary['peak_hits']:,} hits. Avg {_num(summary['avg_hits_win'])} in wins, {_num(summary['avg_hits_loss'])} in losses.",
        f"Top 5 do {_pct(summary['top5'])} of the hits"
        + (f", top 10 do {_pct(summary['top10'])}." if summary["top10"] is not None else "."),
        f"{summary['heavy_count']} heavy hitters, {_pct(summary['heavy_attendance'], 0)} attendance. "
        f"{summary['surge_count']} surge, {summary['low_count']} low/no-show.",
        f"{summary['revivable']}/{summary['roster_size']} revivable.",
    ]
    if summary["new_count"]:
        summary_lines.append(f"{summary['new_count']} new, no war history yet.")
    for m in result["unreliable"]:
        summary_lines.append(
            f"Watch out for {escape(m.name)}: {fmt_member_bs(m)} battle stats but only "
            f"{_pct(m.participation, 0)} attendance."
        )
    if not summary["has_estimates"]:
        summary_lines.append("No battle stats loaded.")
    summary_html = "".join(f"<li>{line}</li>" for line in summary_lines)

    heavy_html = "".join(
        f"<li><b>{escape(m.name)}</b> ({m.role}) - {fmt_member_bs(m)} battle stats - "
        f"{_pct(m.participation, 0)} attendance - revivable: {_yn(m.revivable)}</li>"
        for m in result["heavy"]
    ) or "<li>None.</li>"

    by_threat = sorted(members, key=lambda m: m.threat, reverse=True)
    core = by_threat[:15]
    core_rows = "".join(
        f"<tr><td>{_name(m)}</td><td class='r'>{fmt_member_bs(m)}</td><td class='r'><b>{m.threat:.1f}</b></td>"
        f"<td>{m.role}</td><td class='r'>{_pct(m.participation, 0)}</td><td>{_yn(m.revivable)}</td></tr>"
        for m in core
    )

    by_bs = sorted(members, key=lambda m: (m.bs_estimate is None, -(m.bs_estimate or 0)))
    roster_rows = "".join(
        f"<tr><td class='r'>{i}</td><td>{_name(m)}</td><td>{m.role}</td><td class='r'>{m.threat:.1f}</td>"
        f"<td class='r'>{fmt_member_bs(m)}</td><td class='r'>{m.eligible}</td><td class='r'>{m.active}</td>"
        f"<td class='r'>{_pct(m.participation)}</td><td class='r'>{_pct(m.no_show)}</td>"
        f"<td class='r'>{m.avg_per_war:.1f}</td><td class='r'>{m.avg_per_active:.1f}</td>"
        f"<td class='r'>{_pct(m.hit_share)}</td><td>{m.trend}</td><td>{_yn(m.revivable)}</td></tr>"
        for i, m in enumerate(by_bs, 1)
    )

    power_rows = "".join(
        f"<tr><td>{_name(m)}</td><td class='r'>{m.days_in_faction}</td><td class='r'>{m.peak}</td>"
        f"<td class='r'>{_num(m.avg_win)}</td><td class='r'>{_num(m.avg_loss)}</td></tr>"
        for m in by_bs
    )

    war_rows = "".join(
        f"<tr><td class='r'>{w['n']}</td><td>{dt.datetime.fromtimestamp(w['start'], dt.timezone.utc):%d %b %Y}</td>"
        f"<td>{escape(w['opponent'])}</td><td class='res {w['result'].lower()}'>{w['result']}</td>"
        f"<td class='r'>{w['duration_h']:.1f}</td><td class='r'>{w['roster_hits']}</td><td class='r'>{w['all_hits']}</td>"
        f"<td class='r'>{w['hits_per_hour']:.1f}</td><td class='r'>{w['energy']:,}</td>"
        f"<td class='r'>{w['our_score']:,}</td><td class='r'>{w['opp_score']:,}</td></tr>"
        for w in result["wars"]
    )

    if activity:
        window_rows = "".join(
            f"<tr><td>{w['label']}</td><td>{w['period']}</td><td class='r'>{w['avg']:.1f}</td><td class='r'>{_pct(w['pct'])}</td></tr>"
            for w in activity["windows"]
        )
        blocks_head = "".join(f"<th>{h:02d}-{h + 2:02d}</th>" for h in range(0, 24, 2))
        heat_rows = "".join(
            f"<tr><td>{escape(m.name)}</td><td class='r'>{m.threat:.1f}</td><td class='r'>{fmt_member_bs(m)}</td>"
            + "".join(_heat(v) for v in activity["player_blocks"].get(m.id, [None] * 12))
            + "</tr>"
            for m in by_threat[:10]
        )
        partial = (
            f" Only {activity['span_days']:.1f} days of data so far."
            if activity["span_days"] < 7
            else ""
        )
        activity_html = f"""
<section class="page">
  <h2>Activity</h2>
  <p class="sub">Last {activity['days']} days, {activity['polls']:,} polls. TCT.{partial}</p>
  <div class="two">
    <table><thead><tr><th>Sustained window</th><th>TCT period</th><th>Avg active</th><th>Of roster</th></tr></thead><tbody>{window_rows}</tbody></table>
    <div class="box">
      <div><b>{activity['avg_bucket']:.1f}</b> average active per 15-min slot</div>
      <div><b>{_pct(activity['avg_ratio'])}</b> average observed ratio</div>
      <div><b>{activity['peak_single']}</b> peak in a single poll</div>
      <div><b>{activity['roster_size']}</b> members tracked</div>
    </div>
  </div>
  <h3>Average active members by time of day (TCT)</h3>
  {_bar_chart(activity['profile'])}
</section>
<section class="page">
  <h2>Key threat activity</h2>
  <p class="sub">% of polls they were active in each 2h block (TCT). Top 10 by threat.</p>
  <table class="heatmap"><thead><tr><th>Player</th><th>Threat</th><th>BS</th>{blocks_head}</tr></thead><tbody>{heat_rows}</tbody></table>
</section>"""
    else:
        activity_html = """
<section class="page">
  <h2>Activity</h2>
  <p class="sub">No activity data yet. Run <code>python -m tornspector watch FACTION_ID</code> for a while first.</p>
</section>"""

    author_html = f"<div class='by'>Prepared by {escape(author)}</div>" if author else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}</style></head>
<body>
<header class="cover">
  <div class="eyebrow">tornspector</div>
  <h1>{escape(faction.get('name', ''))} <span class="fid">[{faction.get('id', '')}]</span></h1>
  <div class="meta">{summary['wars_analysed']} wars analysed | Generated {generated:%d %b %Y %H:%M} TCT</div>
  {author_html}
</header>

<section>
  <div class="level" style="border-color:{level_color}">
    <span>Threat level</span><b style="background:{level_color}">{summary['threat_level']}</b>
  </div>
  <div class="kpis">{kpis}</div>
</section>

<section>
  <h2>Summary</h2>
  <ul class="exec">{summary_html}</ul>
  <h3>Heavy hitters</h3>
  <ul class="exec">{heavy_html}</ul>
</section>

<section class="page">
  <h2>Core 15</h2>
  <p class="sub">Top 15 by war threat.</p>
  <table><thead><tr><th>Player</th><th>Battle stats</th><th>War Threat</th><th>Role</th><th>Attendance</th><th>Revivable</th></tr></thead>
  <tbody>{core_rows}</tbody></table>
</section>

<section class="page">
  <h2>Roster</h2>
  <table class="dense"><thead><tr><th>#</th><th>Player</th><th>Role</th><th>Threat</th><th>BS</th><th>Eligible</th><th>Active</th>
  <th>Participation</th><th>No-show</th><th>Avg/war</th><th>Avg/active</th><th>Hit share</th><th>Trend</th><th>Revivable</th></tr></thead>
  <tbody>{roster_rows}</tbody></table>
</section>

<section class="page">
  <h2>Hitting power</h2>
  <table><thead><tr><th>Player</th><th>Days in faction</th><th>Peak hits</th><th>Avg hits, wins</th><th>Avg hits, losses</th></tr></thead>
  <tbody>{power_rows}</tbody></table>
</section>

<section class="page">
  <h2>{summary['wars_analysed']}-war output matrix</h2>
  <table><thead><tr><th>War</th><th>Started</th><th>Opponent</th><th>Result</th><th>Hours</th><th>Roster hits</th><th>All hits</th>
  <th>Hits/hour</th><th>Energy*</th><th>Our score</th><th>Opp. score</th></tr></thead>
  <tbody>{war_rows}</tbody></table>
  <div class="box wide">
    <div><b>Wins:</b> {_num(summary['avg_hits_win'])} avg hits ({_num(summary['hph_win'])} hits/hour)</div>
    <div><b>Losses:</b> {_num(summary['avg_hits_loss'])} avg hits ({_num(summary['hph_loss'])} hits/hour)</div>
    <div><b>Persistence:</b> raw {_pct(summary['persistence_raw'])} | duration-adjusted {_pct(summary['persistence_adj'])}</div>
  </div>
  <p class="foot">War 1 = oldest. Roster hits = current members only. *Energy = hits x 25.</p>
</section>
{activity_html}
<section class="page notes">
  <h2>Notes</h2>
  <ul class="exec">
    <li>Only wars a member was in the faction for count toward their numbers.</li>
    <li>Values marked (spy) are imported snapshots. Other live battle stats are FFScouter estimates.</li>
    <li>Threat level: under 100 avg hits/war LOW, 100-249 MODERATE, 250-499 HIGH, 500+ SEVERE.</li>
  </ul>
</section>
</body></html>"""


CSS = """
:root{--ink:#1a202c;--muted:#4a5568;--line:#e2e8f0;--navy:#1a2238;--accent:#c53030}
*{box-sizing:border-box}
body{font:13px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:#f7f8fa}
section,header{max-width:1100px;margin:0 auto;padding:22px 28px;background:#fff}
.cover{background:var(--navy);color:#fff;padding:34px 28px 26px}
.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:11px;color:#f6ad55}
h1{font-size:30px;margin:6px 0}
.fid,.pid{color:#a0aec0;font-weight:400;font-size:.65em;margin-left:4px}
.meta,.by{color:#cbd5e0}
h2{font-size:18px;margin:0 0 4px;text-transform:uppercase;letter-spacing:.06em;border-bottom:2px solid var(--accent);padding-bottom:4px}
h3{font-size:14px;margin:18px 0 6px}
.sub{color:var(--muted);margin:2px 0 12px}
.level{display:flex;justify-content:space-between;align-items:center;border-left:6px solid;padding:10px 14px;background:#fff5f5;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.level b{color:#fff;padding:4px 14px;border-radius:3px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-top:14px}
.kpi{border:1px solid var(--line);border-radius:4px;padding:10px}
.kpi-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.kpi-value{font-size:22px;font-weight:700;margin-top:2px}
.kpi-note{font-size:10.5px;color:var(--muted)}
.exec li{margin:4px 0}
table{width:100%;border-collapse:collapse;margin:6px 0}
th{background:var(--navy);color:#fff;font-weight:600;text-align:left;padding:6px 8px;font-size:11.5px}
td{padding:5px 8px;border-bottom:1px solid var(--line)}
tbody tr:nth-child(even){background:#fafbfc}
.dense td,.dense th{padding:4px 5px;font-size:11.5px}
.r{text-align:right;font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration:none}
.res.win{color:#2f855a;font-weight:600}.res.loss{color:var(--accent);font-weight:600}
.foot{font-size:11px;color:var(--muted);margin-top:8px}
.box{border:1px solid var(--line);border-left:4px solid var(--accent);padding:10px 14px;background:#fffafa}
.box div{margin:3px 0}
.box.wide{margin-top:10px}
.two{display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start}
.chart{width:100%;height:auto}
.heatmap td.heat{text-align:center;font-size:11px;padding:4px 2px}
.heatmap td.na{color:#cbd5e0}
.notes dt{font-weight:700;margin-top:10px}.notes dd{margin:2px 0 0 0;color:var(--muted)}
code{background:#edf2f7;padding:1px 5px;border-radius:3px}
@page{size:A4 landscape;margin:12mm}
@media print{
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  section,header{max-width:none;padding:10px 0}
  .cover{padding:18px}
  .page{break-before:page}
  tr{break-inside:avoid}
}
"""
