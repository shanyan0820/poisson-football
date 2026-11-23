import copy
import unittest
from datetime import datetime, timedelta, timezone

from prediction_pipeline import public_record
from research_model import (
    build_research_report, direction_rows, make_research_snapshot, relation_for, rule_rows,
    settle_research_directions, summarize_research_rows, summarize_rule_rows, summarize_with_time_validation,
    update_research_archive,
)

UTC = timezone.utc


def public_fixture(actual=None):
    record = {
        "key": "match-1", "league_id": "TEST", "league_name": "测试联赛",
        "home": "主队", "away": "客队", "home_source": "Home", "away_source": "Away",
        "kickoff_utc": "2026-11-22T11:00:00+00:00", "kickoff_beijing": "2026-11-22T19:00:00+08:00",
        "source": "test", "created_at": "2026-11-22T08:00:00+00:00",
        "status": "finished" if actual else "scheduled", "actual": actual,
        "predictions": {"stable": {
            "expected_goals": {"home": 2, "away": 1},
            "forecast": {"home_win": .5, "draw": .25, "away_win": .25},
            "top_scores": [{"home": 2, "away": 1, "probability": .1}],
        }},
        "asian_handicap": {"home_line": -.5, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6},
        "ranking": {"home_rank": 1, "away_rank": 4, "league_team_count": 4,
                    "rank_difference": 3, "rank_ratio": .75, "market_handicap": -2.0},
    }
    return public_record(record, now=datetime(2026, 11, 22, 10, tzinfo=UTC))


class ResearchSnapshotTests(unittest.TestCase):
    def test_relations_keep_direction_separate_from_gap(self):
        aligned = {"model_goal_difference": 1, "market_goal_baseline": .5, "edge": .5}
        opposed = {"model_goal_difference": 1, "market_goal_baseline": -.5, "edge": 1.5}
        self.assertEqual(relation_for("asian_handicap", aligned), "aligned")
        self.assertEqual(relation_for("asian_handicap", opposed), "opposed")

    def test_snapshot_preserves_all_inputs_and_marks_pregame(self):
        snapshot = make_research_snapshot(public_fixture(), datetime(2026, 11, 22, 10, tzinfo=UTC))
        self.assertTrue(snapshot["tracking_eligible"])
        self.assertEqual(snapshot["freeze_status"], "pregame")
        self.assertIn("stable", snapshot["predictions"])
        self.assertIn("home_metrics", snapshot["handicap_analysis"])
        self.assertEqual(snapshot["ranking"]["market_handicap"], -2.0)
        self.assertNotIn("total_analysis", snapshot)
        self.assertNotIn("total_market", snapshot)
        self.assertEqual(set(snapshot["relations"]), {"asian_handicap"})

    def test_archive_freezes_pregame_inputs_and_only_adds_result(self):
        now = datetime(2026, 11, 22, 10, tzinfo=UTC)
        archive = update_research_archive({"version": 1, "matches": {}}, [public_fixture()], now)
        self.assertEqual(archive["version"], 5)
        original = archive["matches"]["match-1"]["featured_prediction"]
        changed = public_fixture({"home_goals": 2, "away_goals": 0, "outcome": "H"})
        changed["featured_prediction"]["expected_goals"] = {"home": 9, "away": 9}
        changed["handicap_analysis"]["edge"] = 99
        archive = update_research_archive(archive, [changed], now + timedelta(hours=2))
        snapshot = archive["matches"]["match-1"]
        self.assertEqual(snapshot["featured_prediction"], original)
        self.assertNotEqual(snapshot["handicap_analysis"]["edge"], 99)
        self.assertEqual(snapshot["actual"]["home_goals"], 2)
        self.assertEqual(set(snapshot["direction_settlements"]), {"asian_handicap"})
        self.assertEqual(set(snapshot["rule_settlements"]), {"E1", "E2"})
        self.assertEqual(len(rule_rows(archive)), 2)

    def test_late_snapshot_is_retrospective_and_excluded_from_forward_report(self):
        late = public_fixture({"home_goals": 2, "away_goals": 0, "outcome": "H"})
        archive = update_research_archive({"version": 1, "matches": {}}, [late], datetime(2026, 11, 22, 12, tzinfo=UTC))
        self.assertFalse(archive["matches"]["match-1"]["tracking_eligible"])
        self.assertEqual(direction_rows(archive), [])
        report = build_research_report(archive, datetime(2026, 11, 22, 12, tzinfo=UTC))
        self.assertEqual(report["sample"]["retrospective_matches"], 1)
        self.assertEqual(report["sample"]["settled_direction_rows"], 0)


