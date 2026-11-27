"""磐石·强主四重确认：赛前Elo及唯一S级触发条件。"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RULE_ID="PANSHI_HOME_QUAD_V1"
RULE_NAME="磐石·强主四重确认"
ELO_CONFIG={"start":1500.0,"home_advantage":60.0,"K":20.0}
BOOTSTRAP_PATH=ROOT/"data/elo_bootstrap_2026_27.json"

def evaluate_panshi(model_handicap,institution_line,home_ev,elo_difference,real_asian):
    """可用亚盘提供结算盘口、赔率与风险否决，不独立决定方向。"""
    values=(model_handicap,institution_line,home_ev,elo_difference)
    if not real_asian or any(v is None for v in values):return None
    if model_handicap<=-1.0 and elo_difference>=150 and home_ev>=.05 and model_handicap-institution_line>-.50:
        return {"rule_id":RULE_ID,"rule_name":RULE_NAME,"side":"home","level":"S",
                "n_all":178,"wr_all":91.5/153.5,"roi_all":27.425/178,
                "reason":"磐石·强主四重确认：模型方向、赛前Elo、真实赔率EV及机构风险过滤同时通过。"}
    return None

def _match_key(date,home,away):return date,home,away

class EloFeatureMap(dict):
    pass

def elo_feature_map(league,bootstrap):
    """从赛季初评分推进；同一源日期全部比赛结束后才统一更新。"""
    initial=dict((bootstrap.get("leagues",{}).get(league["id"],{}) or {}).get("ratings",{}))
    ratings=defaultdict(lambda:ELO_CONFIG["start"],initial)
    by_date=defaultdict(list)
    for order,m in enumerate(league.get("matches",[])):
        by_date[m["date"]].append((order,m))
    output=EloFeatureMap()
    output.initial=initial
    output.snapshots={}
    for day in sorted(by_date):
        output.snapshots[day]=dict(ratings)
        games=sorted(by_date[day],key=lambda x:(x[1].get("time") or "23:59",x[0]))
        for _,m in games:
            home,away=m["home_source"],m["away_source"]
            output[_match_key(day,home,away)]=ratings[home]+ELO_CONFIG["home_advantage"]-ratings[away]
        for _,m in games:
            home,away=m["home_source"],m["away_source"]
            expected=1/(1+10**(-(ratings[home]+ELO_CONFIG["home_advantage"]-ratings[away])/400))
            hg,ag=m["home_goals"],m["away_goals"]
            score=1.0 if hg>ag else .5 if hg==ag else 0.0
            delta=ELO_CONFIG["K"]*(score-expected)
            ratings[home]+=delta;ratings[away]-=delta
    return output

def build_elo_maps(dataset,bootstrap=None):
    if bootstrap is None:
        try:bootstrap=json.loads(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
        except (OSError,ValueError):bootstrap={"leagues":{}}
    return {league["id"]:elo_feature_map(league,bootstrap) for league in dataset.get("leagues",[])}

def freeze_elo(record,fixture,feature_map,now):
    """仅在开球前冻结；赛后或历史档案不得回填成赛前S信号。"""
    if record.get("elo_difference") is not None:return
    kickoff=datetime.fromisoformat(record["kickoff_utc"])
    if kickoff<=now:return
    day=fixture.get("source_date") or kickoff.date().isoformat()
    key=_match_key(day,fixture["home_source"],fixture["away_source"])
    value=feature_map.get(key)
    if value is None:
        # 北京日期与赛事源日期不同：用严格早于该日的评分快照，绝不借用历史交锋差。
        earlier=[d for d in feature_map.snapshots if d<day]
        ratings=feature_map.snapshots[max(earlier)] if earlier else feature_map.initial
        value=ratings.get(fixture["home_source"],ELO_CONFIG["start"])+ELO_CONFIG["home_advantage"]-ratings.get(fixture["away_source"],ELO_CONFIG["start"])
    if value is not None:
        record["elo_difference"]=float(value)
        record["elo_frozen_at"]=now.isoformat(timespec="seconds")
