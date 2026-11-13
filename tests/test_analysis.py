import sqlite3
import unittest

from tornspector import activity, analysis, render
from tornspector.demo import build_demo

DAY = 86400
NOW = 1_800_000_000


def war(i, won, hits, start_days_ago, hours=50):
    start = NOW - start_days_ago * DAY
    return analysis.War(
        id=i, start=start, end=start + hours * 3600, opponent_id=900 + i, opponent_name=f"Opp {i}",
        our_score=100, opp_score=50, our_attacks=sum(hits.values()), won=won, hits=hits,
    )


def member(pid, days, bs=None):
    return analysis.Member(id=pid, name=f"p{pid}", days_in_faction=days, bs_estimate=bs)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.wars = [
            war(1, True, {1: 40, 2: 10}, 60),
            war(2, False, {1: 20, 2: 0}, 40),
            war(3, False, {1: 30}, 20),
            war(4, True, {1: 50, 3: 5}, 5),
        ]
        # p3 joined 10 days ago: only war 4 counts for them
        self.members = [member(1, 400, 2e7), member(2, 400, 5e5), member(3, 10, 1e6)]
        self.res = analysis.analyse({"id": 1, "name": "Test"}, self.members, self.wars, NOW)
        self.by_id = {m.id: m for m in self.res["members"]}

    def test_eligibility_respects_join_date(self):
        self.assertEqual(self.by_id[3].eligible, 1)
        self.assertEqual(self.by_id[3].per_war, [None, None, None, 5])
        self.assertEqual(self.by_id[2].eligible, 4)

    def test_participation_and_averages(self):
        p2 = self.by_id[2]
        self.assertEqual(p2.active, 1)
        self.assertAlmostEqual(p2.participation, 0.25)
        self.assertAlmostEqual(p2.avg_per_war, 2.5)
        self.assertAlmostEqual(p2.avg_per_active, 10)

    def test_faction_summary(self):
        s = self.res["summary"]
        self.assertEqual([w["roster_hits"] for w in self.res["wars"]], [50, 20, 30, 55])
        self.assertAlmostEqual(s["avg_hits_win"], 52.5)
        self.assertAlmostEqual(s["avg_hits_loss"], 25)
        self.assertAlmostEqual(s["persistence_raw"], 25 / 52.5)
        self.assertEqual(s["peak_hits"], 55)
        self.assertIsNone(s["top10"])  # roster too small

    def test_hit_shares_sum_to_one(self):
        self.assertAlmostEqual(sum(m.hit_share for m in self.members), 1.0)

    def test_threat_in_range(self):
        for m in self.members:
            self.assertTrue(0 <= m.threat <= 100)

    def test_parse_war(self):
        report = {
            "id": 7, "start": 1, "end": 3601, "winner": 10,
            "factions": [
                {"id": 10, "name": "Us", "score": 5, "attacks": 3,
                 "members": [{"id": 111, "name": "a", "attacks": 3}]},
                {"id": 20, "name": "Them", "score": 1, "attacks": 1, "members": []},
            ],
        }
        w = analysis.parse_war(report, 10)
        self.assertTrue(w.won)
        self.assertEqual(w.hits, {111: 3})
        self.assertEqual(w.opponent_name, "Them")

    def test_trend(self):
        self.assertEqual(analysis._trend([10, 10, 20, 20]), "Improving")
        self.assertEqual(analysis._trend([20, 20, 10, 10]), "Declining")
        self.assertEqual(analysis._trend([10, 11, 10, 11]), "Stable")


class ActivityTests(unittest.TestCase):
    def test_summary_from_polls(self):
        con = sqlite3.connect(":memory:")
        con.executescript(activity.SCHEMA)
        ts = NOW - 2 * DAY
        while ts <= NOW:
            on = (ts % DAY) < 6 * 3600  # player 1 active 00:00-06:00 TCT only
            activity.record_poll(
                con, 1,
                [{"id": 1, "last_action": {"timestamp": ts - 30 if on else ts - 9999, "status": "Offline"}},
                 {"id": 2, "last_action": {"timestamp": ts - 9999, "status": "Offline"}}],
                ts,
            )
            ts += activity.SLOT
        a = activity.summarise(con, 1, [1, 2], days=14, now=NOW)
        blocks = a["player_blocks"][1]
        self.assertGreater(blocks[0], 0.9)
        self.assertLess(blocks[6], 0.1)
        self.assertEqual(max(a["player_blocks"][2]), 0)


class RenderTests(unittest.TestCase):
    def test_demo_renders_and_escapes(self):
        result, act = build_demo()
        result["members"][0].name = "<script>x</script>"
        html = render.render(result, act)
        self.assertIn("Example Syndicate", html)
        self.assertNotIn("<script>x</script>", html)


if __name__ == "__main__":
    unittest.main()
