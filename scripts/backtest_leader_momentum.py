#!/usr/bin/env python3
"""
주도 섹터 대장주 추종 백테스트 — 출구 3종 맞대결.

진입: 매월말, 12-1개월 모멘텀으로 섹터 점수 → 상위(주도) 섹터의
      각 대장주(섹터 내 모멘텀 1위) 중 모멘텀 상위 N개 동일비중.
출구 비교:
  A 월재랭킹  : 매월 주도-대장 재구성, 탈락 시 교체 (학술 모멘텀 방식)
  B 50일선이탈: 보유 중 종가<50일선이면 즉시 청산, 다음 월말 재충원
  C 20%트레일: 보유 후 고점 대비 -20%면 청산, 다음 월말 재충원

원칙: 룩어헤드 차단(결정은 t까지 데이터만) · 거래비용 0.35%/매매 ·
      벤치마크 코스피(KS11) · 지표: CAGR/총수익/월간 시장이김%/MDD/시장중립 초과.
판정: 월간 beat-market%>52 AND 순초과>0. 매수 권유 아님. 내부 검증.
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
OUT = ROOT / "scripts" / "backtest_leader_momentum.result.json"

MIN_MARCAP = 1_000 * 1e8
YEARS = 3.6
TOPN = 10                 # 보유 종목 수(분산)
SECTOR_TOP_FRAC = 0.20    # 상위 20% 섹터 = 주도 섹터
COST = 0.0035             # 매매 1회당 비용(체결+세금+슬리피지 가정)
MOM_LB, MOM_SKIP = 252, 21  # 12-1개월 모멘텀


def load_universe():
    parts = []
    for mkt in ("KOSPI", "KOSDAQ"):
        d = fdr.StockListing(mkt); d["Market"] = mkt
        parts.append(d)
    L = pd.concat(parts, ignore_index=True)
    L = L[L["Marcap"].fillna(0) >= MIN_MARCAP].copy()
    # 진짜 업종: KRX-DESC의 Sector 컬럼을 Code 기준 병합
    desc = fdr.StockListing("KRX-DESC")          # Industry = 진짜 KSIC 업종
    L = L.merge(desc[["Code", "Industry"]], on="Code", how="left")
    L["Sector"] = L["Industry"].fillna("기타")
    return L[["Code", "Name", "Market", "Sector", "Marcap"]].drop_duplicates("Code").reset_index(drop=True)


def main():
    L = load_universe()
    start = (datetime.now() - timedelta(days=int(YEARS * 365))).strftime("%Y-%m-%d")
    print(f"[주도대장주] {len(L)}종목 · {start}~ · 출구 A/B/C 비교", flush=True)

    # 가격 패널 (종가) 일괄 수집
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
        print("데이터 없음", flush=True); return
    px = pd.DataFrame(closes).sort_index()
    ks = fdr.DataReader("KS11", start)["Close"].astype(float).reindex(px.index).ffill()
    px = px.ffill()
    ma50 = px.rolling(50).mean()

    # 월말 리밸런싱 날짜
    month_ends = px.resample("ME").last().index
    rebals = [px.index[px.index <= me][-1] for me in month_ends if (px.index <= me).any()]
    rebals = sorted(set(d for d in rebals if px.index.get_loc(d) >= MOM_LB))

    def momentum(asof_i):
        a, b = asof_i - MOM_LB, asof_i - MOM_SKIP
        if a < 0:
            return pd.Series(dtype=float)
        return px.iloc[b] / px.iloc[a] - 1.0

    def pick_leaders(asof_i):
        m = momentum(asof_i).dropna()
        if m.empty:
            return []
        sec = pd.Series({c: sectors.get(c, "기타") for c in m.index})
        cnt = sec.value_counts()
        elig = cnt[(cnt >= 3) & (cnt.index != "기타")].index      # 3종목+ 업종만 주도 후보
        sec_score = m.groupby(sec).mean().loc[elig].sort_values(ascending=False)
        if sec_score.empty:
            return []
        n_top = max(1, int(len(sec_score) * SECTOR_TOP_FRAC))
        lead_secs = set(sec_score.index[:n_top])
        # 주도 섹터별 대장주(섹터 내 모멘텀 1위) → 그 중 모멘텀 상위 TOPN
        champs = []
        for s in lead_secs:
            members = m[sec == s]
            if not members.empty:
                champs.append((members.idxmax(), members.max()))
        champs.sort(key=lambda x: -x[1])
        return [c for c, _ in champs[:TOPN]]

    idx = list(px.index)
    rebal_set = set(rebals)

    def simulate(exit_rule):
        cash = 1.0
        holds = {}                       # code -> {'shares','peak'}
        equity = []
        port_val_prev = 1.0
        for i, dt in enumerate(idx):
            # 일별: 보유 종목 청산 체크(B/C) — 결정은 당일 종가까지 정보만
            for c in list(holds.keys()):
                p = px.iat[i, px.columns.get_loc(c)]
                if not np.isfinite(p):
                    continue
                holds[c]['peak'] = max(holds[c]['peak'], p)
                sell = False
                if exit_rule == "B":
                    m = ma50.iat[i, ma50.columns.get_loc(c)]
                    if np.isfinite(m) and p < m:
                        sell = True
                elif exit_rule == "C":
                    if p <= holds[c]['peak'] * 0.80:
                        sell = True
                if sell:
                    cash += holds[c]['shares'] * p * (1 - COST)
                    del holds[c]
            # 월말: 재랭킹
            if dt in rebal_set:
                leaders = pick_leaders(i)
                if exit_rule == "A":                       # 전량 재구성
                    for c in list(holds.keys()):
                        p = px.iat[i, px.columns.get_loc(c)]
                        if np.isfinite(p):
                            cash += holds[c]['shares'] * p * (1 - COST)
                    holds.clear()
                if leaders:
                    cur = set(holds.keys())
                    if exit_rule == "A":
                        targets = leaders
                    else:                                  # 빈 슬롯만 충원
                        slots = TOPN - len(holds)
                        targets = [c for c in leaders if c not in cur][:max(0, slots)]
                    if targets:
                        per = cash / len(targets) if exit_rule == "A" else (cash / max(1, len(targets)))
                        for c in targets:
                            p = px.iat[i, px.columns.get_loc(c)]
                            if np.isfinite(p) and p > 0 and cash > 0:
                                spend = min(cash, per)
                                sh = spend * (1 - COST) / p
                                holds[c] = {'shares': holds.get(c, {}).get('shares', 0) + sh, 'peak': p}
                                cash -= spend
            # 평가
            mv = cash + sum(holds[c]['shares'] * px.iat[i, px.columns.get_loc(c)]
                            for c in holds if np.isfinite(px.iat[i, px.columns.get_loc(c)]))
            equity.append((dt, mv))
        eq = pd.Series({d: v for d, v in equity}).sort_index()
        return eq

    bench = ks / ks.iloc[0]
    results = {}
    for rule in ("A", "B", "C"):
        eq = simulate(rule)
        eqm = eq.resample("ME").last().dropna()
        bm = bench.resample("ME").last().reindex(eqm.index).ffill()
        sr, br = eqm.pct_change().dropna(), bm.pct_change().dropna()
        common = sr.index.intersection(br.index)
        sr, br = sr.loc[common], br.loc[common]
        n_months = len(sr)
        yrs = (eqm.index[-1] - eqm.index[0]).days / 365.25
        cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else float("nan")
        bench_cagr = bench.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else float("nan")
        mdd = ((eq / eq.cummax()) - 1).min()
        beat = float((sr.values > br.values).mean()) * 100 if n_months else float("nan")
        exc = float((sr.values - br.values).mean()) * 100 if n_months else float("nan")
        results[rule] = {
            "exit": {"A": "월재랭킹", "B": "50일선이탈", "C": "20%트레일"}[rule],
            "months": int(n_months),
            "total_return_pct": round((eq.iloc[-1] - 1) * 100, 1),
            "cagr_pct": round(cagr * 100, 2),
            "bench_cagr_pct": round(bench_cagr * 100, 2),
            "mdd_pct": round(mdd * 100, 1),
            "beat_market_monthly_pct": round(beat, 1),
            "excess_monthly_mean_pct": round(exc, 2),
            "pass": bool(beat > 52 and exc > 0),
        }

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period_start": start,
        "universe": f"KOSPI+KOSDAQ 시총 ≥ 1,000억 ({len(L)}종목, 현재기준·생존편향)",
        "params": {"TOPN": TOPN, "sector_top": SECTOR_TOP_FRAC, "cost_per_trade": COST,
                   "momentum": "12-1개월"},
        "benchmark": "KOSPI(KS11) 매수후보유",
        "results": results,
        "publish_rule": "월간 beat-market%>52 AND 월간 순초과>0",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print("\n===== 결과 (주도 섹터 대장주 추종) =====", flush=True)
    print(f"벤치마크 코스피 CAGR ≈ {list(results.values())[0]['bench_cagr_pct']}%", flush=True)
    for rule, r in results.items():
        print(f"\n[출구 {rule} · {r['exit']}]  {r['months']}개월", flush=True)
        print(f"  총수익 {r['total_return_pct']:+}% / CAGR {r['cagr_pct']:+}% "
              f"(코스피 {r['bench_cagr_pct']:+}%) / MDD {r['mdd_pct']}%", flush=True)
        print(f"  월간 시장이김 {r['beat_market_monthly_pct']}% / 월 순초과 {r['excess_monthly_mean_pct']:+}%"
              f"  → {'합격 ✅' if r['pass'] else '불합격 ❌'}", flush=True)
    print(f"\n✓ 상세 저장 → {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
