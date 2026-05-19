#!/usr/bin/env python3
"""
진단 — narrow-breadth 가설 마무리.

Q1. 우리 모멘텀-주도주 전략이 삼성전자·SK하이닉스를 매월 리밸런싱에서
    실제로 '주도 대장주'로 잡았는가? (보유 월 비율)
Q2. 같은 진입(주도섹터 대장주, 12-1모멘텀)을 균등비중 vs 시총가중으로
    운용하면 시총가중 코스피를 따라잡/이기는가?
참고. 코스피 / 삼성+하이닉스 50:50 매수후보유 로 양 끝을 표시.

출구는 50일선 이탈(앞 검증의 대표값) 고정 — 비중 효과만 분리.
시총가중 프록시: shares≈현재Marcap/현재가(주식수 불변 가정), cap_t = shares×price_t.
룩어헤드 차단 · 거래비용 0.35%/매매 · 벤치 코스피. 전략 낚시 아님(가설 진단).
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
OUT = ROOT / "scripts" / "diag_breadth_capweight.result.json"

MIN_MARCAP = 1_000 * 1e8
YEARS = 3.6
TOPN = 10
SECTOR_TOP_FRAC = 0.20
COST = 0.0035
MOM_LB, MOM_SKIP = 252, 21
MA_EXIT = 50
SS, HY = "005930", "000660"


def main():
    parts = []
    for mkt in ("KOSPI", "KOSDAQ"):
        d = fdr.StockListing(mkt); d["Market"] = mkt
        parts.append(d)
    L = pd.concat(parts, ignore_index=True)
    L = L[L["Marcap"].fillna(0) >= MIN_MARCAP].copy()
    desc = fdr.StockListing("KRX-DESC")
    L = L.merge(desc[["Code", "Industry"]], on="Code", how="left")
    L["Sector"] = L["Industry"].fillna("기타")
    marcap_now = dict(zip(L["Code"], L["Marcap"].astype(float)))
    L = L[["Code", "Name", "Sector"]].drop_duplicates("Code").reset_index(drop=True)

    start = (datetime.now() - timedelta(days=int(YEARS * 365))).strftime("%Y-%m-%d")
    print(f"[진단] {len(L)}종목 · {start}~ · 균등 vs 시총가중 · SS/HY 보유추적", flush=True)

    closes, sectors, shares = {}, {}, {}
    for i, r in enumerate(L.itertuples(), 1):
        try:
            df = fdr.DataReader(r.Code, start)
        except Exception:
            continue
        if df is None or len(df) < MOM_LB + 40:
            continue
        c = df["Close"].astype(float)
        closes[r.Code] = c
        sectors[r.Code] = r.Sector
        pnow = c.iloc[-1]
        shares[r.Code] = (marcap_now.get(r.Code, np.nan) / pnow) if pnow > 0 else np.nan
        if i % 200 == 0:
            print(f"  ...수집 {i}/{len(L)} (유효 {len(closes)})", flush=True)
    px = pd.DataFrame(closes).sort_index().ffill()
    ks = fdr.DataReader("KS11", start)["Close"].astype(float).reindex(px.index).ffill()
    man = px.rolling(MA_EXIT).mean()
    col = {c: px.columns.get_loc(c) for c in px.columns}

    month_ends = px.resample("ME").last().index
    rebals = sorted({px.index[px.index <= me][-1] for me in month_ends if (px.index <= me).any()})
    rebals = [d for d in rebals if px.index.get_loc(d) >= MOM_LB]
    rebal_set = set(rebals)
    idx = list(px.index)

    def pick(asof_i):
        a, b = asof_i - MOM_LB, asof_i - MOM_SKIP
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

    picks = {px.index.get_loc(d): pick(px.index.get_loc(d)) for d in rebals}
    n_reb = len(picks)
    ss_held = sum(1 for v in picks.values() if SS in v)
    hy_held = sum(1 for v in picks.values() if HY in v)

    def simulate(capweight):
        cash, holds, eq = 1.0, {}, []
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
                leaders = [c for c in picks.get(i, []) if np.isfinite(px.iat[i, col[c]])]
                # 전량 재구성(비중효과 분리 위해 월 완전 리밸런싱)
                for c in list(holds.keys()):
                    p = px.iat[i, col[c]]
                    if np.isfinite(p):
                        cash += holds[c]['sh'] * p * (1 - COST)
                holds.clear()
                if leaders:
                    if capweight:
                        caps = np.array([shares.get(c, np.nan) * px.iat[i, col[c]] for c in leaders], float)
                        caps = np.where(np.isfinite(caps) & (caps > 0), caps, 0.0)
                        wsum = caps.sum()
                        wts = caps / wsum if wsum > 0 else np.ones(len(leaders)) / len(leaders)
                    else:
                        wts = np.ones(len(leaders)) / len(leaders)
                    total_cash = cash
                    for c, w in zip(leaders, wts):
                        p = px.iat[i, col[c]]
                        if np.isfinite(p) and p > 0 and w > 0:
                            spend = total_cash * w
                            holds[c] = {'sh': spend * (1 - COST) / p}
                            cash -= spend
            tot = cash + sum(holds[c]['sh'] * px.iat[i, col[c]]
                             for c in holds if np.isfinite(px.iat[i, col[c]]))
            eq.append((dt, tot))
        return pd.Series({d: v for d, v in eq}).sort_index()

    bench = ks / ks.iloc[0]
    # SS/HY 50:50 매수후보유
    ss_p = px[SS] if SS in px else None
    hy_p = px[HY] if HY in px else None
    yrs = (px.index[-1] - px.index[0]).days / 365.25

    def metrics(eq):
        em = eq.resample("ME").last().dropna()
        bm = bench.resample("ME").last().reindex(em.index).ffill()
        sr, br = em.pct_change().dropna(), bm.pct_change().dropna()
        cm = sr.index.intersection(br.index)
        sr, br = sr.loc[cm], br.loc[cm]
        return {
            "total_return_pct": round((eq.iloc[-1] - 1) * 100, 1),
            "cagr_pct": round((eq.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
            "mdd_pct": round(((eq / eq.cummax()) - 1).min() * 100, 1),
            "beat_market_monthly_pct": round(float((sr.values > br.values).mean()) * 100, 1),
        }

    res = {
        "rebalance_months": n_reb,
        "samsung_held_pct": round(ss_held / n_reb * 100, 1),
        "hynix_held_pct": round(hy_held / n_reb * 100, 1),
        "equal_weight": metrics(simulate(False)),
        "cap_weight": metrics(simulate(True)),
        "kospi_buyhold": {"total_return_pct": round((bench.iloc[-1] - 1) * 100, 1),
                          "cagr_pct": round((bench.iloc[-1] ** (1 / yrs) - 1) * 100, 2)},
    }
    if ss_p is not None and hy_p is not None:
        sh = 0.5 * ss_p / ss_p.iloc[0] + 0.5 * hy_p / hy_p.iloc[0]
        res["samsung_hynix_5050"] = metrics(sh)
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "period_start": start, "universe_n": len(L),
           "note": "비중효과 분리용 진단. 시총가중은 주식수불변 프록시. 단일국면 한계.",
           "result": res}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print("\n===== 진단 결과 =====", flush=True)
    print(f"리밸런싱 {n_reb}개월 중 — 삼성전자 보유 {res['samsung_held_pct']}% · "
          f"SK하이닉스 보유 {res['hynix_held_pct']}%", flush=True)
    print(f"코스피 매수후보유: 총 {res['kospi_buyhold']['total_return_pct']:+}% / "
          f"CAGR {res['kospi_buyhold']['cagr_pct']:+}%", flush=True)
    if "samsung_hynix_5050" in res:
        m = res["samsung_hynix_5050"]
        print(f"삼성+하이닉스 50:50: 총 {m['total_return_pct']:+}% / CAGR {m['cagr_pct']:+}% / "
              f"MDD {m['mdd_pct']}% / 시장이김 {m['beat_market_monthly_pct']}%", flush=True)
    for k in ("equal_weight", "cap_weight"):
        m = res[k]
        print(f"{'균등비중' if k=='equal_weight' else '시총가중'} 모멘텀리더: "
              f"총 {m['total_return_pct']:+}% / CAGR {m['cagr_pct']:+}% / MDD {m['mdd_pct']}% / "
              f"시장이김 {m['beat_market_monthly_pct']}%", flush=True)
    print(f"\n✓ 상세 저장 → {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
