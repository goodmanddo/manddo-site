#!/usr/bin/env python3
"""
출구 이동평균선 비교 — 같은 '주도 섹터 대장주' 진입, 출구만 20/50/60/120일선 이탈.

진입은 backtest_leader_momentum.py와 동일(12-1모멘텀·실업종·3종목+·상위20%섹터·
섹터1위·TOPN 동일비중·월말). 출구만: 보유 종가 < N일선이면 청산, 다음 월말 빈슬롯 충원.
목적: '짧을수록 반납↓·휩쏘↑, 길수록 대박↑·낙폭↑' 트레이드오프를 코스피 데이터로 확인.
원칙: 룩어헤드 차단 · 거래비용 0.35%/매매 · 벤치 코스피. 출구 실험 최종 1회(이후 튜닝 금지).
"""

import json
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path.home() / "manddo-site"
OUT = ROOT / "scripts" / "backtest_exit_ma.result.json"

MIN_MARCAP = 1_000 * 1e8
YEARS = 3.6
TOPN = 10
SECTOR_TOP_FRAC = 0.20
COST = 0.0035
MOM_LB, MOM_SKIP = 252, 21
MA_WINDOWS = [20, 50, 60, 120]


def load_universe():
    parts = []
    for mkt in ("KOSPI", "KOSDAQ"):
        d = fdr.StockListing(mkt); d["Market"] = mkt
        parts.append(d)
    L = pd.concat(parts, ignore_index=True)
    L = L[L["Marcap"].fillna(0) >= MIN_MARCAP].copy()
    desc = fdr.StockListing("KRX-DESC")
    L = L.merge(desc[["Code", "Industry"]], on="Code", how="left")
    L["Sector"] = L["Industry"].fillna("기타")
    return L[["Code", "Name", "Market", "Sector", "Marcap"]].drop_duplicates("Code").reset_index(drop=True)


