"""公开 CSV 解析与静态数据生成测试。"""
import unittest
from datetime import datetime, timezone

from generate_data import LEAGUES, TEAM_ZH, extra_season, localize_matches, parse_extra_matches, parse_matches, season_code, standings


SAMPLE = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\r
E0,01/08/2026,20:00,A Team,B Team,2,1,H\r
E0,08/08/2026,15:00,B Team,A Team,0,0,D\r
E0,09/08/2026,15:00,A Team,C Team,3,1,H\r
E0,10/08/2026,15:00,C Team,B Team,,,\r
"""


SAMPLE_EXTRA = """Country,League,Season,Date,Time,Home,Away,HG,AG,Res\r
Sweden,Allsvenskan,2025,01/04/2025,18:00,AIK,Malmo FF,1,0,H\r
Sweden,Allsvenskan,2026,01/04/2026,18:00,AIK,Malmo FF,2,2,D\r
Sweden ,Allsvenskan,2026,08/04/2026,18:00,Malmo FF,AIK,0,1,A\r
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

    def test_team_names_are_translated_without_changing_results(self):
        raw = [{"home": "Arsenal", "away": "Brighton", "home_goals": 2, "away_goals": 1}]
        translated = localize_matches(raw)[0]
        self.assertEqual((translated["home"], translated["away"]), ("阿森纳", "布莱顿"))
        self.assertEqual((translated["home_source"], translated["away_source"]), ("Arsenal", "Brighton"))
        self.assertEqual((translated["home_goals"], translated["away_goals"]), (2, 1))

    def test_unknown_future_team_keeps_english_name(self):
        translated = localize_matches([{"home": "New Club", "away": "Arsenal"}])[0]
        self.assertEqual(translated["home"], "New Club")
        self.assertEqual(translated["away"], "阿森纳")

    def test_additional_mainstream_leagues_and_translations(self):
        ids = {league["id"] for league in LEAGUES}
        self.assertEqual(len(LEAGUES), 30)
        self.assertTrue({"N1", "P1", "B1", "SC0", "T1", "G1"}.issubset(ids))
        self.assertTrue({"E1", "SP2", "I2", "D2", "F2", "SWE", "SWZ", "JPN", "USA"}.issubset(ids))
        self.assertNotIn("KOR", ids)  # KOR.csv 当前实际为挪威数据，禁止错误接入。
        self.assertNotIn("COL", ids)  # COL.csv 当前实际为波兰数据，禁止错误接入。
        self.assertEqual(TEAM_ZH["Ajax"], "阿贾克斯")
        self.assertEqual(TEAM_ZH["Porto"], "波尔图")
        self.assertEqual(TEAM_ZH["Celtic"], "凯尔特人")

    def test_extra_csv_uses_only_current_season(self):
        matches = parse_extra_matches(SAMPLE_EXTRA, "SWE", "2026", "Sweden")
        self.assertEqual(len(matches), 2)
        self.assertTrue(all(m["date"].startswith("2026-") for m in matches))
        self.assertEqual(matches[0]["home_goals"], 2)

    def test_extra_csv_rejects_mislabelled_country_file(self):
        with self.assertRaises(ValueError):
            parse_extra_matches(SAMPLE_EXTRA, "KOR", "2026", "South Korea")

    def test_extra_season_calendar_and_cross_year(self):
        now = datetime(2026, 11, 22, tzinfo=timezone.utc)
        self.assertEqual(extra_season({"season_type": "calendar"}, now), "2026")
        self.assertEqual(extra_season({"season_type": "cross_year"}, now), "2026/2027")

    def test_wrong_league_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_matches(SAMPLE, "SP1")


if __name__ == "__main__":
    unittest.main()
