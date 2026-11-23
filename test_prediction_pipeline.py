import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from prediction_pipeline import (
    ESPN_TEAM_ALIASES, MODEL_CONFIGS, base_fixture, build_market_tracking, choose_integrated_selection,
    choose_recommendation, compare_handicap, find_result, strength_band,
    freeze_integrated_selection, freeze_recommendation_selection, global_backtest,
    map_espn_team, outcome, parse_espn_events, ranking_snapshot, prediction_window,
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
        expected = [(-1, "home_big_win"), (-.75, "home_big_win"), (-.74, "home_win"),
                    (-.5, "home_win"), (-.49, "home_unbeaten"), (-.25, "home_unbeaten"),
                    (-.24, "draw"), (0, "draw"), (.24, "draw"), (.25, "away_unbeaten"),
                    (.49, "away_unbeaten"), (.5, "away_win"), (.74, "away_win"),
                    (.75, "away_big_win")]
        for value, band in expected:
            with self.subTest(value=value):
                self.assertEqual(strength_band(value)["id"], band)

    def test_featured_a_requires_all_three_in_same_band(self):
        analysis = self.analysis(1.4, 1.0, -.4)
        selected = choose_recommendation(analysis, {"market_handicap": -.4})
        self.assertEqual((selected["status"], selected["band_id"]), ("candidate", "home_unbeaten"))
        self.assertEqual(selected["consensus_type"], "same_band")
        featured = choose_integrated_selection(analysis, {"market_handicap": -.4})
        self.assertEqual((featured["status"], featured["featured_class"]), ("candidate", "A"))
        self.assertEqual(selected["handicap_bands"]["model"]["id"], "home_unbeaten")

    def test_featured_b_uses_same_direction_and_median_band(self):
        analysis = self.analysis(1.6, 1.0, -.25)  # 三盘 -0.40/-0.60/-0.25。
        selected = choose_recommendation(analysis, {"market_handicap": -.4})
        self.assertEqual(selected["consensus_type"], "direction_spread")
        self.assertEqual(selected["band_id"], "home_unbeaten")
        featured = choose_integrated_selection(analysis, {"market_handicap": -.4})
        self.assertEqual((featured["status"], featured["featured_class"]), ("candidate", "B"))
        self.assertAlmostEqual(featured["spread"], .35)

    def test_ordinary_majority_and_median_consensus(self):
        majority = choose_recommendation(self.analysis(2.4, 1.0, -.74), {"market_handicap": -1.3})
        self.assertEqual((majority["consensus_type"], majority["band_id"]), ("majority_adjacent", "home_big_win"))
        self.assertIsNone(majority["featured_class"])
        median = choose_recommendation(self.analysis(1.9, 1.0, -.55), {"market_handicap": -.25})
        self.assertEqual((median["consensus_type"], median["band_id"]), ("median_direction", "home_win"))

    def test_cross_direction_or_large_spread_is_skipped(self):
        crossed = choose_recommendation(self.analysis(1.4, 1.0, .3), {"market_handicap": -.4})
        self.assertEqual((crossed["status"], crossed["consensus_type"]), ("skip", "opposite_direction"))
        wide = choose_recommendation(self.analysis(2.4, 1.0, 0), {"market_handicap": -1.4})
        self.assertEqual((wide["status"], wide["consensus_type"]), ("skip", "extreme_disagreement"))
        self.assertEqual(choose_recommendation(self.analysis(1.4, 1.0, -.4), {})["status"], "skip")

    def test_draw_band_is_display_only_and_not_settled(self):
        analysis = self.analysis(1.0, 1.0, 0)
        selected = choose_recommendation(analysis, {"market_handicap": 0})
        self.assertEqual((selected["status"], selected["side"]), ("candidate", "draw"))
        self.assertIsNone(selected["odds"])
        record = {
            "predictions": {"stable": {"expected_goals": {"home": 1, "away": 1}, "forecast": forecast(.3, .4, .3)}},
            "actual": {"home_goals": 1, "away_goals": 1, "outcome": "D"},
            "asian_handicap": {"home_line": 0, "bet365_home_odds": 1.9, "bet365_away_odds": 1.9},
            "ranking": {"market_handicap": 0},
        }
        public = public_record(record)
        self.assertEqual(public["recommendation_selection"]["band_id"], "draw")
        self.assertNotIn("recommendation", public["market_settlement"])
        self.assertNotIn("integrated", public["market_settlement"])

    def test_recommendation_rule_version_and_missing_handicap(self):
        selected = choose_recommendation(self.analysis(1.4, 1.0, -.4), {"market_handicap": -.4})
        self.assertEqual(selected["rule_version"], "three-line-consensus-v4")
        self.assertEqual(selected["candidate_count"], 1)
        self.assertEqual(choose_recommendation(None, None)["status"], "skip")

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
            "predictions": {"stable": {"expected_goals": {"home": 2, "away": 1}, "forecast": forecast()}},
            "actual": {"home_goals": 2, "away_goals": 0, "outcome": "H"},
            "asian_handicap": {"home_line": -.5, "bet365_home_odds": 2.2, "bet365_away_odds": 1.6},
            "ranking": {"home_rank": 1, "away_rank": 2, "league_team_count": 4,
                        "rank_difference": 1, "rank_ratio": .25, "market_handicap": -1.0},
            "total_market": {"line": 2.5, "bet365_over_odds": 2.0, "bet365_under_odds": 1.5},
        }
        public = public_record(record)
        self.assertEqual(public["integrated_selection"]["status"], "candidate")
        self.assertIn("integrated", public["market_settlement"])
        self.assertIn("recommendation", public["market_settlement"])
        self.assertEqual(public["recommendation_selection"]["rule_version"], "three-line-consensus-v4")
        self.assertEqual(public["integrated_selection"]["rule_version"], "three-line-consensus-featured-v4")
        self.assertFalse(public["recommendation_selection"]["tracking_eligible"])
        self.assertEqual(public["featured_selection"], public["integrated_selection"])
        self.assertEqual(public["market_settlement"]["asian_handicap"]["result"], "full_win")
        self.assertNotIn("total_market", public)
        self.assertNotIn("total_analysis", public)
        self.assertNotIn("total_goals", public["market_settlement"])
        tracking = build_market_tracking([public])
        ah = tracking["asian_handicap"]["overall"]
        self.assertEqual((ah["n"], ah["effective_win_rate"]), (1, 1))
        self.assertAlmostEqual(ah["roi"], 1.2)
        self.assertNotIn("total_goals", tracking)
        self.assertEqual(tracking["integrated"]["overall"]["n"], 0)
        self.assertEqual(tracking["recommendation"]["overall"]["n"], 0)
        public["market_settlement"]["integrated"]["tracking_eligible"] = True
        public["market_settlement"]["recommendation"]["tracking_eligible"] = True
        formal = build_market_tracking([public])
        self.assertEqual(formal["integrated"]["overall"]["n"], 1)
        self.assertEqual(formal["recommendation"]["by_league"]["TEST"]["overall"]["n"], 1)


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
        expected = ((before["home_rank"] - before["away_rank"]) /
                    (before["league_team_count"] - 1) * 2)
        self.assertAlmostEqual(before["market_handicap"], expected)

    def test_result_matching_allows_timezone_date_difference(self):
        fixture = copy.deepcopy(self.fixture)
        match = self.league["matches"][0]
        match.update({"home_source": "Alpha", "away_source": "Beta", "date": "2026-11-21"})
        found = find_result(fixture, self.league)
        self.assertIs(found, match)


class PageContractTests(unittest.TestCase):
    def test_mobile_page_uses_market_name_without_rank_detail(self):
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        self.assertIn("市场让球", html)
        self.assertIn("三盘共识规则 V4", html)
        self.assertNotIn("排名让球", html)
        self.assertNotIn("排名与差值明细", html)
        self.assertNotIn("ranking_handicap", html)

    def test_research_page_is_v4(self):
        html = Path(__file__).with_name("research.html").read_text(encoding="utf-8")
        self.assertIn("研究模型 V4", html)
        self.assertIn("consensus-summary", html)


if __name__ == "__main__":
    unittest.main()
