#!/usr/bin/env python3
"""
스마트머니 백테스트 — 바닥 근처 + 외국인·기관 5거래일 연속 순매수.

원칙: 룩어헤드 차단 · 시장중립(같은날 유니버스 평균 대비) · 종목·패턴 20일 쿨다운.
데이터: pykrx (KRX 로그인 — ~/.config/manddo/krx.json), 일별 OHLCV + 투자자별 순매수대금.

세 가지를 분리 검정해 '어느 조건이 엣지를 만드는가'를 본다:
  smart_bottom : 바닥 근처 AND 외국인>0·기관>0 5일 연속
  smart_only   : 외국인>0·기관>0 5일 연속 (바닥 조건 없음)
  bottom_only  : 바닥 근처만
판정: +20일 excess_mean>0 & beat_market%>52 & n≥100.
매수 권유 아님. 게시 여부 판단용 내부 검증.
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "scripts" / "backtest_smartmoney.result.json"

# KRX 자격증명 (값은 출력하지 않음)
_cred = json.load(open(os.path.expanduser("~/.config/manddo/krx.json")))
os.environ["KRX_ID"] = str(_cred["id"])
os.environ["KRX_PW"] = str(_cred["pw"])
from pykrx import stock  # noqa: E402  (env 설정 후 import)

MIN_MARCAP = 1_000 * 1e8
YEARS = 3.2
HORIZONS = [5, 20, 60]
COOLDOWN = 20
BOTTOM_NEAR = 1.15        # 종가 ≤ 6개월 저점 ×1.15 = '바닥 근처'
LOW_LOOKBACK = 126        # 6개월
STREAK = 5                # 연속 순매수 거래일


def detect(t, close, low6min, f_pos, i_pos):
    """t시점 — (smart_bottom, smart_only, bottom_only) 중 해당 타입 set 반환.
    f_pos/i_pos: 외국인/기관 그날 순매수>0 여부 bool 배열."""
    if t < max(LOW_LOOKBACK, STREAK):
        return ()
    near = close[t] <= low6min[t] * BOTTOM_NEAR
    streak = f_pos[t - STREAK + 1:t + 1].all() and i_pos[t - STREAK + 1:t + 1].all()
    out = []
    if streak and near:
        out.append("smart_bottom")
    if streak:
        out.append("smart_only")
    if near:
        out.append("bottom_only")
    return out


def main():
    k = fdr.StockListing("KOSPI"); k["Market"] = "KOSPI"
    q = fdr.StockListing("KOSDAQ"); q["Market"] = "KOSDAQ"
    L = pd.concat([k, q], ignore_index=True)
    L = L[L["Marcap"].fillna(0) >= MIN_MARCAP].sort_values("Marcap", ascending=False).reset_index(drop=True)
    total = len(L)
    end = datetime.now()
    start = end - timedelta(days=int(YEARS * 365))
    sd, ed = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    print(f"[스마트머니] {total}종목 · {sd}~{ed} · 룩어헤드 차단 · 시장중립", flush=True)

    # 코스피 지수 일별(시장중립 벤치마크는 유니버스 평균이지만, 날짜축 확보용으로도 사용)
    signals = []
    uni_sum = defaultdict(lambda: [0.0, 0, 0.0, 0, 0.0, 0])  # date -> [s5,n5,s20,n20,s60,n60]
    HINDEX = {h: i for i, h in enumerate(HORIZONS)}

    done = 0
    for r in L.itertuples():
        code, name = r.Code, r.Name
        try:
            ohlcv = stock.get_market_ohlcv_by_date(sd, ed, code)
            inv = stock.get_market_trading_value_by_date(sd, ed, code)
        except Exception:
            continue
        time.sleep(0.2)
        done += 1
        if done % 50 == 0:
            print(f"  ...{done}/{total} (신호 {len(signals)})", flush=True)
        if ohlcv is None or inv is None or ohlcv.empty or inv.empty:
            continue
        df = ohlcv.join(inv[["외국인합계", "기관합계"]], how="inner").dropna()
        if len(df) < LOW_LOOKBACK + max(HORIZONS) + STREAK:
            continue
        close = df["종가"].to_numpy(float)
        lowv = df["저가"].to_numpy(float)
        f_pos = (df["외국인합계"].to_numpy(float) > 0)
        i_pos = (df["기관합계"].to_numpy(float) > 0)
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        n = len(df)
        low6min = pd.Series(lowv).rolling(LOW_LOOKBACK).min().to_numpy()

        for t in range(n):
            for h in HORIZONS:
                if t + h < n:
                    ret = close[t + h] / close[t] - 1.0
                    b = uni_sum[dates[t]]; j = HINDEX[h] * 2
                    b[j] += ret; b[j + 1] += 1

        last_fire = {}
        for t in range(max(LOW_LOOKBACK, STREAK), n - max(HORIZONS)):
            for typ in detect(t, close, low6min, f_pos, i_pos):
                if t - last_fire.get(typ, -10**9) < COOLDOWN:
                    continue
                last_fire[typ] = t
                rets = {h: close[t + h] / close[t] - 1.0 for h in HORIZONS}
                signals.append((code, name, dates[t], typ, rets))

    uni_mean = {}
    for d, b in uni_sum.items():
        uni_mean[d] = {h: (b[HINDEX[h] * 2] / b[HINDEX[h] * 2 + 1] if b[HINDEX[h] * 2 + 1] else None)
                       for h in HORIZONS}

    agg = {}
    for typ in ("smart_bottom", "smart_only", "bottom_only"):
        rows = [s for s in signals if s[3] == typ]
        rec = {"signals": len(rows), "by_horizon": {}}
        for h in HORIZONS:
            raw = np.array([s[4][h] for s in rows], float)
            exc = np.array([s[4][h] - uni_mean[s[2]][h] for s in rows
                            if uni_mean.get(s[2], {}).get(h) is not None], float)
            if raw.size == 0:
                continue
            rec["by_horizon"][f"d{h}"] = {
                "n": int(raw.size),
                "raw_mean_pct": round(float(raw.mean()) * 100, 2),
                "raw_win_pct": round(float((raw > 0).mean()) * 100, 1),
                "excess_mean_pct": round(float(exc.mean()) * 100, 2) if exc.size else None,
                "beat_market_pct": round(float((exc > 0).mean()) * 100, 1) if exc.size else None,
            }
        agg[typ] = rec

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": f"{sd}~{ed}",
        "universe": f"KOSPI+KOSDAQ 시총 ≥ 1,000억 ({total}종목, 현재 기준·생존편향)",
        "method": "룩어헤드 차단 · 시장중립 · 외국인·기관 5일연속 순매수 · 바닥=6개월저점+15%이내",
        "patterns": agg,
        "publish_rule": "+20일 excess_mean>0 AND beat_market%>52 AND n≥100",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print("\n===== 결과 =====", flush=True)
    for typ, rec in agg.items():
        print(f"\n[{typ}] 신호 {rec['signals']}건")
        for hk, m in rec["by_horizon"].items():
            print(f"  {hk}: 평균 {m['raw_mean_pct']:+.2f}% / 적중 {m['raw_win_pct']}% "
                  f"/ 초과 {m['excess_mean_pct']:+}% / 시장이김 {m['beat_market_pct']}% (n={m['n']})", flush=True)
        d20 = rec["by_horizon"].get("d20", {})
        if d20:
            ok = (d20.get("excess_mean_pct") or -1) > 0 and (d20.get("beat_market_pct") or 0) > 52 and d20["n"] >= 100
            print(f"  → 게시판정(+20일): {'합격 ✅' if ok else '불합격 ❌'}", flush=True)
    print(f"\n✓ 상세 저장 → {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
