"""四赛季七档规则真实回测；历史快照只重映射冻结的V2B市场盘，不调参。"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from prediction_pipeline import settle_margin, summarize_market_rows
from season_backtest import LEAGUES, build_league_samples, parse_season_csv
from tier_rules import RULE_DEFINITIONS, evaluate_tier_rules, handicap_tier

ROOT = Path(__file__).resolve().parent
BACKTEST_ROOT = ROOT / "data" / "backtests"
OUT = BACKTEST_ROOT / "seven-tier-model-anchor-2022-26"
HISTORICAL = {
    "2022-23": BACKTEST_ROOT / "three-season-five-leagues-2022-25" / "matches_points_venue25_2022_23.json",
    "2023-24": BACKTEST_ROOT / "cross-season-five-leagues-2023-24" / "matches_points_venue25.json",
    "2024-25": BACKTEST_ROOT / "market-v2-five-leagues-2024-25" / "matches_points_venue25.json",
}


def v2b(sample: dict) -> dict:
    season, recent = sample["season_market"], sample["recent6_market"]
    if season is None or recent is None:
        raise ValueError(f"V2B字段缺失：{sample['match_id']}")
    return {**sample, "market_handicap": .8 * season + .2 * recent, "market_version": "points_form20"}


def rule_rows(sample: dict, season: str) -> list[dict]:
    rows = []
    evaluations = evaluate_tier_rules(sample["model_handicap"], sample["market_handicap"], sample["institution_handicap"])
    for rule in evaluations:
        if not rule["triggered"]:
            continue
        side = rule["side"]
        settlement = sample.get("settlements", {}).get(side)
        if not settlement:
            continue
        rows.append({
            "season": season, "match_id": sample["match_id"], "date": sample["date"],
            "time": sample.get("time"), "league_id": sample["league_id"], "league_name": sample["league_name"],
            "home": sample["home"], "away": sample["away"], "rule_id": rule["rule_id"],
            "rule_name": rule["rule_name"], "side": side,
            "line": sample["institution_handicap"] if side == "home" else -sample["institution_handicap"],
            "odds": settlement["odds"], "handicap_source": "real",
            "market_handicap": sample["market_handicap"], "model_handicap": sample["model_handicap"],
            "institution_handicap": sample["institution_handicap"],
            "tiers": {key: handicap_tier(value)["id"] for key, value in (
                ("model", sample["model_handicap"]), ("market", sample["market_handicap"]),
                ("institution", sample["institution_handicap"]))},
            "tracking_eligible": True,
            **{key: settlement[key] for key in ("result", "win_eq", "loss_eq", "push_eq", "unit_profit")},
        })
    return rows


def summarize(season: str, samples: list[dict], rows: list[dict], raw_matches: int, excluded: int) -> dict:
    by_rule = {}
    for rule_id, definition in RULE_DEFINITIONS.items():
        selected = [row for row in rows if row["rule_id"] == rule_id]
        by_league = {}
        for league_id, league_name in LEAGUES.items():
            items = [row for row in selected if row["league_id"] == league_id]
            by_league[league_id] = {"league_name": league_name, **summarize_market_rows(items)}
        by_rule[rule_id] = {**definition, "overall": summarize_market_rows(selected), "by_league": by_league}
    dates = [sample["date"] for sample in samples]
    return {"season": season, "raw_matches": raw_matches, "valid_samples": len(samples),
            "excluded": excluded, "first_valid_date": min(dates) if dates else None,
            "last_valid_date": max(dates) if dates else None, "rules": by_rule}


def download_2025_26() -> tuple[list[dict], dict]:
    source_dir = OUT / "source" / "2526"
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"season": "2025-26", "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": {}}
    all_samples, excluded = [], []
    for league_id in LEAGUES:
        url = f"https://www.football-data.co.uk/mmz4281/2526/{league_id}.csv"
        path = source_dir / f"{league_id}.csv"
        if not path.exists():
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read()
            path.write_bytes(payload)
        payload = path.read_bytes()
        rows = parse_season_csv(payload.decode("utf-8-sig"), league_id)
        samples, league_excluded = build_league_samples(rows, market_version="points_form20")
        all_samples.extend(samples)
        excluded.extend(league_excluded)
        manifest["files"][league_id] = {"url": url, "sha256": hashlib.sha256(payload).hexdigest(),
                                        "bytes": len(payload), "parsed_matches": len(rows),
                                        "valid_samples": len(samples), "excluded": len(league_excluded)}
        print(f"2025-26 {league_id}: raw={len(rows)} valid={len(samples)} excluded={len(league_excluded)}", flush=True)
    all_samples.sort(key=lambda row: (row["date"], row.get("time") or "23:59", row["match_id"]))
    (OUT / "source" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return all_samples, {"manifest": manifest, "excluded": excluded}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows, reports, normalized_samples = [], {}, {}
    for season, path in HISTORICAL.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        normalized = [v2b(sample) for sample in data["samples"]]
        samples = [sample for sample in normalized if all(side in sample.get("settlements", {}) for side in ("home", "away"))]
        missing_odds = len(normalized) - len(samples)
        rows = [row for sample in samples for row in rule_rows(sample, season)]
        reports[season] = summarize(season, samples, rows,
                                    raw_matches=len(normalized) + len(data.get("excluded", [])),
                                    excluded=len(data.get("excluded", [])) + missing_odds)
        all_rows.extend(rows)
        normalized_samples[season] = samples
        print(f"{season}: valid={len(samples)} " + " ".join(
            f"{rule_id}={reports[season]['rules'][rule_id]['overall']['n']}" for rule_id in RULE_DEFINITIONS), flush=True)
    samples_2526, source_info = download_2025_26()
    rows = [row for sample in samples_2526 for row in rule_rows(sample, "2025-26")]
    reports["2025-26"] = summarize("2025-26", samples_2526, rows,
                                   raw_matches=sum(f["parsed_matches"] for f in source_info["manifest"]["files"].values()),
                                   excluded=len(source_info["excluded"]))
    all_rows.extend(rows)
    normalized_samples["2025-26"] = samples_2526
    print(f"2025-26: valid={len(samples_2526)} " + " ".join(
        f"{rule_id}={reports['2025-26']['rules'][rule_id]['overall']['n']}" for rule_id in RULE_DEFINITIONS), flush=True)
    combined = {"valid_samples": sum(report["valid_samples"] for report in reports.values()),
                "raw_matches": sum(report["raw_matches"] for report in reports.values()), "rules": {}}
    for rule_id, definition in RULE_DEFINITIONS.items():
        selected = [row for row in all_rows if row["rule_id"] == rule_id]
        combined["rules"][rule_id] = {**definition, "overall": summarize_market_rows(selected)}
    result = {
        "schema": "model-anchor-seven-tiers-backtest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anchor": "model", "market_version": "points_form20",
        "market_formula": "V2B=赛季场均积分盘80%＋近6场场均积分盘20%",
        "tier_rule": "≤-1.25主3；(-1.25,-.75]主2；(-.75,-.25]主1；(-.25,.25)0；[.25,.75)客1；[.75,1.25)客2；≥1.25客3",
        "settlement": "真实AHh与Bet365赛前赔率；四分之一盘全赢/赢半/走盘/输半/全输；胜率排除走盘，ROI分母含全部触发场",
        "seasons": reports, "combined": combined,
        "source_2025_26": source_info["manifest"],
    }
    (OUT / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "selections.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "normalized_samples.json").write_text(json.dumps(normalized_samples, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (ROOT / "data" / "seven_tier_backtest.json").write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(combined, ensure_ascii=False, indent=2), flush=True)
    print(f"WROTE {OUT / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
