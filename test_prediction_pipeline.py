import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from prediction_pipeline import (
    ESPN_TEAM_ALIASES, MODEL_CONFIGS, base_fixture, build_market_tracking, choose_integrated_selection,
    american_to_decimal, choose_recommendation, compare_handicap, find_result, fit_poisson_to_1x2, strength_band,
    freeze_integrated_selection, freeze_recommendation_selection, global_backtest,
    map_espn_team, outcome, parse_espn_events, ranking_snapshot, prediction_window,
    parse_main_fixtures, parse_espn_opening_1x2, parse_opening_1x2, predicted_outcome, public_record, rolling_backtest, score_prediction,
    settle_margin, split_quarter_line, summarize_market_rows, update_handicap_snapshot, update_prediction_archive,
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


class EquivalentHandicapTests(unittest.TestCase):
    def test_opening_odds_and_american_conversion(self):
        row = {"B365H": "2.10", "B365D": "3.40", "B365A": "3.60", "B365CH": "9.99"}
        odds = parse_opening_1x2(row)
        self.assertEqual((odds["home"], odds["draw"], odds["away"]), (2.10, 3.40, 3.60))
        self.assertEqual(american_to_decimal("+110"), 2.10)
        self.assertEqual(american_to_decimal("-200"), 1.50)
        competition = {"odds": [{"provider": {"name": "DraftKings"}, "moneyline": {
            side: {"open": {"odds": opening}, "close": {"odds": closing}}
            for side, opening, closing in (("home", "+110", "+150"), ("draw", "+210", "+250"), ("away", "+275", "+300"))
        }}]}
        espn = parse_espn_opening_1x2(competition)
        self.assertAlmostEqual(espn["home"], 2.10)
        self.assertAlmostEqual(espn["draw"], 3.10)
        self.assertAlmostEqual(espn["away"], 3.75)
        self.assertIsNone(parse_espn_opening_1x2({"odds": [None]}))

    def test_poisson_fit_has_correct_sign_and_no_invented_asian_odds(self):
        balanced = fit_poisson_to_1x2({"home": 2.0, "draw": 2.0, "away": 2.0, "source": "test"})
        self.assertEqual(balanced["home_line"], 0)
        favorite = fit_poisson_to_1x2({"home": 1.50, "draw": 4.20, "away": 6.50, "source": "test"})
        self.assertLess(favorite["home_line"], 0)
        self.assertIsNone(favorite["bet365_home_odds"])
        self.assertIsNone(favorite["bet365_away_odds"])
        self.assertLess(favorite["estimation"]["rmse"], .05)
        self.assertAlmostEqual(sum(favorite["estimation"]["normalized_probabilities"].values()), 1)

    def test_real_handicap_replaces_simulated_and_started_match_is_not_backfilled(self):
        now = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)
        record = {"kickoff_utc": "2026-11-22T12:00:00+00:00",
                  "opening_1x2": {"home": 1.5, "draw": 4.2, "away": 6.5, "source": "test"}}
        update_handicap_snapshot(record, None, now)
        self.assertEqual(record["handicap_source"], "simulated")
        self.assertLess(record["asian_handicap"]["home_line"], 0)
        real = {"home_line": -.75, "bet365_home_odds": 1.9, "source": "football-data fixtures.csv"}
        update_handicap_snapshot(record, real, now)
        self.assertEqual(record["handicap_source"], "real")
        self.assertEqual(record["asian_handicap"]["home_line"], -.75)
        self.assertEqual(record["asian_handicap_history"][0]["source"], "等效换算｜test")
        started = {"kickoff_utc": "2026-11-22T09:00:00+00:00", "opening_1x2": record["opening_1x2"]}
        update_handicap_snapshot(started, None, now)
        self.assertEqual(started["handicap_source"], "unavailable")
        self.assertIsNone(started.get("asian_handicap"))


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
        self.assertNotIn("total_market", result[0])

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

    def test_espn_completed_event_provides_real_final_score(self):
        league = {**sample_league(), "id": "SWE", "name": "瑞典超"}
        league["matches"] = league["matches"][:2]
        league["matches"][0].update({"home_source": "Hammarby", "away_source": "Sirius"})
        event = {
            "id": "finished-1", "date": "2026-11-22T16:00Z",
            "status": {"type": {"state": "post", "completed": True}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": "3", "team": {"displayName": "Hammarby IF"}},
                {"homeAway": "away", "score": "1", "team": {"displayName": "IK Sirius"}},
            ]}],
        }
        result = parse_espn_events([{"events": [event]}], league, ["Hammarby", "Sirius"], date(2026, 11, 23))
        self.assertEqual(result[0]["actual"]["home_goals"], 3)
        self.assertEqual(result[0]["actual"]["away_goals"], 1)
        self.assertEqual(result[0]["actual"]["outcome"], "H")
        event["status"] = {"type": {"state": "in", "completed": False}}
        live = parse_espn_events([{"events": [event]}], league, ["Hammarby", "Sirius"], date(2026, 11, 23))
        self.assertNotIn("actual", live[0])

    def test_prediction_window_is_noon_to_noon_and_end_is_exclusive(self):
        start, end = prediction_window(datetime(2026, 11, 22, 4, tzinfo=timezone.utc))
        self.assertEqual(start.isoformat(), "2026-11-22T12:00:00+08:00")
        self.assertEqual(end.isoformat(), "2026-11-23T12:00:00+08:00")
        before_noon, _ = prediction_window(datetime(2026, 11, 22, 3, tzinfo=timezone.utc))
        self.assertEqual(before_noon.isoformat(), "2026-11-21T12:00:00+08:00")
        league = sample_league()
        text = ("Div,Date,Time,HomeTeam,AwayTeam\r\n"
                "E0,22/11/2026,05:00,Alpha,Beta\r\n"
                "E0,22/11/2026,16:00,Gamma,Delta\r\n"
                "E0,23/11/2026,04:00,Alpha,Gamma\r\n")
        result = parse_main_fixtures(text, {"E0": league}, start, end)
        self.assertEqual(len(result), 2)
        self.assertNotIn("2026-11-23T12:00:00+08:00", {r["kickoff_beijing"] for r in result})


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

    def test_positive_ev_direction_is_kept_as_secondary_metric(self):
        prediction = {"expected_goals": {"home": 2, "away": 1}}
        market = {"home_line": 0, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6}
        analysis = compare_handicap(prediction, market)
        self.assertEqual(analysis["ev_side"], "home")
        self.assertGreater(analysis["home_metrics"]["ev"], 0)
        no_ev = compare_handicap(prediction, {"home_line": 0, "bet365_home_odds": 1.01, "bet365_away_odds": 1.01})
        self.assertIsNone(no_ev["ev_side"])

    def analysis(self, home, away, institution):
        return compare_handicap({"expected_goals": {"home": home, "away": away}},
                                {"home_line": institution, "bet365_home_odds": 1.9, "bet365_away_odds": 1.9})

    def test_strength_band_boundaries(self):
        expected = [(-1.25, "home3"), (-1, "home2"), (-.75, "home2"), (-.5, "home1"),
                    (-.25, "home1"), (-.249, "neutral0"), (0, "neutral0"), (.249, "neutral0"),
                    (.25, "away1"), (.74, "away1"), (.75, "away2"), (1.24, "away2"), (1.25, "away3")]
        for value, band in expected:
            with self.subTest(value=value):
                self.assertEqual(strength_band(value)["id"], band)

    def test_exact_rule_engine_evaluates_all_15_lines(self):
        from recommendation_rules import RULE_DEFINITIONS, evaluate_rules
        analysis = self.analysis(2.02, 1.0, -1.75)
        rules = evaluate_rules(analysis, {"market_handicap": -1.5})
        self.assertEqual(len(RULE_DEFINITIONS), 15)
        self.assertEqual([r["rule_id"] for r in rules], list(RULE_DEFINITIONS))
        triggered = [r for r in rules if r["triggered"]]
        self.assertEqual([(r["rule_id"], r["side"], r["line"], r["grade"]) for r in triggered],
                         [("A01", "away", 1.75, "A")])
        self.assertTrue(all(r["front_eligible"] for r in rules))

    def test_exact_rule_examples_and_minus_quarter_split(self):
        cases = [
            (-1.75, 2.02, 1.0, "A01", "away", 1.75, "A"),
            (-1.00, 1.6, 1.1, "B03", "home", -1.00, "B"),
            (-0.25, 1.8, 1.0, "A02", "away", 0.25, "A"),
            (-0.25, 1.0, 1.6, "A03", "home", -0.25, "A"),
            (1.50, 1.0, 2.0, "A04", "away", -1.50, "A"),
        ]
        for institution, home, away, rule_id, side, line, grade in cases:
            with self.subTest(rule_id=rule_id):
                analysis = self.analysis(home, away, institution)
                selected = choose_recommendation(analysis, {"market_handicap": institution})
                self.assertEqual((selected["rule_id"], selected["side"], selected["line"], selected["grade"]),
                                 (rule_id, side, line, grade))

    def test_a_grade_is_featured_but_not_a_profit_guarantee(self):
        analysis = self.analysis(2.02, 1.0, -1.75)
        ranking = {"market_handicap": -1.5}
        selected = choose_recommendation(analysis, ranking)
        self.assertEqual(selected["rule_id"], "A01")
        self.assertEqual(selected["rule_version"], "exact-15-lines-v1")
        featured = choose_integrated_selection(analysis, ranking)
        self.assertEqual(featured["status"], "candidate")
        self.assertIn("不构成收益保证", featured["reason"])
        b_grade = self.analysis(1.8, 1.0, -.5)
        self.assertEqual(choose_integrated_selection(b_grade, {"market_handicap": -.5})["status"], "skip")
        self.assertEqual(choose_recommendation(None, None)["status"], "skip")

    def test_draw_band_is_display_only_and_not_settled(self):
        analysis = self.analysis(1.0, 1.0, 0)
        selected = choose_recommendation(analysis, {"market_handicap": 0})
        self.assertEqual((selected["status"], selected["side"]), ("skip", None))
        record = {
            "predictions": {"stable": {"expected_goals": {"home": 1, "away": 1}, "forecast": forecast(.3, .4, .3)}},
            "actual": {"home_goals": 1, "away_goals": 1, "outcome": "D"},
            "asian_handicap": {"home_line": 0, "bet365_home_odds": 1.9, "bet365_away_odds": 1.9},
            "ranking": {"market_handicap": 0},
        }
        public = public_record(record)
        self.assertNotIn("recommendation", public["market_settlement"])
        self.assertNotIn("integrated", public["market_settlement"])

    def test_all_recommendation_does_not_backfill_pregame_tracking(self):
        now = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)
        record = {"kickoff_utc": "2026-11-22T11:00:00+00:00", "status": "scheduled"}
        first = freeze_recommendation_selection(record, {"status": "candidate", "ev": -.02}, now)
        self.assertTrue(first["tracking_eligible"])
        after = freeze_recommendation_selection(record, {"status": "candidate", "ev": .08}, now + timedelta(hours=2))
        self.assertEqual(after["ev"], -.02)
        late_record = {"kickoff_utc": "2026-11-22T11:00:00+00:00", "status": "awaiting_result"}
        retrospective = freeze_recommendation_selection(late_record, {"status": "candidate", "ev": .08}, now + timedelta(hours=2))
        self.assertFalse(retrospective["tracking_eligible"])
        self.assertEqual(retrospective["freeze_status"], "retrospective")

    def test_integrated_selection_freezes_on_first_generation(self):
        now = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)
        record = {"kickoff_utc": "2026-11-22T11:00:00+00:00", "status": "scheduled"}
        first = freeze_integrated_selection(record, {"status": "candidate", "ev": .08}, now)
        self.assertEqual(first["ev"], .08)
        updated = freeze_integrated_selection(record, {"status": "candidate", "ev": .10}, now)
        self.assertEqual(updated["ev"], .08)
        frozen = freeze_integrated_selection(record, {"status": "skip"}, now + timedelta(hours=2))
        self.assertEqual(frozen["ev"], .08)

    def test_finished_record_automatically_builds_handicap_tracking(self):
        record = {
            "league_id": "TEST", "league_name": "测试联赛",
            "predictions": {"stable": {"expected_goals": {"home": 2.02, "away": 1.0}, "forecast": forecast()}},
            "actual": {"home_goals": 2, "away_goals": 0, "outcome": "H"},
            "asian_handicap": {"home_line": -1.75, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6},
            "ranking": {"home_rank": 1, "away_rank": 2, "league_team_count": 4,
                        "rank_difference": 1, "rank_ratio": .25, "market_handicap": -1.5},
            "total_market": {"line": 2.5, "bet365_over_odds": 2.0, "bet365_under_odds": 1.5},
        }
        public = public_record(record)
        self.assertEqual(public["integrated_selection"]["status"], "candidate")
        self.assertIn("recommendation", public["market_settlement"])
        self.assertIn("integrated", public["market_settlement"])
        self.assertEqual(public["recommendation_selection"]["rule_version"], "exact-15-lines-v1")
        self.assertEqual(public["integrated_selection"]["rule_version"], "exact-15-lines-v1-grade-a")
        self.assertEqual(len(public["rule_evaluations"]), 15)
        self.assertIn("A01", public["market_settlement"]["rule_settlements"])
        self.assertFalse(public["recommendation_selection"]["tracking_eligible"])
        self.assertEqual(public["featured_selection"], public["integrated_selection"])
        self.assertEqual(public["market_settlement"]["asian_handicap"]["result"], "half_loss")
        self.assertNotIn("total_market", public)
        self.assertNotIn("total_analysis", public)
        self.assertNotIn("total_goals", public["market_settlement"])
        tracking = build_market_tracking([public])
        ah = tracking["asian_handicap"]["overall"]
        self.assertEqual((ah["n"], ah["effective_win_rate"]), (1, 0))
        self.assertAlmostEqual(ah["roi"], -.5)
        self.assertNotIn("total_goals", tracking)
        self.assertEqual(tracking["integrated"]["overall"]["n"], 0)
        self.assertEqual(tracking["recommendation"]["overall"]["n"], 0)
        public["market_settlement"]["recommendation"]["tracking_eligible"] = True
        formal = build_market_tracking([public])
        self.assertEqual(formal["integrated"]["overall"]["n"], 0)
        self.assertEqual(formal["recommendation"]["by_league"]["TEST"]["overall"]["n"], 1)
        for settlement in public["market_settlement"]["rule_settlements"].values():
            settlement["tracking_eligible"] = True
        with_rules = build_market_tracking([public])
        self.assertEqual(with_rules["rules"]["A01"]["overall"]["n"], 1)
        self.assertEqual(with_rules["rules"]["A01"]["overall"]["effective_win_rate"], 0)
        self.assertEqual(with_rules["rules"]["A01"]["by_source"]["real"]["n"], 1)
        self.assertEqual(with_rules["rules"]["A01"]["by_source"]["simulated"]["n"], 0)
        self.assertEqual(with_rules["recommendation"]["by_league"]["TEST"]["by_source"]["real"]["n"], 1)
        self.assertEqual(len(with_rules["rule_definitions"]), 15)


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
        archive = update_prediction_archive(archive, [], later_dataset, self.now + timedelta(days=2))
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
        archive = update_prediction_archive({"version": 1, "matches": {}}, [fixture], self.dataset, self.now)
        key = next(iter(archive["matches"]))
        original = copy.deepcopy(archive["matches"][key]["predictions"])
        changed = copy.deepcopy(fixture)
        changed["asian_handicap"] = {"home_line": -.75, "bet365_home_odds": 1.95}
        archive = update_prediction_archive(archive, [changed], self.dataset, self.now + timedelta(hours=1))
        record = archive["matches"][key]
        self.assertEqual(record["predictions"], original)
        self.assertEqual(record["asian_handicap"]["home_line"], -.75)
        self.assertEqual(len(record["asian_handicap_history"]), 1)
        self.assertEqual(record["asian_handicap_history"][0]["home_line"], -.5)
        self.assertTrue(record["asian_handicap"]["captured_at"].endswith("+00:00"))
        self.assertNotIn("total_market", record)

    def test_ranking_uses_only_matches_before_kickoff_date(self):
        before = ranking_snapshot(self.fixture, self.league)
        later = copy.deepcopy(self.league)
        later["matches"].append({"date": "2026-11-21", "time": "23:00", "home": "Alpha", "away": "Beta",
                                 "home_source": "Alpha", "away_source": "Beta", "home_goals": 9, "away_goals": 0})
        after = ranking_snapshot(self.fixture, later)
        self.assertEqual(before, after)
        self.assertEqual(before["rank_ratio"], before["rank_difference"] / before["league_team_count"])
        expected = .8 * before["season_market"] + .2 * before["recent6_market"]
        self.assertAlmostEqual(before["market_handicap"], expected)
        self.assertEqual(before["market_version"], "points_form20")
        self.assertGreaterEqual(min(before["home_played"], before["away_played"]), 7)

    def test_espn_final_score_settles_without_rewriting_frozen_prediction(self):
        archive = update_prediction_archive({"version": 1, "matches": {}}, [self.fixture], self.dataset, self.now)
        key = next(iter(archive["matches"]))
        original = copy.deepcopy(archive["matches"][key]["predictions"])
        finished = {**self.fixture, "source_event_id": "different-espn-event-id", "actual": {
            "date": "2026-11-21", "home_goals": 2, "away_goals": 2,
            "outcome": "D", "source": "ESPN scoreboard",
        }}
        archive = update_prediction_archive(archive, [], self.dataset, self.now + timedelta(days=2),
                                            result_fixtures=[finished])
        record = archive["matches"][key]
        self.assertEqual(record["predictions"], original)
        self.assertEqual(record["status"], "finished")
        self.assertEqual(record["actual"]["home_goals"], 2)
        self.assertEqual(record["actual"]["away_goals"], 2)
        self.assertEqual(record["actual"]["source"], "ESPN scoreboard")

    def test_result_matching_allows_timezone_date_difference(self):
        fixture = copy.deepcopy(self.fixture)
        match = self.league["matches"][0]
        match.update({"home_source": "Alpha", "away_source": "Beta", "date": "2026-11-21"})
        found = find_result(fixture, self.league)
        self.assertIs(found, match)