class ResearchSettlementTests(unittest.TestCase):
    def test_every_market_side_is_settled(self):
        snapshot = make_research_snapshot(public_fixture(), datetime(2026, 11, 22, 10, tzinfo=UTC))
        snapshot["actual"] = {"home_goals": 2, "away_goals": 0, "outcome": "H"}
        settled = settle_research_directions(snapshot)
        self.assertEqual(settled["asian_handicap"]["home"]["result"], "full_win")
        self.assertEqual(settled["asian_handicap"]["away"]["result"], "full_loss")
        self.assertNotIn("total_goals", settled)
        self.assertEqual(len(direction_rows({"matches": {"1": snapshot}})), 0)
        snapshot["direction_settlements"] = settled
        self.assertEqual(len(direction_rows({"matches": {"1": snapshot}})), 2)

    def test_summary_excludes_other_results_from_accuracy_but_not_roi(self):
        results = ["full_win", "full_loss", "half_win", "half_loss", "push", "win_loss"]
        rows = [{"result": result, "unit_profit": profit, "kickoff_utc": "2026-01-01T00:00:00+00:00",
                 "key": str(i), "market": "asian_handicap", "side": "home"}
                for i, (result, profit) in enumerate(zip(results, [1, -1, .5, -.5, 0, 0]))]
        summary = summarize_research_rows(rows)
        self.assertEqual((summary["n"], summary["valid_n"], summary["excluded_n"]), (6, 4, 2))
        self.assertEqual(summary["accuracy"], .5)
        self.assertEqual(summary["roi"], 0)

    def test_time_validation_requires_independent_later_sample(self):
        rows = [{"result": "full_win", "unit_profit": 1, "kickoff_utc": f"2026-01-{i + 1:02d}T00:00:00+00:00",
                 "key": str(i), "market": "asian_handicap", "side": "home"} for i in range(40)]
        summary = summarize_with_time_validation(rows)
        self.assertEqual(summary["validation_status"], "evaluated")
        self.assertEqual(summary["validation"]["n"], 12)
        self.assertTrue(summary["qualified"])
        small = summarize_with_time_validation(rows[:20])
        self.assertEqual(small["validation_status"], "insufficient_sample")
        self.assertFalse(small["qualified"])

    def test_rule_candidate_requires_sample_time_validation_and_weekly_stability(self):
        rows = []
        for i in range(60):
            day = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
            rows.append({"result": "full_win", "unit_profit": .9,
                         "kickoff_utc": day.isoformat(), "key": str(i),
                         "market": "asian_handicap", "side": "home", "rule_id": "A1"})
        summary = summarize_rule_rows(rows)
        self.assertEqual(summary["valid_n"], 60)
        self.assertTrue(summary["core_passed"])
        self.assertTrue(summary["weekly_stable"])
        self.assertTrue(summary["candidate"])
        self.assertEqual(summary["review_status"], "candidate_for_review")
        self.assertFalse(summary["qualified"])
        small = summarize_rule_rows(rows[:20])
        self.assertFalse(small["candidate"])
        self.assertEqual(small["validation_status"], "insufficient_sample")

    def test_report_exposes_all_rule_groups_without_auto_qualification(self):
        now = datetime(2026, 11, 22, 10, tzinfo=UTC)
        archive = update_research_archive({"version": 1, "matches": {}}, [public_fixture()], now)
        finished = public_fixture({"home_goals": 2, "away_goals": 0, "outcome": "H"})
        archive = update_research_archive(archive, [finished], now)
        report = build_research_report(archive, datetime(2026, 11, 22, 10, tzinfo=UTC))
        self.assertEqual(len(report["rule_groups"]), 13)
        self.assertEqual(report["sample"]["settled_rule_rows"], 2)
        self.assertEqual(report["sample"]["real_handicap_matches"], 1)
        self.assertEqual(report["sample"]["simulated_handicap_matches"], 0)
        self.assertIn("real", report["direction_groups"]["asian_handicap"]["by_source"])
        self.assertEqual(report["direction_groups"]["asian_handicap"]["by_source"]["real"]["n"], 2)
        self.assertIn("不构造赔率或收益", report["methodology"]["handicap_source"])
        self.assertEqual(report["optimization"]["rule_candidates"], [])
        self.assertEqual(report["optimization"]["status"], "no_validated_rule")


if __name__ == "__main__":
    unittest.main()
