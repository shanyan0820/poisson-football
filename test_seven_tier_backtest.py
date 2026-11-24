import hashlib
import json
import unittest
from pathlib import Path

from prediction_pipeline import summarize_market_rows
from tier_rules import evaluate_tier_rules


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "backtests" / "seven-tier-model-anchor-2022-26"


class SevenTierBacktestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
        cls.rows = json.loads((OUT / "selections.json").read_text(encoding="utf-8"))
        cls.samples = json.loads((OUT / "normalized_samples.json").read_text(encoding="utf-8"))

    def test_four_season_sample_chain_and_locked_results(self):
        self.assertEqual(self.report["combined"]["raw_matches"], 7082)
        self.assertEqual(self.report["combined"]["valid_samples"], 5723)
        self.assertEqual([s["valid_samples"] for s in self.report["seasons"].values()], [1482, 1415, 1415, 1411])
        expected = {"T1": (151, 9.405), "T2": (647, -6.99), "T3": (26, 1.625)}
        for rule_id, (n, profit) in expected.items():
            overall = self.report["combined"]["rules"][rule_id]["overall"]
            self.assertEqual(overall["n"], n)
            self.assertAlmostEqual(overall["unit_profit"], profit)

    def test_every_selection_reproduces_shared_rule_and_summary(self):
        for row in self.rows:
            rule = next(item for item in evaluate_tier_rules(
                row["model_handicap"], row["market_handicap"], row["institution_handicap"]
            ) if item["rule_id"] == row["rule_id"])
            self.assertTrue(rule["triggered"])
            self.assertEqual(rule["side"], row["side"])
            self.assertEqual(row["handicap_source"], "real")
            self.assertGreater(row["odds"], 1)
        for rule_id in ("T1", "T2", "T3"):
            summary = summarize_market_rows([row for row in self.rows if row["rule_id"] == rule_id])
            self.assertEqual(summary, self.report["combined"]["rules"][rule_id]["overall"])

    def test_all_samples_use_v2b_and_have_real_two_sided_settlement_inputs(self):
        for season, samples in self.samples.items():
            for sample in samples:
                self.assertEqual(sample["market_version"], "points_form20")
                self.assertAlmostEqual(sample["market_handicap"],
                                       .8 * sample["season_market"] + .2 * sample["recent6_market"])
                self.assertGreaterEqual(min(sample["home_played"], sample["away_played"]), 7)
                self.assertIn("home", sample["settlements"])
                self.assertIn("away", sample["settlements"])

    def test_2025_26_source_hashes_match_downloaded_files(self):
        source = self.report["source_2025_26"]
        for league_id, item in source["files"].items():
            payload = (OUT / "source" / "2526" / f"{league_id}.csv").read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), item["sha256"])
            self.assertEqual(len(payload), item["bytes"])
            self.assertIn("/2526/", item["url"])


if __name__ == "__main__":
    unittest.main()
