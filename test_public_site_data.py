import json
import unittest

from public_site_data import (
    build_public_backtest,
    build_public_predictions,
    sanitize_fixture,
)


class PublicSiteDataTests(unittest.TestCase):
    def sample_raw(self):
        return {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "featured_model_id": "m1",
            "models": [{"id": "m1", "name": "模型", "description": "说明", "private": 1}],
            "backtest": {"global": [{"model_id": "m1", "n": 10, "private": 1}]},
            "market_tracking": {
                "rule_definitions": {
                    "A01": {"grade": "A", "threshold": -0.95},
                    "A02": {"grade": "A", "threshold": 0.5},
                    "B01": {"grade": "B", "threshold": -0.7},
                },
                "rules": {
                    "A01": {
                        "overall": {"n": 2, "unit_profit": 1, "full_win": 1, "full_loss": 1},
                        "by_league": {"E0": {"n": 2, "unit_profit": 1, "full_win": 1, "full_loss": 1}},
                    },
                    "A02": {
                        "overall": {"n": 1, "unit_profit": 1, "full_win": 1},
                        "by_league": {"E0": {"n": 1, "unit_profit": 1, "full_win": 1}},
                    },
                    "B01": {
                        "overall": {"n": 1, "unit_profit": 0.5, "half_win": 1},
                        "by_league": {"E1": {"n": 1, "unit_profit": 0.5, "half_win": 1}},
                    },
                },
            },
            "fixtures": [],
            "history": [],
        }

    def test_public_predictions_remove_sensitive_fields(self):
        raw = self.sample_raw()
        raw["fixtures"] = [{
            "key": "match-1",
            "league_id": "E0",
            "home": "Home",
            "away": "Away",
            "rule_evaluations": [{"rule_id": "A01", "threshold": -0.95}],
            "handicap_analysis": {"threshold": 0.15},
            "recommendation_selection": {
                "status": "candidate", "side": "away", "line": -0.25,
                "grade": "A", "rule_id": "A01", "threshold": -0.95,
                "reason": "private reason", "odds": 1.9,
            },
            "market_settlement": {
                "recommendation": {"result": "full_win", "rule_id": "A01", "unit_profit": 0.9},
                "rule_settlements": [{"rule_id": "A01"}],
            },
        }]
        public = build_public_predictions(raw)
        serialized = json.dumps(public, ensure_ascii=False)
        for forbidden in ("rule_id", "rule_name", "threshold", "reason", "rule_evaluations", "handicap_analysis"):
            self.assertNotIn(forbidden, serialized)
        selection = public["fixtures"][0]["recommendation_selection"]
        self.assertEqual(selection["side"], "away")
        self.assertEqual(selection["grade"], "A")
        self.assertEqual(selection["odds"], 1.9)
        self.assertEqual(public["fixtures"][0]["market_settlement"]["recommendation"]["result"], "full_win")
        self.assertNotIn("private", json.dumps(public["models"]))

    def test_grade_aggregation(self):
        public = build_public_predictions(self.sample_raw())
        grade_a = public["market_tracking"]["grades"]["A"]["overall"]
        self.assertEqual(grade_a["n"], 3)
        self.assertEqual(grade_a["unit_profit"], 2)
        self.assertAlmostEqual(grade_a["effective_win_rate"], 2 / 3)
        self.assertAlmostEqual(grade_a["roi"], 2 / 3)
        self.assertEqual(public["market_tracking"]["grades"]["A"]["by_league"]["E0"]["n"], 3)
        self.assertEqual(public["market_tracking"]["grades"]["B"]["by_league"]["E1"]["n"], 1)

    def test_sanitize_fixture_drops_internal_analysis(self):
        public = sanitize_fixture({
            "key": "m", "predictions": [{"model": "private"}],
            "integrated_selection": {"rule_id": "A01"},
            "featured_selection": {"threshold": 1},
        })
        self.assertNotIn("predictions", public)
        self.assertNotIn("integrated_selection", public)
        self.assertNotIn("featured_selection", public)

    def test_public_backtest_contains_only_grade_summaries(self):
        raw = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "combined": {"valid_samples": 100},
            "loso": {"n": 4, "roi": 0.01},
        }
        selections = [
            {"season": "2021-22", "grade": "A", "win_eq": 1, "loss_eq": 0, "unit_profit": 0.9},
            {"season": "2021-22", "grade": "B", "win_eq": 0, "loss_eq": 1, "unit_profit": -1},
            {"season": "2021-22", "grade": "C", "win_eq": 0.5, "loss_eq": 0, "unit_profit": 0.4},
            {"season": "2026-27", "grade": "A", "win_eq": 0, "loss_eq": 1, "unit_profit": -1},
        ]
        public = build_public_backtest(raw, selections)
        self.assertEqual(public["combined"]["overall"]["n"], 3)
        self.assertEqual(public["combined"]["grades"]["A"]["n"], 1)
        self.assertEqual(public["combined"]["grades"]["A"]["effective_win_rate"], 1)
        self.assertEqual(public["full_holdout"]["overall"]["n"], 1)
        self.assertEqual(public["full_holdout"]["grades"]["A"]["effective_win_rate"], 0)
        serialized = json.dumps(public, ensure_ascii=False)
        for forbidden in ("rule_id", "rule_name", "threshold", "rule_version"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
