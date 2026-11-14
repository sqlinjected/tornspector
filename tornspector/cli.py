"""tornspector report / watch / demo"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from . import activity, analysis, ffscouter, render
from .torn import TornAPIError, TornClient


def load_dotenv(path: str = ".env") -> None:
    # small .env loader so theres no dependencies
    env_file = Path(path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _safe_filename(name: str) -> str:
    # strip the characters windows wont allow in a filename
    return re.sub(r'[<>:"/\\|?*]+', "", name).strip()


def write_outputs(result, act, out_dir: Path, author: str, as_json: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    html = render.render(result, act, author=author)
    name = result["faction"].get("name", str(result["faction"].get("id")))
    stamp = time.strftime("%Y-%m-%d", time.gmtime(result["generated_at"]))
    base = out_dir / _safe_filename(f"{name} - Enemy Historical Intelligence - {stamp}")
    html_path = base.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    if as_json:
        report_json = analysis.to_jsonable(result)
        report_json["activity"] = act
        base.with_suffix(".json").write_text(json.dumps(report_json, indent=2, default=str), encoding="utf-8")
    return html_path


def cmd_report(args) -> int:
    key = os.environ.get("TORN_API_KEY", "")
    if not key:
        print("no TORN_API_KEY (put it in .env)", file=sys.stderr)
        return 2
    client = TornClient(key, cache_dir=args.cache)
    fid = args.faction_id
    now = int(time.time())

    print(f"Loading faction {fid}...")
    basic = client.faction_basic(fid)
    faction = {"id": fid, "name": basic.get("name", str(fid)), "tag": basic.get("tag", "")}
    raw_members = client.faction_members(fid)
    print(f"  {faction['name']}: {len(raw_members)} members")

    # ask for a few extra, unfinished wars get filtered out below. api max is 100
    wars = client.ranked_wars(fid, limit=min(100, args.wars + 10))
    finished = [w for w in wars if w.get("end") and int(w["end"]) <= now]
    finished.sort(key=lambda w: int(w["start"]), reverse=True)
    finished = finished[: args.wars]
    if not finished:
        print("no finished ranked wars", file=sys.stderr)
        return 1

    parsed = []
    for i, w in enumerate(finished, 1):
        print(f"  war report {i}/{len(finished)} (id {w['id']})")
        war = analysis.parse_war(client.ranked_war_report(int(w["id"])), fid)
        if war:
            parsed.append(war)

    estimates = {}
    ff_key = os.environ.get("FFSCOUTER_KEY") or key
    if args.ffscouter and not args.no_ffscouter:
        try:
            estimates = ffscouter.get_stats(ff_key, [m["id"] for m in raw_members])
            print(f"  FFScouter estimates: {len(estimates)}/{len(raw_members)}")
        except Exception as exc:  # estimates are optional
            print(f"  FFScouter skipped: {exc}")

    members = [analysis.parse_member(m, estimates.get(int(m["id"]))) for m in raw_members]
    result = analysis.analyse(faction, members, parsed, now)

    act = None
    if Path(args.activity_db).exists():
        con = activity.connect(args.activity_db)
        act = activity.summarise(con, fid, [m.id for m in members], days=args.days, now=now)

    path = write_outputs(result, act, Path(args.out), args.author, args.json)
    print(f"Done in {client.calls} Torn API calls -> {path}")
    return 0


def cmd_watch(args) -> int:
    key = os.environ.get("TORN_API_KEY", "")
    if not key:
        print("no TORN_API_KEY (put it in .env)", file=sys.stderr)
        return 2
    client = TornClient(key, cache_dir=None)
    con = activity.connect(args.activity_db)
    print(f"watching {', '.join(map(str, args.faction_ids))} every {args.interval // 60} min")
    while True:
        ts = int(time.time())
        for fid in args.faction_ids:
            try:
                n = activity.record_poll(con, fid, client.faction_members(fid), ts)
                print(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))} TCT  faction {fid}: {n} members logged")
            except (TornAPIError, OSError) as exc:
                print(f"  poll failed for {fid}: {exc}", file=sys.stderr)
        if args.once:
            return 0
        # sleep till the next 15 min mark so polls line up
        time.sleep(args.interval - (time.time() % args.interval) + 2)


def cmd_demo(args) -> int:
    from .demo import build_demo

    result, act = build_demo()
    path = write_outputs(result, act, Path(args.out), "demo data (fake faction)", args.json)
    print(f"Sample report -> {path}")
    return 0


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="tornspector", description="torn war intel")
    sub = parser.add_subparsers(dest="cmd", required=True)

    report_p = sub.add_parser("report", help="make a report")
    report_p.add_argument("faction_id", type=int)
    report_p.add_argument("--wars", type=int, default=10, help="number of wars (default 10)")
    report_p.add_argument("--out", default="reports", help="output folder")
    report_p.add_argument("--author", default="", help="your name for the cover")
    report_p.add_argument("--json", action="store_true", help="also save json")
    report_p.add_argument("--ffscouter", action="store_true", help="include FFScouter battle-stat estimates")
    report_p.add_argument("--no-ffscouter", action="store_true", help="skip ffscouter")
    report_p.add_argument("--activity-db", default="data/activity.db")
    report_p.add_argument("--days", type=int, default=14, help="activity days (default 14)")
    report_p.add_argument("--cache", default="cache", help="cache folder")
    report_p.set_defaults(func=cmd_report)

    watch_p = sub.add_parser("watch", help="track activity")
    watch_p.add_argument("faction_ids", type=int, nargs="+")
    watch_p.add_argument("--interval", type=int, default=900, help="seconds between polls")
    watch_p.add_argument("--activity-db", default="data/activity.db")
    watch_p.add_argument("--once", action="store_true", help="poll once and exit")
    watch_p.set_defaults(func=cmd_watch)

    demo_p = sub.add_parser("demo", help="fake data report")
    demo_p.add_argument("--out", default="reports")
    demo_p.add_argument("--json", action="store_true")
    demo_p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except TornAPIError as exc:
        print(exc, file=sys.stderr)
        if exc.code == 2:
            print("bad key?", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
