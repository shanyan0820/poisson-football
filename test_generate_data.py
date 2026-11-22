"""公开 CSV 解析与静态数据生成测试。"""
import unittest
from datetime import datetime, timezone

from generate_data import parse_matches, season_code, standings


SAMPLE = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\r
E0,01/08/2026,20:00,A Team,B Team,2,1,H\r
E0,08/08/2026,15:00,B Team,A Team,0,0,D\r
E0,09/08/2026,15:00,A Team,C Team,3,1,H\r
E0,10/08/2026,15:00,C Team,B Team,,,\r
"""


class GenerateDataTests(unittest.TestCase):
    def test_season_rolls_over_in_july(self):
        self.assertEqual(season_code(datetime(2026, 11, 22, tzinfo=timezone.utc)), "2627")
        self.assertEqual(season_code(datetime(2027, 3, 1, tzinfo=timezone.utc)), "2627")
        self.assertEqual(season_code(datetime(2027, 7, 1, tzinfo=timezone.utc)), "2728")

    def test_only_finished_matches_are_used_and_sorted(self):
        matches = parse_matches(SAMPLE, "E0")
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[-1]["home"], "A Team")
        self.assertEqual(matches[0]["home_goals"], 2)

    def test_standings_are_derived_from_real_results(self):
        table = standings(parse_matches(SAMPLE, "E0"))
        a = next(t for t in table if t["name"] == "A Team")
        self.assertEqual((a["played"], a["points"], a["gf"], a["ga"]), (3, 7, 5, 2))
        self.assertEqual(a["rank"], 1)

    def test_wrong_league_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_matches(SAMPLE, "SP1")


if __name__ == "__main__":
    unittest.main()
