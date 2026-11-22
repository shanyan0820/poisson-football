import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from prediction_pipeline import (
    ESPN_TEAM_ALIASES, MODEL_CONFIGS, base_fixture, find_result, global_backtest,
    map_espn_team, outcome, parse_espn_events, parse_main_fixtures,
    predicted_outcome, rolling_backtest, score_prediction, update_prediction_archive,
)


def forecast(h=.5, d=.3, a=.2):
    return {"home_win": h, "draw": d, "away_win": a}


def sample_league(count=40):
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    matches = []
    start = date(2026, 8, 1)
    for i in range(count):
        home = teams[i % 4]
        away = teams[(i + 1) % 4]
        matches.append({
            "date": (start + timedelta(days=i * 2)).isoformat(), "time": "15:00",
            "home": home, "away": away, "home_source": home, "away_source": away,
            "home_goals": (i + 1) % 3, "away_goals": i % 2,
        })
    return {"id": "TEST", "name": "测试联赛", "matches": matches}


class PredictionMetricTests(unittest.TestCase):
    def test_outcome_and_predicted_outcome(self):
        self.assertEqual(outcome(2, 1), "H")
        self.assertEqual(outcome(1, 1), "D")
        self.assertEqual(outcome(0, 2), "A")
        self.assertEqual(predicted_outcome(forecast()), "H")

    def test_brier_logloss_and_hit(self):
        score = score_prediction(forecast(), "H")
        self.assertTrue(score["hit"])
        self.assertAlmostEqual(score["brier"], .38)
        self.assertAlmostEqual(score["log_loss"], .6931471805599453)
        miss = score_prediction(forecast(), "A")
        self.assertFalse(miss["hit"])
        self.assertAlmostEqual(miss["brier"], .98)

    def test_extreme_probability_is_bounded_for_logloss(self):
        score = score_prediction(forecast(1, 0, 0), "A")
        self.assertLess(score["log_loss"], 40)


class FixtureParserTests(unittest.TestCase):
    def test_main_fixture_is_converted_to_beijing_day(self):
        league = sample_league()
        text = "Div,Date,Time,HomeTeam,AwayTeam\r\nE0,22/11/2026,16:00,Alpha,Beta\r\nE0,22/11/2026,15:00,Gamma,Delta\r\n"
        # 英国16:00=北京时间次日00:00，因此目标11月23日只保留第一场。
        result = parse_main_fixtures(text, {"E0": league}, date(2026, 11, 23))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["home"], "Alpha")
        self.assertTrue(result[0]["kickoff_beijing"].startswith("2026-11-23T00:00"))

    def test_espn_alias_and_direct_mapping(self):
        names = ["Hammarby", "Malmo FF"]
        self.assertEqual(map_espn_team("Hammarby IF", names), "Hammarby")
        self.assertEqual(map_espn_team("Malmo FF", names), "Malmo FF")
        with self.assertRaises(ValueError):
            map_espn_team("Unknown FC", names)

    def test_espn_event_mapping_and_beijing_filter(self):
        league = {**sample_league(), "id": "SWE", "name": "瑞典超"}
        league["matches"] = league["matches"][:2]
        league["matches"][0].update({"home_source": "Hammarby", "away_source": "Sirius"})
        payload = {"events": [{
            "id": "evt1", "date": "2026-11-22T16:00Z",
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": "Hammarby IF"}},
                {"homeAway": "away", "team": {"displayName": "IK Sirius"}},
            ]}],
        }]}
        result = parse_espn_events([payload], league, ["Hammarby", "Sirius"], date(2026, 11, 23))
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["home_source"], result[0]["away_source"]), ("Hammarby", "Sirius"))
        self.assertEqual(result[0]["source_event_id"], "evt1")


class BacktestTests(unittest.TestCase):
    def test_rolling_backtest_only_passes_prior_matches(self):
        league = sample_league(36)
        lengths = []

        def fake(history, home, away, config):
            lengths.append(len(history))
            return {"predicted": "H", "forecast": forecast(), "expected_goals": {"home": 1, "away": 1}, "top_scores": []}

        with patch("prediction_pipeline.predict_with_config", side_effect=fake):
            report = rolling_backtest(league, MODEL_CONFIGS[:2], min_history=30)
        self.assertEqual(sorted(set(lengths)), [30, 31, 32, 33, 34, 35])
        self.assertEqual(report["models"][0]["n"], 6)

    def test_real_backtest_has_consistent_metrics(self):
        report = rolling_backtest(sample_league(36), MODEL_CONFIGS[:2], min_history=30)
        for model in report["models"]:
            self.assertEqual(model["n"], 6)
            self.assertGreaterEqual(model["brier"], 0)
            self.assertGreaterEqual(model["log_loss"], 0)
            self.assertGreaterEqual(model["accuracy"], 0)
            self.assertLessEqual(model["accuracy"], 1)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.league = sample_league(40)
        self.dataset = {"leagues": [self.league]}
        self.now = datetime(2026, 11, 20, tzinfo=timezone.utc)
        self.fixture = base_fixture(self.league, "Alpha", "Beta",
                                    datetime(2026, 11, 21, 12, tzinfo=timezone.utc), "test", "fixture-1")

    def test_pregame_prediction_is_frozen_then_result_is_added(self):
        archive = update_prediction_archive({"version": 1, "matches": {}}, [self.fixture], self.dataset, self.now)
        key = next(iter(archive["matches"]))
        original = copy.deepcopy(archive["matches"][key]["predictions"])
        created = archive["matches"][key]["created_at"]
        self.assertTrue(original)
        later_dataset = copy.deepcopy(self.dataset)
        later_dataset["leagues"][0]["matches"].append({
            "date": "2026-11-21", "time": "20:00", "home": "Alpha", "away": "Beta",
            "home_source": "Alpha", "away_source": "Beta", "home_goals": 3, "away_goals": 1,
        })
        changed = copy.deepcopy(self.fixture)
        changed["home"] = "不应覆盖"
        archive = update_prediction_archive(archive, [self.fixture], later_dataset, self.now + timedelta(days=1))
        record = archive["matches"][key]
        self.assertEqual(record["predictions"], original)
        self.assertEqual(record["created_at"], created)
        self.assertEqual(record["status"], "finished")
        self.assertEqual(record["actual"]["outcome"], "H")

    def test_first_seen_after_kickoff_is_not_backfilled(self):
        archive = update_prediction_archive({"version": 1, "matches": {}}, [self.fixture],
                                            self.dataset, self.now + timedelta(days=2))
        record = next(iter(archive["matches"].values()))
        self.assertEqual(record["status"], "untracked")
        self.assertEqual(record["predictions"], {})

    def test_locked_match_waits_for_result_without_rewriting_prediction(self):
        archive = update_prediction_archive({"version": 1, "matches": {}}, [self.fixture], self.dataset, self.now)
        key = next(iter(archive["matches"]))
        original = copy.deepcopy(archive["matches"][key]["predictions"])
        archive = update_prediction_archive(archive, [self.fixture], self.dataset, self.now + timedelta(days=2))
        self.assertEqual(archive["matches"][key]["status"], "awaiting_result")
        self.assertEqual(archive["matches"][key]["predictions"], original)

    def test_result_matching_allows_timezone_date_difference(self):
        fixture = copy.deepcopy(self.fixture)
        match = self.league["matches"][0]
        match.update({"home_source": "Alpha", "away_source": "Beta", "date": "2026-11-21"})
        found = find_result(fixture, self.league)
        self.assertIs(found, match)


if __name__ == "__main__":
    unittest.main()
