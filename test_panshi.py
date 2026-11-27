import json
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from panshi import evaluate_panshi,elo_feature_map,freeze_elo,RULE_ID
from league_pattern import evaluate_pattern
from public_site_data import PATTERN_FIELDS

class PanshiRuleTest(unittest.TestCase):
    def test_real_asian_quad_confirmation(self):
        hit=evaluate_panshi(-1.2,-1.0,.06,160,True)
        self.assertEqual(hit['rule_id'],RULE_ID);self.assertEqual(hit['side'],'home')
        result=evaluate_pattern('TEST',-1.2,-1.0,160,.06,True)
        self.assertEqual(result['level'],'S');self.assertEqual(result['rule_count'],1)
    def test_converted_market_and_missing_elo_never_trigger(self):
        self.assertIsNone(evaluate_panshi(-1.2,-1.0,.06,160,False))
        self.assertIsNone(evaluate_panshi(-1.2,-1.0,.06,None,True))
        self.assertEqual(evaluate_pattern('TEST',-1.2,-1.0,160,.06,False)['status'],'skip')
    def test_each_gate_is_mandatory(self):
        for args in [(-.99,-1,.1,200),(-1.2,-1,.049,200),(-1.2,-1,.1,149),(-1.8,-1,.1,200)]:
            self.assertIsNone(evaluate_panshi(*args,True))
    def test_conflict_is_removed_and_same_direction_is_one_bet(self):
        away={'id':'X','league':'TEST','side':'away','kind':'exact','params':{'line':-1,'threshold':0},'grade':'A','n':10,'wr':50,'roi':10}
        with patch('league_pattern.BY_LEAGUE',{'TEST':[away]}):
            result=evaluate_pattern('TEST',-1.2,-1,200,.1,True)
        self.assertEqual(result['status'],'skip');self.assertIn('冲突',result['reason'])
        home={'id':'H','league':'TEST','side':'home','kind':'exact','params':{'line':-1,'threshold':-2},'grade':'A','n':10,'wr':50,'roi':10}
        with patch('league_pattern.BY_LEAGUE',{'TEST':[home]}):
            result=evaluate_pattern('TEST',-1.2,-1,200,.1,True)
        self.assertEqual(result['level'],'S');self.assertEqual(result['side'],'home');self.assertEqual(result['rule_count'],2);self.assertEqual(result['confirmation_count'],2)

class PanshiEloTest(unittest.TestCase):
    def bootstrap(self):return {'leagues':{'TEST':{'ratings':{'H':2000,'A':1000}}}}
    def test_pre_kickoff_freeze_and_no_backfill(self):
        league={'id':'TEST','matches':[]};features=elo_feature_map(league,self.bootstrap())
        fixture={'source_date':'2026-11-28','home_source':'H','away_source':'A'}
        record={'kickoff_utc':'2026-11-28T12:00:00+00:00'}
        freeze_elo(record,fixture,features,datetime(2026,11,27,tzinfo=timezone.utc));self.assertEqual(record['elo_difference'],1060)
        old={'kickoff_utc':'2026-11-26T12:00:00+00:00'};freeze_elo(old,fixture,features,datetime(2026,11,27,tzinfo=timezone.utc));self.assertNotIn('elo_difference',old)
    def test_same_day_results_update_only_after_all_features(self):
        league={'id':'TEST','matches':[{'date':'2026-01-01','home_source':'H','away_source':'A','home_goals':3,'away_goals':0},{'date':'2026-01-01','home_source':'C','away_source':'D','home_goals':0,'away_goals':1},{'date':'2026-01-02','home_source':'H','away_source':'B','home_goals':1,'away_goals':0}]}
        f=elo_feature_map(league,{'leagues':{}})
        self.assertEqual(f['2026-01-01','H','A'],60);self.assertEqual(f['2026-01-01','C','D'],60);self.assertGreater(f['2026-01-02','H','B'],60)
    def test_public_fields_do_not_expose_private_inputs(self):
        self.assertNotIn('elo_difference',PATTERN_FIELDS);self.assertNotIn('home_ev',PATTERN_FIELDS)
        report=json.loads((Path(__file__).parent/'data/fusion_report.json').read_text())
        self.assertEqual(report['grades']['S']['overall']['n'],2105);self.assertEqual(report['version'],'fusion-panshi-35-7-v8')
        self.assertEqual(report['double']['n'],1954)
        self.assertEqual(report['triple']['n'],10)
        self.assertEqual(report['current']['net_wins'],29)
        self.assertEqual(report['cutoff_utc'],'2026-11-27T04:00:00+00:00')
        self.assertTrue(all(row['date']<='2026-11-27' for row in report['current']['matches']))
    def test_converted_market_can_trigger_panshi_in_refresh_path(self):
        from refresh_validated_public import refresh
        record={'league_id':'TEST','handicap_source':'simulated','elo_difference':160,
                'featured_prediction':{'expected_goals':{'home':2.2,'away':1.0}},
                'handicap_analysis':{'home_metrics':{'ev':.06}},
                'asian_handicap':{'home_line':-1.0,'home_odds':1.9,'away_odds':1.9,'source':'等效换算｜test'}}
        refresh(record)
        self.assertEqual(record['pattern_selection']['level'],'S')
        self.assertEqual(record['pattern_selection']['side'],'home')
        self.assertEqual(record['pattern_selection']['handicap_source'],'1x2_converted')
        self.assertEqual(record['pattern_selection']['odds'],1.9)
if __name__=='__main__':unittest.main()