class PageContractTests(unittest.TestCase):
    def test_mobile_page_uses_three_handicaps_without_rule_labels(self):
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        self.assertIn("市场盘口", html)
        self.assertIn("模型盘口", html)
        self.assertIn("机构盘口", html)
        self.assertIn("Market Signal Model", html)
        self.assertIn('<option value="SIGNAL">模型信号</option>', html)
        self.assertIn("status==='SIGNAL'", html)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", html)
        self.assertNotIn("盘口规则与验证口径", html)
        self.assertNotIn("15条", html)
        self.assertNotIn("rule_id", html)
        self.assertNotIn("threshold", html)
        self.assertNotIn("市场让球", html)
        self.assertNotIn("排名与差值明细", html)
        self.assertNotIn("consensus_type", html)
        self.assertNotIn("featured_class", html)

    def test_mobile_page_has_symmetric_metrics_and_direction_consistent_scores(self):
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        self.assertIn("主胜概率", html)
        self.assertIn("平局概率", html)
        self.assertIn("客胜概率", html)
        self.assertIn("参考比分", html)
        self.assertIn("slice(0,3)", html)
        self.assertIn("score-item", html)
        self.assertIn("analysis-table", html)
        self.assertIn("versus-grid", html)
        self.assertIn("模型信号", html)
        self.assertIn("级信号", html)
        self.assertIn("allow=home?(h,a)=>h>a:(h,a)=>a>h", html)
        self.assertIn("分析依据", html)
        self.assertIn("function professionalReason", html)
        self.assertIn("主客场表现、预期进球、联赛排名与盘口定价", html)
        self.assertIn("${referenceScoresHtml(r)}", html)
        self.assertNotIn("触发规则", html)
        self.assertNotIn("阈值", html)
        self.assertNotIn("${base.rule_id}", html)
        self.assertNotIn("base.threshold", html)

    def test_research_and_settlement_pages_use_public_signal_names(self):
        research = Path(__file__).with_name("research.html").read_text(encoding="utf-8")
        predictions = Path(__file__).with_name("predictions.html").read_text(encoding="utf-8")
        self.assertIn("研究档案 V5", research)
        self.assertIn("当前上线：Market Signal Model", research)
        self.assertIn("consensus-summary", research)
        self.assertIn("Market Signal Model", predictions)
        self.assertIn("A/B/C 分档赛后表现", predictions)
        self.assertIn('<option value="ALL">全部档位</option>', predictions)
        self.assertIn("ALL:'全部档位'", predictions)
        self.assertIn("function combineSummaries", predictions)
        self.assertIn("function netWins", predictions)
        self.assertIn("净胜场 = 全赢＋0.5×半赢－全输－0.5×半输", predictions)
        self.assertIn("table-layout:fixed", predictions)
        self.assertIn("五季合并训练", predictions)
        self.assertIn("data/public_predictions.json", predictions)
        self.assertIn("data/market_signal_backtest.json", predictions)
        self.assertIn("by_source", research)
        self.assertIn("handicap-source-method", research)
        for forbidden in ("15条", "rule_id", "threshold", "handicap-source", "by_source", "exact15_backtest"):
            self.assertNotIn(forbidden, predictions)
        self.assertNotIn("精选A", predictions)


if __name__ == "__main__":
    unittest.main()
