#!/usr/bin/env python3
"""
'국민연금 따라사기' 검증 백테스트 → tools/data/nps_follow_backtest.json

nps_holdings.json의 지분 변동 공시를 이벤트로, 공시일 종가 매수 후 +5/10/20일
수익률을 시장 매칭 벤치(코스피→코스피200, 코스닥→코스닥150) 대비로 계산.
지분 늘림(+1%p↑) vs 줄임(-1%p↓) 두 그룹을 비교해 '방향 신호'가 있는지(long-short
스프레드) 검증. 숫자는 한투 수정주가로 결정론 계산.
"""
import json, sys, statistics
from datetime import datetime
from pathlib import Path
sys.path.insert(0, "/Users/mandoo/stock_auto_trade")
from kis_api import KISApi

SITE = Path.home() / "manddo-site"
SRC = SITE / "tools" / "data" / "nps_holdings.json"
OUT = SITE / "tools" / "data" / "nps_follow_backtest.json"
HOLDS = [5, 10, 20]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    items = [x for x in json.loads(SRC.read_text())["items"] if x.get("stkrt_irds") not in (None, "")]
    api = KISApi()
    cache = {}

    def series(code):
        if code not in cache:
            try:
                r = api._get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20260401",
                     "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d"),
                     "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"})
                rs = [(x["stck_bsop_date"], float(x["stck_clpr"])) for x in r.get("output2", []) if x.get("stck_clpr")]
                rs.sort()
                cache[code] = rs
            except Exception:
                cache[code] = []
        return cache[code]

    bmaps = {"069500": {dt: c for dt, c in series("069500")},
             "229200": {dt: c for dt, c in series("229200")}}

    def excess(code, ymd, n, market):
        bmap = bmaps["229200" if market == "코스닥" else "069500"]
        s = series(code)
        idx = next((i for i, (dd, c) in enumerate(s) if dd >= ymd), None)
        if idx is None or idx + n >= len(s):
            return None
        r = (s[idx + n][1] / s[idx][1] - 1) * 100
        b0 = bmap.get(s[idx][0]); bn = bmap.get(s[idx + n][0])
        if not b0 or not bn:
            return None
        return r - (bn / b0 - 1) * 100

    up = [x for x in items if (num(x["stkrt_irds"]) or 0) >= 1.0]
    dn = [x for x in items if (num(x["stkrt_irds"]) or 0) <= -1.0]

    def grp(g, n):
        rs = [excess(x["stock_code"], x["date"], n, x.get("market", "")) for x in g]
        rs = [r for r in rs if r is not None]
        if not rs:
            return None
        w = sum(1 for r in rs if r > 0)
        return {"n": len(rs), "beat": round(w / len(rs) * 100, 0), "mean": round(statistics.mean(rs), 2)}

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_up": len(up), "n_dn": len(dn),
        "up": {str(n): grp(up, n) for n in HOLDS},
        "dn": {str(n): grp(dn, n) for n in HOLDS},
        "spread": {},
    }
    for n in HOLDS:
        u, d = out["up"][str(n)], out["dn"][str(n)]
        out["spread"][str(n)] = round(u["mean"] - d["mean"], 2) if u and d else None
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ 저장", OUT.name, "| 늘림", out["n_up"], "줄임", out["n_dn"],
          "| +20 스프레드", out["spread"]["20"], "%p")


if __name__ == "__main__":
    main()
