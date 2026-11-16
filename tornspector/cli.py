"""tornspector report / watch / demo"""

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

from . import activity, analysis, ffscouter, render
from .torn import TornAPIError, TornClient


def load_dotenv(path=".env"):
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


def write_outputs(result, act, out_dir, author, as_json):
    out_dir.mkdir(parents=True, exist_ok=True)
    html = render.render(result, act, author=author)
    name = result["faction"].get("name", str(result["faction"].get("id")))
    stamp = time.strftime("%Y-%m-%d", time.gmtime(result["generated_at"]))
    filename = f"{name} - Enemy Historical Intelligence - {stamp}"
    # windows wont allow these in a filename
    filename = re.sub(r'[<>:"/\\|?*]+', "", filename).strip()
    base = out_dir / filename
    html_path = base.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    if as_json:
        report_json = analysis.to_jsonable(result)
        report_json["activity"] = act
        base.with_suffix(".json").write_text(json.dumps(report_json, indent=2, default=str), encoding="utf-8")
    return html_path


def load_spies(path):
    spies = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = csv.DictReader(file, strict=True)
        if not rows.fieldnames or not {"player_id", "total"} <= set(rows.fieldnames):
            raise ValueError("expected player_id and total columns")
        if len(set(rows.fieldnames)) != len(rows.fieldnames):
            raise ValueError("CSV has duplicate column names")

        for row in rows:
            try:
                player_id = int(row["player_id"])
                total = int(row["total"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"line {rows.line_num}: use whole numbers") from exc

            if None in row or player_id <= 0 or total <= 0:
                raise ValueError(f"line {rows.line_num}: check the player ID and total")
            if player_id in spies:
                raise ValueError(f"line {rows.line_num}: duplicate player ID {player_id}")

            spies[player_id] = {"bs_estimate": total, "bs_source": "spy"}

    return spies


def cmd_report(args):
    try:
        estimates = load_spies(args.spies) if args.spies else {}
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Could not read spy CSV: {exc}", file=sys.stderr)
        return 2

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

    if args.ffscouter and not args.no_ffscouter:
        missing_ids = [int(m["id"]) for m in raw_members if int(m["id"]) not in estimates]
        if missing_ids:
            ff_key = os.environ.get("FFSCOUTER_KEY") or key
            try:
                scouted = ffscouter.get_stats(ff_key, missing_ids)
                estimates = {**scouted, **estimates}
                print(f"  FFScouter estimates: {len(scouted)}/{len(missing_ids)}")
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


def cmd_watch(args):
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
                when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))
                print(f"{when} TCT  faction {fid}: {n} members logged")
            except (TornAPIError, OSError) as exc:
                print(f"  poll failed for {fid}: {exc}", file=sys.stderr)
        if args.once:
            return 0
        # sleep till the next 15 min mark so polls line up
        time.sleep(args.interval - (time.time() % args.interval) + 2)


def cmd_export(args):
    if not Path(args.activity_db).exists():
        print(f"no activity log at {args.activity_db}, run watch first", file=sys.stderr)
        return 1
    con = activity.connect(args.activity_db)
    out = args.out or Path("data") / f"activity-{args.faction_id}.csv"
    n = activity.export_csv(con, args.faction_id, out)
    if not n:
        print(f"nothing logged for faction {args.faction_id} yet", file=sys.stderr)
        return 1
    print(f"{n} rows -> {out}")
    return 0


def cmd_import(args):
    if not args.csv_file.exists():
        print(f"Could not find {args.csv_file}", file=sys.stderr)
        return 2
    con = activity.connect(args.activity_db)
    try:
        n = activity.import_csv(con, args.csv_file)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Could not read activity CSV: {exc}", file=sys.stderr)
        return 2
    print(f"added {n} rows to {args.activity_db}")
    return 0


def cmd_demo(args):
    from .demo import build_demo

    result, act = build_demo()
    path = write_outputs(result, act, Path(args.out), "demo data (fake faction)", args.json)
    print(f"Sample report -> {path}")
    return 0


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(prog="tornspector", description="torn war intel")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("report", help="make a report")
    p.add_argument("faction_id", type=int)
    p.add_argument("--wars", type=int, default=10, help="number of wars (default 10)")
    p.add_argument("--out", default="reports", help="output folder")
    p.add_argument("--author", default="", help="your name for the cover")
    p.add_argument("--json", action="store_true", help="also save json")
    p.add_argument("--spies", type=Path, help="local CSV with player_id and total")
    p.add_argument("--ffscouter", action="store_true", help="include FFScouter battle-stat estimates")
    p.add_argument("--no-ffscouter", action="store_true", help="skip ffscouter")
    p.add_argument("--activity-db", default="data/activity.db")
    p.add_argument("--days", type=int, default=14, help="activity days (default 14)")
    p.add_argument("--cache", default="cache", help="cache folder")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("watch", help="track activity")
    p.add_argument("faction_ids", type=int, nargs="+")
    p.add_argument("--interval", type=int, default=900, help="seconds between polls")
    p.add_argument("--activity-db", default="data/activity.db")
    p.add_argument("--once", action="store_true", help="poll once and exit")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("export", help="save the activity log as csv")
    p.add_argument("faction_id", type=int)
    p.add_argument("--out", type=Path, help="csv path (default data/activity-ID.csv)")
    p.add_argument("--activity-db", default="data/activity.db")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="add an activity csv to the log")
    p.add_argument("csv_file", type=Path)
    p.add_argument("--activity-db", default="data/activity.db")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("demo", help="fake data report")
    p.add_argument("--out", default="reports")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_demo)

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
