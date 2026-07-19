#!/usr/bin/env python3
"""
게임주 '신작 출시 전 상승' 가설 시장중립 백테스트.

각 신작 출시 이벤트에 대해:
  - 출시 전 상승(run-up): D-30→D-1, D-7→D-1 구간의 종목 수익률 − 벤치마크(ETF) 수익률 = '시장초과수익'
  - 출시 후(sell the news): D0→D+10 시장초과수익
집계: 평균/중앙값 초과수익, beat-market%(초과수익>0 비율). 엣지 판정 기준 beat% > 52%.

숫자는 한투 수정주가로 결정론 계산 → 환각 없음. 이벤트 날짜는 game_launch_events.json(편집).
출력: manddo-site/tools/data/game_launch_backtest.json
"""
import os
import sys
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/Users/mandoo/stock_auto_trade")
from kis_api import KISApi  # noqa: E402

SITE = Path.home() / "manddo-site"
EVENTS = SITE / "scripts" / "game_launch_events.json"
OUT = SITE / "tools" / "data" / "game_launch_backtest.json"
LOG = SITE / "scripts" / "game_launch_backtest.log"


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {m}\n")
    print(m)


def candles_window(api, code, bgn, end):
    """수정주가 일봉 [(YYYYMMDD, close), ...] (오름차순)."""
    data = api._get(
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        "FHKST03010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
         "FID_INPUT_DATE_1": bgn, "FID_INPUT_DATE_2": end,
         "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"},
    )
    rows = [(r["stck_bsop_date"], float(r["stck_clpr"]))
            for r in data.get("output2", []) if r.get("stck_clpr")]
    rows.sort()
    return rows


def close_on_or_after(rows, ymd):
    for d, c in rows:
        if d >= ymd:
            return d, c
    return None


def close_on_or_before(rows, ymd):
    prev = None
    for d, c in rows:
        if d <= ymd:
            prev = (d, c)
        else:
            break
    return prev


def ret_between(rows, d1, d2):
    a = close_on_or_after(rows, d1)
    b = close_on_or_before(rows, d2)
    if not a or not b or a[1] <= 0:
        return None
    return b[1] / a[1] - 1


def ymd_shift(ymd, days):
    return (datetime.strptime(ymd, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def excess(stock_rows, bench_rows, d1, d2):
    s = ret_between(stock_rows, d1, d2)
    b = ret_between(bench_rows, d1, d2)
    if s is None or b is None:
        return None
    return (s - b) * 100  # %p


def main():
    cfg = json.loads(EVENTS.read_text())
    api = KISApi()
    results = []
    for ev in cfg["events"]:
        L = ev["date"]
        bgn = ymd_shift(L, -70)
        end = ymd_shift(L, 30)
        try:
            srows = candles_window(api, ev["code"], bgn, end)
            brows = candles_window(api, ev["bench"], bgn, end)
        except Exception as e:
            log(f"! {ev['company']} {ev['game']} 시세 실패: {e}")
            continue
        if len(srows) < 20 or len(brows) < 20:
            log(f"! {ev['company']} {ev['game']} 데이터 부족 (미상장/미래?)")
            continue
        pre_day = close_on_or_before(srows, ymd_shift(L, -1))
        d0 = close_on_or_after(srows, L)
        rec = {
            "company": ev["company"], "code": ev["code"], "game": ev["game"],
            "date": L, "bench": ev["bench"],
            "runup_30": excess(srows, brows, ymd_shift(L, -30), ymd_shift(L, -1)),
            "runup_7": excess(srows, brows, ymd_shift(L, -7), ymd_shift(L, -1)),
            "post_10": excess(srows, brows, L, ymd_shift(L, 15)),
        }
        results.append(rec)
        log(f"  {ev['company']:8} {ev['game'][:16]:16} D-30 {fmt(rec['runup_30'])} | D-7 {fmt(rec['runup_7'])} | post {fmt(rec['post_10'])}")

    def summ(key):
        vals = [r[key] for r in results if r[key] is not None]
        if not vals:
            return None
        wins = sum(1 for v in vals if v > 0)
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 2),
            "median": round(statistics.median(vals), 2),
            "beat_market_pct": round(wins / len(vals) * 100, 1),
        }

    summary = {k: summ(k) for k in ("runup_30", "runup_7", "post_10")}
    edge = bool(summary["runup_30"] and summary["runup_30"]["beat_market_pct"] > 52
                and summary["runup_30"]["mean"] > 0)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_events": len(results),
        "summary": summary,
        "edge_verdict": edge,
        "edge_threshold": "beat-market% > 52 & 평균 초과수익 > 0 (시장중립)",
        "events": sorted(results, key=lambda r: r["date"], reverse=True),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✓ 저장 {OUT.name} | 이벤트 {len(results)} | 엣지판정 {edge}")
    log(f"  D-30 {summary['runup_30']} / D-7 {summary['runup_7']} / post {summary['post_10']}")


def fmt(v):
    return f"{v:+.1f}%p" if v is not None else "  n/a"


if __name__ == "__main__":
    main()