def main():
    L = load_universe()
    start = (datetime.now() - timedelta(days=int(YEARS * 365))).strftime("%Y-%m-%d")
    print(f"[출구MA비교] {len(L)}종목 · {start}~ · 출구 {MA_WINDOWS}일선", flush=True)

    closes, sectors = {}, {}
    for i, r in enumerate(L.itertuples(), 1):
        try:
            df = fdr.DataReader(r.Code, start)
        except Exception:
            continue
        if df is None or len(df) < MOM_LB + 40:
            continue
        closes[r.Code] = df["Close"].astype(float)
        sectors[r.Code] = r.Sector
        if i % 200 == 0:
            print(f"  ...수집 {i}/{len(L)} (유효 {len(closes)})", flush=True)
    if not closes:
        print("데이터 없음"); return
    px = pd.DataFrame(closes).sort_index().ffill()
    ks = fdr.DataReader("KS11", start)["Close"].astype(float).reindex(px.index).ffill()
    ma = {w: px.rolling(w).mean() for w in MA_WINDOWS}

    month_ends = px.resample("ME").last().index
    rebals = sorted({px.index[px.index <= me][-1] for me in month_ends if (px.index <= me).any()})
    rebals = [d for d in rebals if px.index.get_loc(d) >= MOM_LB]
    rebal_set = set(rebals)
    idx = list(px.index)
    col = {c: px.columns.get_loc(c) for c in px.columns}

    def pick_leaders(asof_i):
        a, b = asof_i - MOM_LB, asof_i - MOM_SKIP
        if a < 0:
            return []
        m = (px.iloc[b] / px.iloc[a] - 1.0).dropna()
        if m.empty:
            return []
        sec = pd.Series({c: sectors.get(c, "기타") for c in m.index})
        cnt = sec.value_counts()
        elig = cnt[(cnt >= 3) & (cnt.index != "기타")].index
        score = m.groupby(sec).mean().reindex(elig).dropna().sort_values(ascending=False)
        if score.empty:
            return []
        lead = set(score.index[:max(1, int(len(score) * SECTOR_TOP_FRAC))])
        champs = []
        for s in lead:
            mem = m[sec == s]
            if not mem.empty:
                champs.append((mem.idxmax(), mem.max()))
        champs.sort(key=lambda x: -x[1])
        return [c for c, _ in champs[:TOPN]]

    leaders_at = {px.index.get_loc(d): pick_leaders(px.index.get_loc(d)) for d in rebals}

    def simulate(w):
        cash, holds, eq = 1.0, {}, []
        man = ma[w]
        for i, dt in enumerate(idx):
            for c in list(holds.keys()):
                p = px.iat[i, col[c]]
                if not np.isfinite(p):
                    continue
                mv = man.iat[i, col[c]]
                if np.isfinite(mv) and p < mv:
                    cash += holds[c]['sh'] * p * (1 - COST)
                    del holds[c]
            if dt in rebal_set:
                leaders = leaders_at.get(i, [])
                if leaders:
                    slots = TOPN - len(holds)
                    tg = [c for c in leaders if c not in holds][:max(0, slots)]
                    if tg:
                        per = cash / len(tg)
                        for c in tg:
                            p = px.iat[i, col[c]]
                            if np.isfinite(p) and p > 0 and cash > 0:
                                spend = min(cash, per)
                                holds[c] = {'sh': spend * (1 - COST) / p}
                                cash -= spend
            tot = cash + sum(holds[c]['sh'] * px.iat[i, col[c]]
                             for c in holds if np.isfinite(px.iat[i, col[c]]))
            eq.append((dt, tot))
        return pd.Series({d: v for d, v in eq}).sort_index()

    bench = ks / ks.iloc[0]
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    bench_cagr = bench.iloc[-1] ** (1 / yrs) - 1
    results = {}
    for w in MA_WINDOWS:
        eq = simulate(w)
        em = eq.resample("ME").last().dropna()
        bm = bench.resample("ME").last().reindex(em.index).ffill()
        sr, br = em.pct_change().dropna(), bm.pct_change().dropna()
        cm = sr.index.intersection(br.index)
        sr, br = sr.loc[cm], br.loc[cm]
        cagr = eq.iloc[-1] ** (1 / yrs) - 1
        mdd = ((eq / eq.cummax()) - 1).min()
        beat = float((sr.values > br.values).mean()) * 100
        exc = float((sr.values - br.values).mean()) * 100
        results[f"{w}일선"] = {
            "months": int(len(sr)),
            "total_return_pct": round((eq.iloc[-1] - 1) * 100, 1),
            "cagr_pct": round(cagr * 100, 2),
            "mdd_pct": round(mdd * 100, 1),
            "beat_market_monthly_pct": round(beat, 1),
            "excess_monthly_mean_pct": round(exc, 2),
            "pass": bool(beat > 52 and exc > 0),
        }

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period_start": start,
        "universe": f"KOSPI+KOSDAQ 시총 ≥ 1,000억 ({len(L)}종목, 생존편향)",
        "entry": "주도 섹터(실업종 상위20%) 대장주 TOPN 동일비중·월말, 12-1모멘텀",
        "benchmark_cagr_pct": round(bench_cagr * 100, 2),
        "results": results,
        "publish_rule": "월간 beat-market%>52 AND 월간 순초과>0",
        "note": "출구 실험 최종. 1등 선택은 과최적화 — 트레이드오프 관찰용.",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"\n===== 출구 이동평균 비교 (벤치 코스피 CAGR {round(bench_cagr*100,1)}%) =====", flush=True)
    for k, r in results.items():
        print(f"\n[{k} 이탈 시 매도]  {r['months']}개월", flush=True)
        print(f"  총수익 {r['total_return_pct']:+}% / CAGR {r['cagr_pct']:+}% / MDD {r['mdd_pct']}%", flush=True)
        print(f"  월간 시장이김 {r['beat_market_monthly_pct']}% / 월 순초과 {r['excess_monthly_mean_pct']:+}%"
              f"  → {'합격 ✅' if r['pass'] else '불합격 ❌'}", flush=True)
    print(f"\n✓ 상세 저장 → {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
