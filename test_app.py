import unittest

from model import Match, WEIGHTS, analyze, league_baselines, poisson_forecast, team_profile
from scraper import parse_league_schedule, parse_standings, parse_team_schedule


class ParserTests(unittest.TestCase):
    def test_standings(self):
        html = '''<table><tr class="dp-row"><td>1</td><td><a href="/team/513">阿森纳</a></td>
        <td>11</td><td>9</td><td>1</td><td>1</td><td>17/7</td><td>+10</td><td>28</td></tr></table>'''
        rows = parse_standings(html)
        self.assertEqual(rows[0]["id"], 513)
        self.assertEqual(rows[0]["goals_for"], 17)

    def test_league_schedule(self):
        html = '''<a class="dp-schedule-row"><span class="dp-schedule-row__time">2026-08-22 03:00</span>
        <span class="dp-schedule-row__status--ended">已结束</span><span class="dp-schedule-row__team--home">阿森纳</span>
        <span class="dp-schedule-row__score">3 - 0</span><span class="dp-schedule-row__team--away">考文垂</span></a>
        <a class="dp-schedule-row"><span class="dp-schedule-row__team--home">甲</span>
        <span class="dp-schedule-row__score">-</span><span class="dp-schedule-row__team--away">乙</span></a>'''
        rows = parse_league_schedule(html, "英超")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].home_goals, rows[0].away_goals), (3, 0))

    def test_team_schedule_filters_competition(self):
        html = '''<div class="tp-schedule-row"><span class="tp-schedule-row__comp">英超</span>
        <span class="tp-schedule-row__time">11/22 01:30</span><span class="tp-schedule-row__team--home">纽卡斯尔联</span>
        <span class="tp-schedule-row__score">2 - 2</span><span class="tp-schedule-row__team--away">阿森纳</span></div>
        <div class="tp-schedule-row"><span class="tp-schedule-row__comp">欧冠</span>
        <span class="tp-schedule-row__team--home">阿森纳</span><span class="tp-schedule-row__score">3 - 0</span>
        <span class="tp-schedule-row__team--away">里尔</span></div>'''
        rows = parse_team_schedule(html, "英超")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].home, "纽卡斯尔联")


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.matches = [
            Match("测试联赛", "甲", "乙", 2, 1),
            Match("测试联赛", "乙", "甲", 0, 1),
            Match("测试联赛", "甲", "乙", 1, 1),
            Match("测试联赛", "乙", "甲", 2, 0),
        ]

    def test_baselines(self):
        base = league_baselines(self.matches)
        self.assertAlmostEqual(base["mu_home"], 1.25)
        self.assertAlmostEqual(base["mu_away"], 0.75)

    def test_three_layers_and_weights(self):
        profile = team_profile(self.matches, "甲", "home", league_baselines(self.matches))
        self.assertEqual(profile["layers"]["venue"]["n"], 2)
        self.assertEqual(profile["layers"]["all"]["n"], 4)
        self.assertEqual(profile["layers"]["recent"]["n"], 3)
        self.assertEqual(sum(x["weight"] for x in profile["layers"].values()), 1.0)
        self.assertEqual(WEIGHTS, {"venue": .5, "all": .4, "recent": .1})

    def test_probabilities_are_consistent(self):
        result = poisson_forecast(1.5, 1.0)
        total = result["home_win"] + result["draw"] + result["away_win"]
        self.assertAlmostEqual(total, result["coverage"], places=10)
        self.assertGreater(result["coverage"], .999)
        self.assertGreaterEqual(result["top_scores"][0]["probability"], result["top_scores"][1]["probability"])

    def test_analyze_returns_traceable_lambdas(self):
        result = analyze(self.matches, "甲", "乙")
        h = result["expected_goals"]["home"]
        expected = result["home_profile"]["gf"] * result["away_profile"]["ga"] / result["baselines"]["mu_home"]
        self.assertAlmostEqual(h, expected)


if __name__ == "__main__":
    unittest.main()
