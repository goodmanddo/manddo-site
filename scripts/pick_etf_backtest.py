#!/usr/bin/env python3
"""
'AI 픽을 종목 대신 섹터 ETF로 샀다면' 검증 → tools/data/pick_etf_backtest.json

weekly_pick_state.json(휴먼vsAI AI픽 자동매매 history)의 각 라운드에서, AI가 고른
개별종목 대신 그 종목의 섹터 ETF를 같은 주(월 시가~금 종가) 매매했을 때 수익률을
계산해 비교. 섹터는 픽 사유(reason)의 키워드로 매핑. 한투 수정주가.
"""
import json, sys, statistics
from datetime import datetime
from pathlib import Path
sys.path.insert(0, "/Users/mandoo/stock_auto_trade")
from kis_api import KISApi

SITE = Path.home() / "manddo-site"
STATE = Path.home() / "stock_auto_trade" / "weekly_pick_state.json"
OUT = SITE / "tools" / "data" / "pick_etf_backtest.json"

SECTOR_ETF = [
    (["반도체"], "091160", "반도체"),
    (["헬스", "바이오", "제약"], "266420", "헬스케어"),
    (["에너지", "정유", "유가", "화학"], "117460", "에너지화학"),
    (["자동차", "소비재", "경기소비"], "091180", "자동차"),
    (["금융", "은행", "증권"], "091170", "은행"),
    (["2차전지", "배터리"], "305720", "2차전지"),
    (["운송", "조선", "항공"], "140710", "운송"),
]


def sector_etf(reason):
    r = reason or ""
    if "유틸" in r or "전력" in r:   # 유틸리티는 마땅한 한국 섹터 ETF 없음 → 제외
        return None, None
    for kws, code, name in SECTOR_ETF:
        if any(k in r for k in kws):
            return code, name
    return None, None


def main():
    hist = json.loads(STATE.read_text()).get("history", [])
    api = KISApi()
    cache = {}

    def rows(code):
        if code not in cache:
            d = api._get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20260601",
                 "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d"), "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"})
            cache[code] = {x["stck_bsop_date"]: (float(x["stck_oprc"]), float(x["stck_clpr"]))
                           for x in d.get("output2", []) if x.get("stck_oprc")}
        return cache[code]

    def etf_ret(code, bd, sd):
        m = rows(code); by = bd.replace("-", ""); sy = sd.replace("-", "")
        o = next((m[k][0] for k in sorted(m) if k >= by), None)
        c = next((m[k][1] for k in sorted(m, reverse=True) if k <= sy), None)
        return round((c / o - 1) * 100, 2) if o and c and o > 0 else None

    events = []
    for x in hist:
        etf, ename = sector_etf(x.get("reason"))
        er = etf_ret(etf, x["buy_date"], x["sell_date"]) if etf else None
        events.append({
            "buy_date": x["buy_date"], "sell_date": x["sell_date"],
            "name": x["name"], "code": x["code"], "stock_ret": x["return_pct"],
            "etf": etf, "etf_name": ename, "etf_ret": er,
        })

    def agg(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        w = sum(1 for v in vals if v > 0)
        return {"n": len(vals), "winrate": round(w / len(vals) * 100),
                "mean": round(statistics.mean(vals), 2), "cum": round(sum(vals), 1)}

    paired = [(e["stock_ret"], e["etf_ret"]) for e in events if e["etf_ret"] is not None]
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_rounds": len(events),
        "n_paired": len(paired),
        "all_stock": agg([e["stock_ret"] for e in events]),
        "paired_stock": agg([a for a, b in paired]),
        "paired_etf": agg([b for a, b in paired]),
        "events": events,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ 저장", OUT.name, "| 라운드", len(events), "| ETF매핑", len(paired),
          "| 종목누적", out["paired_stock"]["cum"], "ETF누적", out["paired_etf"]["cum"])


if __name__ == "__main__":
    main()
