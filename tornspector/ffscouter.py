"""ffscouter stat estimates. optional, key has to be registered on ffscouter.com"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://ffscouter.com/api/v1"
BATCH = 100  # a torn faction maxes out at 100 members, so one request covers it


class FFScouterError(RuntimeError):
    pass


def get_stats(key: str, player_ids: list[int]) -> dict[int, dict]:
    estimates: dict[int, dict] = {}
    ids = [int(i) for i in player_ids]
    for i in range(0, len(ids), BATCH):
        chunk = ids[i : i + BATCH]
        # ffscouter only takes the key as a query param, no header option
        query = urllib.parse.urlencode({"key": key, "targets": ",".join(map(str, chunk))})
        req = urllib.request.Request(
            f"{BASE_URL}/get-stats?{query}", headers={"User-Agent": "tornspector/0.1"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                rows = json.load(resp)
        except urllib.error.HTTPError as exc:
            try:
                body = json.load(exc)
                raise FFScouterError(f"FFScouter {body.get('code')}: {body.get('error')}") from exc
            except (ValueError, AttributeError):
                raise FFScouterError(f"FFScouter HTTP {exc.code}") from exc
        # success is a list of rows, errors come back as a dict
        if isinstance(rows, dict) and "error" in rows:
            raise FFScouterError(f"FFScouter {rows.get('code')}: {rows.get('error')}")
        for row in rows:
            if row.get("no_data") or row.get("bs_estimate") is None:
                continue
            estimates[int(row["player_id"])] = row
        if i + BATCH < len(ids):
            time.sleep(1)  # pause between batches so ffscouter doesnt throw errors
    return estimates
