"""用47条保留BIG7叠加版刷新现有赛前档案与公开数据，不重新抓取或改赛果。"""
from __future__ import annotations
import json
from pathlib import Path
from league_pattern import evaluate_pattern, league_gate, PATTERN_VERSION
from odds_to_asian import convert as convert_asian_odds
from public_site_data import build_public_predictions

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "data/predictions.json"
PUBLIC = ROOT / "data/public_predictions.json"


def settle(pattern, actual):
    side = pattern["side"]; line = pattern["line"]
    margin = actual["home_goals"] - actual["away_goals"] if side == "home" else actual["away_goals"] - actual["home_goals"]
    parts = [line] if abs(line * 2 - round(line * 2)) < 1e-9 else [line - .25, line + .25]
    outcomes = [1 if margin + p > 1e-9 else -1 if margin + p < -1e-9 else 0 for p in parts]
    w = outcomes.count(1) / len(parts); l = outcomes.count(-1) / len(parts)
    odds = pattern.get("odds")
    result = {(1., 0.): "full_win", (0., 1.): "full_loss", (.5, 0.): "half_win", (0., .5): "half_loss", (0., 0.): "push", (.5, .5): "win_loss"}[(w, l)]
    return {"result": result, "win_eq": w, "loss_eq": l, "push_eq": 1 - w - l,
            "unit_profit": w * (odds - 1) - l, "odds": odds, "market": "league_pattern",
            "side": side, "line": line, "rule_version": PATTERN_VERSION, "level": pattern["level"], "odds_available": True}


def refresh(record):
    featured = record.get("featured_prediction") or {}
    expected = featured.get("expected_goals") or {}
    handicap = record.get("handicap_analysis") or {}
    model_line = expected["away"] - expected["home"] if expected else (-handicap["model_goal_difference"] if handicap.get("model_goal_difference") is not None else None)
    asian = record.get("asian_handicap") or {}
    converted = convert_asian_odds(record.get("opening_1x2"))
    asian_complete = asian.get("home_line") is not None and asian.get("bet365_home_odds") and asian.get("bet365_away_odds")
    if asian_complete:
        institution_line, source = asian["home_line"], "asian"
    elif converted:
        institution_line, source = converted["home_line"], "1x2_converted"
    else:
        institution_line, source = asian.get("home_line"), "asian"
        if institution_line is None and handicap:
            institution_line, source = handicap.get("market_line"), "model"
    pattern = evaluate_pattern(
        record.get("league_id"), model_line, institution_line,
        elo_difference=record.get("elo_difference"),
        home_ev=(handicap.get("home_metrics") or {}).get("ev") if handicap else None,
        real_asian=bool(asian_complete and record.get("handicap_source") == "real"),
    )
    pattern["handicap_source"] = source; pattern["institution_line"] = institution_line
    if pattern.get("status") == "candidate":
        side = pattern["side"]
        if source == "1x2_converted": odds = converted[f"{side}_odds"]
        elif source == "asian": odds = asian.get("bet365_home_odds" if side == "home" else "bet365_away_odds")
        else: odds = (handicap.get(f"{side}_metrics") or {}).get("odds")
        pattern["odds"] = odds
        if odds is None:
            pattern["status"] = "skip"; pattern["reason"] = "本场没有可用的真实盘口赔率，不输出推荐。"
    record["pattern_selection"] = pattern
    record["league_gate"] = league_gate(record.get("league_id"))
    settlement = record.get("market_settlement") or {}
    old = settlement.get("pattern")
    if old is not None:
        if pattern.get("status") == "candidate" and record.get("actual") and pattern.get("odds"):
            new = settle(pattern, record["actual"])
            for key in ("league_id", "league_name", "kickoff_utc", "key", "tracking_eligible"):
                if key in old: new[key] = old[key]
            settlement["pattern"] = new
        else:
            settlement.pop("pattern", None)
    return record


def main():
    raw = json.loads(PATH.read_text())
    backup = ROOT / "data/predictions_pre_validated_v6.json"
    if not backup.exists(): backup.write_text(PATH.read_text(), encoding="utf-8")
    public_backup = ROOT / "data/public_predictions_pre_validated_v6.json"
    if public_backup.exists() is False and PUBLIC.exists(): public_backup.write_text(PUBLIC.read_text(), encoding="utf-8")
    count = 0
    for collection in ("fixtures", "history"):
        for record in raw.get(collection, []):
            refresh(record); count += 1
            assert record["pattern_selection"]["rule_version"] == PATTERN_VERSION
            assert record["pattern_selection"].get("level") in {None, "S", "A", "B", "C"}
    PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    public = build_public_predictions(raw)
    PUBLIC.write_text(json.dumps(public, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"version": PATTERN_VERSION, "records_refreshed": count,
                      "public_candidates": sum(r.get("pattern_selection", {}).get("status") == "candidate" for r in public["fixtures"]),
                      "public_A": sum(r.get("pattern_selection", {}).get("level") == "A" for r in public["fixtures"])}, ensure_ascii=False))


if __name__ == "__main__": main()
