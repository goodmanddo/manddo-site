#!/usr/bin/env python3
"""
계절성(시즌 강세) 백테스트 — 종목별 '특정 달에 시장을 반복적으로 이기는가'.

원칙:
  - 시장중립: 매월 (종목 수익률 − 코스피 같은 달 수익률) = 초과수익.
  - 평균 아닌 '적중 연수': 그 달에 시장을 이긴 햇수 / 데이터 햇수.
  - 데이터마이닝 가드: 표본 ≥10년, (적중≥8 & 적중률≥80% & 중앙값 초과>0 & 평균 초과>1%).
  - 우연 기대치 보고: 귀무가설(P=실측 기저승률)에서 같은 기준을 우연히 통과하는
    조합 수를 이항분포로 계산 → 실제 통과 수와 비교(신호 대 노이즈).

출력: scripts/backtest_seasonality.result.json + 콘솔 요약.
매수 권유 아님. 과거 통계 관찰. 게시 여부 판단용 내부 검증.
"""

import json
import sys
from math import comb
from datetime import datetime
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "scripts" / "backtest_seasonality.result.json"

MIN_MARCAP = 1_000 * 1e8
START = "2010-01-01"
MIN_YEARS = 10            # 그 달 관측 최소 햇수
MIN_HITS = 8              # 적중 최소 연수
MIN_HITRATE = 0.80        # 적중률 하한
MIN_AVG_EXC = 0.01        # 평균 초과수익 하한(1%)


def binom_tail_ge(n: int, k: int, p: float) -> float:
    """P(X >= k), X~Binom(n,p)"""
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def main():
    k = fdr.StockListing("KOSPI"); k["Market"] = "KOSPI"
    q = fdr.StockListing("KOSDAQ"); q["Market"] = "KOSDAQ"
    L = pd.concat([k, q], ignore_index=True)
    L = L[L["Marcap"].fillna(0) >= MIN_MARCAP].sort_values("Marcap", ascending=False).reset_index(drop=True)
    sec_col = next((c for c in ("Sector", "Industry", "Dept") if c in L.columns), None)
    total = len(L)
    print(f"[계절성] {total}종목 · {START}~ · 시장중립 · 데이터마이닝 가드", flush=True)

    # 코스피 월간 수익률
    idx = fdr.DataReader("KS11", START)["Close"].dropna()
    idx_m = idx.resample("ME").last()
    idx_ret = idx_m.pct_change()
    idx_ret.index = idx_ret.index.to_period("M")

    candidates = []          # 통과한 (code,name,sector,month,years,hits,hitrate,avg,med)
    tested = 0               # n>=MIN_YEARS 인 (종목,달) 조합 수
    base_wins = base_total = 0
    done = 0
    for r in L.itertuples():
        code, name = r.Code, r.Name
        sector = getattr(r, sec_col) if sec_col else ""
        try:
            s = fdr.DataReader(code, START)["Close"].dropna()
        except Exception:
            continue
        done += 1
        if done % 100 == 0:
            print(f"  ...{done}/{total} (통과 {len(candidates)})", flush=True)
        if len(s) < 240:
            continue
        sm = s.resample("ME").last()
        sret = sm.pct_change()
        sret.index = sret.index.to_period("M")
        df = pd.concat({"s": sret, "m": idx_ret}, axis=1).dropna()
        if len(df) < 120:
            continue
        df["exc"] = df["s"] - df["m"]
        df["mon"] = df.index.month
        base_wins += int((df["exc"] > 0).sum())
        base_total += len(df)
        for mon, g in df.groupby("mon"):
            yrs = len(g)
            if yrs < MIN_YEARS:
                continue
            tested += 1
            hits = int((g["exc"] > 0).sum())
            hitrate = hits / yrs
            avg = float(g["exc"].mean())
            med = float(g["exc"].median())
            if hits >= MIN_HITS and hitrate >= MIN_HITRATE and med > 0 and avg > MIN_AVG_EXC:
                candidates.append({
                    "code": code, "name": name, "sector": str(sector),
                    "month": int(mon), "years": yrs, "hits": hits,
                    "hit_rate_pct": round(hitrate * 100, 1),
                    "avg_excess_pct": round(avg * 100, 2),
                    "med_excess_pct": round(med * 100, 2),
                })

    p0 = base_wins / base_total if base_total else 0.5  # 실측 기저승률
    # 우연 기대치: 통과 조건을 'n=MIN_YEARS, k=MIN_HITS, p=p0' 근사로 일괄 적용
    p_pass = binom_tail_ge(MIN_YEARS, MIN_HITS, p0)
    expected_by_chance = tested * p_pass

    candidates.sort(key=lambda c: (-c["hit_rate_pct"], -c["avg_excess_pct"]))
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period_start": START,
        "universe": f"KOSPI+KOSDAQ 시총 ≥ 1,000억 ({total}종목, 현재 기준·생존편향 존재)",
        "rule": f"그 달 관측 ≥{MIN_YEARS}년 & 시장초과 적중 ≥{MIN_HITS}년 & 적중률 ≥{int(MIN_HITRATE*100)}% & 중앙값>0 & 평균초과>{int(MIN_AVG_EXC*100)}%",
        "base_win_rate_pct": round(p0 * 100, 2),
        "tested_combos": tested,
        "passed": len(candidates),
        "expected_by_chance": round(expected_by_chance, 1),
        "signal_to_noise": round(len(candidates) / expected_by_chance, 2) if expected_by_chance else None,
        "candidates": candidates,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print("\n===== 결과 =====", flush=True)
    print(f"기저 월간 시장승률 p0 = {p0*100:.1f}%", flush=True)
    print(f"검정 조합(n≥{MIN_YEARS}) = {tested}, 통과 = {len(candidates)}, "
          f"우연 기대 = {expected_by_chance:.1f}, 신호/노이즈 = {out['signal_to_noise']}", flush=True)
    MN = ["", "1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]
    for c in candidates[:40]:
        print(f"  {MN[c['month']]} | {c['name']}({c['code']}) {c['sector'][:14]} "
              f"| {c['hits']}/{c['years']}년 {c['hit_rate_pct']}% | 평균초과 {c['avg_excess_pct']:+}% 중앙 {c['med_excess_pct']:+}%", flush=True)
    print(f"\n✓ 상세 저장 → {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
