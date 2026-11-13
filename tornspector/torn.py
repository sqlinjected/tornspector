"""torn api v2 client. key goes in the Authorization header so it doesnt end up in urls/logs"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://api.torn.com/v2"
USER_AGENT = "tornspector/0.1"
COMMENT = "tornspector"  # shows up in the key's api log on torn
MIN_INTERVAL = 0.7  # ~85 calls/min, under Torn's 100/min cap
MAX_ATTEMPTS = 5

# 5 = too many requests, 17 = backend error. just wait and retry
RETRYABLE_TORN_CODES = {5, 17}
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class TornAPIError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"Torn API error {code}: {message}")
        self.code = code


class TornClient:
    def __init__(self, key: str, cache_dir: str | Path | None = "cache"):
        if not key:
            raise ValueError("A Torn API key is required (set TORN_API_KEY).")
        self.key = key.strip()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._last_call = 0.0
        self.calls = 0

    def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> dict:
        query = dict(params or {})
        query["comment"] = COMMENT
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
        headers = {"Authorization": f"ApiKey {self.key}", "User-Agent": USER_AGENT}

        delay = 5.0
        for attempt in range(MAX_ATTEMPTS):
            last_try = attempt == MAX_ATTEMPTS - 1
            self._throttle()
            self.calls += 1
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.load(resp)
            except urllib.error.HTTPError as exc:
                if last_try or exc.code not in RETRYABLE_HTTP:
                    raise
            except urllib.error.URLError:
                if last_try:
                    raise
            else:
                err = payload.get("error")
                if not err:
                    return payload
                if last_try or err["code"] not in RETRYABLE_TORN_CODES:
                    raise TornAPIError(err["code"], err["error"])
            time.sleep(delay)
            delay *= 2

    def faction_basic(self, faction_id: int) -> dict:
        resp = self.get(f"/faction/{faction_id}/basic")
        return resp.get("basic", resp)

    def faction_members(self, faction_id: int) -> list[dict]:
        resp = self.get(f"/faction/{faction_id}/members", {"striptags": "true"})
        members = resp.get("members", [])
        # v1 gave a dict, v2 gives a list
        if isinstance(members, dict):
            members = [{"id": int(k), **v} for k, v in members.items()]
        return members

    def ranked_wars(self, faction_id: int, limit: int = 20) -> list[dict]:
        resp = self.get(f"/faction/{faction_id}/rankedwars", {"limit": limit})
        wars = resp.get("rankedwars", [])
        if isinstance(wars, dict):
            wars = [{"id": int(k), **v} for k, v in wars.items()]
        for w in wars:  # schema has used both names
            w.setdefault("id", w.get("war_id"))
        return wars

    def ranked_war_report(self, war_id: int) -> dict:
        # finished wars never change so cache them
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / f"rankedwar_{war_id}.json"
        if cache_file and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        # not a typo: v2 takes the war id in the faction slot for this endpoint
        resp = self.get(f"/faction/{war_id}/rankedwarreport")
        report = resp.get("rankedwarreport", resp)
        if cache_file and report.get("end"):
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(report), encoding="utf-8")
        return report
