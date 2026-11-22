import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from prediction_pipeline import (
    ESPN_TEAM_ALIASES, MODEL_CONFIGS, base_fixture, build_market_tracking, choose_integrated_selection,
    compare_handicap, compare_total_market, find_result, freeze_integrated_selection, global_backtest,
    map_espn_team, outcome, parse_espn_events,
    parse_main_fixtures, predicted_outcome, public_record, rolling_backtest, score_prediction,
    settle_margin, split_quarter_line, summarize_market_rows, update_prediction_archive,
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

    def test_main_fixture_reads_real_asian_handicap_fields(self):
        league = sample_league()
        text = ("Div,Date,Time,HomeTeam,AwayTeam,AHh,B365AHH,B365AHA,AvgAHH,AvgAHA,B365>2.5,B365<2.5,Avg>2.5,Avg<2.5\r\n"
                "E0,22/11/2026,16:00,Alpha,Beta,-0.75,1.90,1.95,1.87,1.91,1.80,2.00,1.77,1.96\r\n")
        result = parse_main_fixtures(text, {"E0": league}, date(2026, 11, 23))
        market = result[0]["asian_handicap"]
        self.assertEqual(market["home_line"], -.75)
        self.assertEqual(market["bet365_home_odds"], 1.90)
        self.assertEqual(market["average_away_odds"], 1.91)
        total = result[0]["total_market"]
        self.assertEqual(total["line"], 2.5)
        self.assertEqual(total["bet365_over_odds"], 1.80)
        self.assertEqual(total["average_under_odds"], 1.96)

    def test_missing_handicap_stays_empty(self):
        league = sample_league()
        text = "Div,Date,Time,HomeTeam,AwayTeam\r\nE0,22/11/2026,16:00,Alpha,Beta\r\n"
        result = parse_main_fixtures(text, {"E0": league}, date(2026, 11, 23))
        self.assertIsNone(result[0]["asian_handicap"])

    def test_handicap_comparison_has_neutral_zone_and_correct_sides(self):
        prediction = {"expected_goals": {"home": 1.8, "away": 1.0}}
        market = {"home_line": -.5}
        home = compare_handicap(prediction, market)
        self.assertEqual(home["side"], "home")
        self.assertEqual(home["line"], -.5)
        self.assertAlmostEqual(home["edge"], .3)
        away = compare_handicap({"expected_goals": {"home": 1.0, "away": 1.8}}, market)
        self.assertEqual(away["side"], "away")
        self.assertEqual(away["line"], .5)
        neutral = compare_handicap({"expected_goals": {"home": 1.45, "away": 1.0}}, market)
        self.assertEqual(neutral["side"], "neutral")
        self.assertIsNone(compare_handicap(prediction, None))

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


class MarketModelTests(unittest.TestCase):
    def test_quarter_line_split_and_settlement(self):
        self.assertEqual(split_quarter_line(-.75), [-1, -.5])
        self.assertEqual(split_quarter_line(.25), [0, .5])
        self.assertEqual(split_quarter_line(.5), [.5])
        half_win = settle_margin(1, -.75, 2.0)
        self.assertEqual(half_win["result"], "half_win")
        self.assertAlmostEqual(half_win["unit_profit"], .5)
        self.assertAlmostEqual(half_win["push_eq"], .5)
        half_loss = settle_margin(0, -.25, 2.0)
        self.assertEqual(half_loss["result"], "half_loss")
        self.assertAlmostEqual(half_loss["unit_profit"], -.5)
        over = settle_margin(3, -2.5, 1.9)
        self.assertEqual(over["result"], "full_win")
        self.assertAlmostEqual(over["unit_profit"], .9)

    def test_accuracy_uses_half_win_half_loss_and_excludes_push(self):
        rows = [
            settle_margin(1, 0, 1.9),
            settle_margin(1, -.75, 2.0),
            settle_margin(0, -.25, 2.0),
            settle_margin(0, 0, 1.9),
        ]
        summary = summarize_market_rows(rows)
        self.assertEqual(summary["n"], 4)
        self.assertEqual((summary["full_win"], summary["half_win"], summary["half_loss"], summary["push"]), (1, 1, 1, 1))
        self.assertAlmostEqual(summary["effective_win_rate"], .75)

    def test_positive_ev_direction_and_total_direction(self):
        prediction = {"expected_goals": {"home": 2, "away": 1}}
        market = {"home_line": 0, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6}
        analysis = compare_handicap(prediction, market)
        self.assertEqual(analysis["ev_side"], "home")
        self.assertGreater(analysis["home_metrics"]["ev"], 0)
        no_ev = compare_handicap(prediction, {"home_line": 0, "bet365_home_odds": 1.01, "bet365_away_odds": 1.01})
        self.assertIsNone(no_ev["ev_side"])
        total = compare_total_market({"expected_goals": {"home": 2, "away": 2}},
                                     {"line": 2.5, "bet365_over_odds": 2.0, "bet365_under_odds": 1.5})
        self.assertEqual(total["ev_side"], "over")
        self.assertGreater(total["over_metrics"]["ev"], 0)

    def test_integrated_selection_takes_highest_ev_among_aligned_candidates(self):
        prediction = {"expected_goals": {"home": 2, "away": 1}}
        handicap = compare_handicap(prediction, {"home_line": 0, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6})
        total = compare_total_market(prediction, {"line": 2.5, "bet365_over_odds": 2.0, "bet365_under_odds": 1.5})
        selected = choose_integrated_selection(handicap, total)
        self.assertEqual(selected["status"], "candidate")
        self.assertEqual(selected["candidate_count"], 2)
        self.assertEqual(selected["ev"], max(handicap["home_metrics"]["ev"], total["over_metrics"]["ev"]))
        self.assertEqual(selected["rule_version"], "gap-direction-ev-v1")

    def test_extreme_handicap_gap_rejects_whole_match_even_with_total_value(self):
        prediction = {"expected_goals": {"home": 3, "away": 0}}
        handicap = compare_handicap(prediction, {"home_line": 0, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6})
        total = compare_total_market(prediction, {"line": 2.5, "bet365_over_odds": 2.0, "bet365_under_odds": 1.5})
        self.assertGreater(total["over_metrics"]["ev"], 0)
        selected = choose_integrated_selection(handicap, total)
        self.assertEqual(selected["status"], "skip")
        self.assertEqual(selected["extreme_markets"], ["asian_handicap"])

    def test_low_home_probability_with_deep_home_handicap_does_not_pick_away(self):
        prediction = {"expected_goals": {"home": .8, "away": 1.4}}
        handicap = compare_handicap(prediction, {"home_line": -1.5, "bet365_home_odds": 1.8, "bet365_away_odds": 2.2})
        selected = choose_integrated_selection(handicap, None)
        self.assertLess(handicap["edge"], -1)
        self.assertEqual(selected["status"], "skip")
        self.assertIsNone(selected["side"])

    def test_too_small_gap_is_not_forced_into_a_candidate(self):
        prediction = {"expected_goals": {"home": 1.05, "away": 1}}
        handicap = compare_handicap(prediction, {"home_line": 0, "bet365_home_odds": 3.0, "bet365_away_odds": 1.5})
        selected = choose_integrated_selection(handicap, None)
        self.assertLess(abs(handicap["edge"]), .15)
        self.assertEqual(selected["status"], "skip")
        self.assertIn("不足 0.15", selected["reason"])

    def test_integrated_selection_freezes_at_kickoff(self):
        now = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)
        record = {"kickoff_utc": "2026-11-22T11:00:00+00:00", "status": "scheduled"}
        first = freeze_integrated_selection(record, {"status": "candidate", "ev": .08}, now)
        self.assertEqual(first["ev"], .08)
        updated = freeze_integrated_selection(record, {"status": "candidate", "ev": .10}, now)
        self.assertEqual(updated["ev"], .10)
        frozen = freeze_integrated_selection(record, {"status": "skip"}, now + timedelta(hours=2))
        self.assertEqual(frozen["ev"], .10)

    def test_finished_record_automatically_builds_market_tracking(self):
        record = {
            "predictions": {"stable": {"expected_goals": {"home": 2, "away": 1}, "forecast": forecast()}},
            "actual": {"home_goals": 2, "away_goals": 0, "outcome": "H"},
            "asian_handicap": {"home_line": 0, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6},
            "total_market": {"line": 2.5, "bet365_over_odds": 2.0, "bet365_under_odds": 1.5},
        }
        public = public_record(record)
        self.assertEqual(public["integrated_selection"]["status"], "candidate")
        self.assertIn("integrated", public["market_settlement"])
        self.assertEqual(public["market_settlement"]["asian_handicap"]["result"], "full_win")
        self.assertEqual(public["market_settlement"]["total_goals"]["result"], "full_loss")
        tracking = build_market_tracking([public])
        ah = tracking["asian_handicap"]["overall"]
        ou = tracking["total_goals"]["overall"]
        self.assertEqual((ah["n"], ah["effective_win_rate"]), (1, 1))
        self.assertAlmostEqual(ah["roi"], 1.2)
        self.assertEqual((ou["n"], ou["effective_win_rate"]), (1, 0))
        self.assertAlmostEqual(ou["roi"], -1)
        self.assertEqual(tracking["integrated"]["overall"]["n"], 1)


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

    def test_handicap_snapshot_updates_before_kickoff_without_changing_prediction(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["asian_handicap"] = {"home_line": -.5, "bet365_home_odds": 1.9}
        fixture["total_market"] = {"line": 2.5, "bet365_over_odds": 1.8, "bet365_under_odds": 2.0}
        archive = update_prediction_archive({"version": 1, "matches": {}}, [fixture], self.dataset, self.now)
        key = next(iter(archive["matches"]))
        original = copy.deepcopy(archive["matches"][key]["predictions"])
        changed = copy.deepcopy(fixture)
        changed["asian_handicap"] = {"home_line": -.75, "bet365_home_odds": 1.95}
        changed["total_market"] = {"line": 2.5, "bet365_over_odds": 1.85, "bet365_under_odds": 1.95}
        archive = update_prediction_archive(archive, [changed], self.dataset, self.now + timedelta(hours=1))
        record = archive["matches"][key]
        self.assertEqual(record["predictions"], original)
        self.assertEqual(record["asian_handicap"]["home_line"], -.75)
        self.assertEqual(len(record["asian_handicap_history"]), 1)
        self.assertEqual(record["asian_handicap_history"][0]["home_line"], -.5)
        self.assertTrue(record["asian_handicap"]["captured_at"].endswith("+00:00"))
        self.assertEqual(record["total_market"]["bet365_over_odds"], 1.85)
        self.assertEqual(len(record["total_market_history"]), 1)

    def test_result_matching_allows_timezone_date_difference(self):
        fixture = copy.deepcopy(self.fixture)
        match = self.league["matches"][0]
        match.update({"home_source": "Alpha", "away_source": "Beta", "date": "2026-11-21"})
        found = find_result(fixture, self.league)
        self.assertIs(found, match)


if __name__ == "__main__":
    unittest.main()
