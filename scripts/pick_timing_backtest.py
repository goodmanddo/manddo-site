#!/usr/bin/env python3
"""
'AI 오늘의 픽' 매매 타이밍 백테스트 → tools/data/pick_timing_backtest.json

git 히스토리의 ai-log/today_pick.json 90일치를 복원해, 각 픽을 09시 시가에
매수했을 때의 결과를 검증:
  - 09시 갭(전일종가→당일시가): 픽 종목이 이미 얼마나 올라 시작하는가
  - 당일 09→15시(인트라데이) 수익률
  - 09시 매수 후 +1/3/5/10/20일 보유 수익률 (절대 + 코스피200 대비 초과)
숫자는 한투 수정주가로 결정론 계산(환각 없음).
"""
import subprocess, json, sys, statistics
from datetime import datetime
from pathlib import Path
sys.path.insert(0, "/Users/mandoo/stock_auto_trade")
from kis_api import KISApi

SITE = Path.home() / "manddo-site"
OUT = SITE / "tools" / "data" / "pick_timing_backtest.json"
HOLDS = [1, 3, 5, 10, 20]


def load_picks():
    hashes = subprocess.run(["git", "-C", str(SITE), "log", "--format=%H", "--", "ai-log/today_pick.json"],
                            capture_output=True, text=True).stdout.split()
    picks = {}
    for h in hashes:
        try:
            d = json.loads(subprocess.run(["git", "-C", str(SITE), "show", f"{h}:ai-log/today_pick.json"],
                                          capture_output=True, text=True).stdout)
            if d.get("date") and d.get("code") and d["date"] not in picks:
                picks[d["date"]] = d["code"]
        except Exception:
            continue
    return dict(sorted(picks.items()))


def summ(r):
    if not r:
        return None
    w = sum(1 for x in r if x > 0)
    return {"n": len(r), "winrate": round(w / len(r) * 100, 1),
            "mean": round(statistics.mean(r), 2), "cum": round(sum(r), 1)}


def main():
    picks = load_picks()
    api = KISApi()
    cache = {}

    def series(code):
        if code not in cache:
            d = api._get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20260401",
                 "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d"),
                 "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"})
            rs = [(x["stck_bsop_date"], float(x["stck_oprc"]), float(x["stck_clpr"]))
                  for x in d.get("output2", []) if x.get("stck_oprc")]
            rs.sort()
            cache[code] = rs
        return cache[code]

    bench = series("069500")
    bmap = {dt: c for dt, o, c in bench}

    gaps, intraday = [], []
    hold = {n: [] for n in HOLDS}
    holdx = {n: [] for n in HOLDS}
    for dt, code in picks.items():
        ymd = dt.replace("-", "")
        s = series(code)
        idx = next((i for i, (d, o, c) in enumerate(s) if d == ymd), None)
        if idx is None or idx == 0:
            continue
        prev_c, o, c0 = s[idx - 1][2], s[idx][1], s[idx][2]
        if prev_c > 0:
            gaps.append((o / prev_c - 1) * 100)
        if o > 0:
            intraday.append((c0 / o - 1) * 100)
        bo = bmap.get(ymd)
        for n in HOLDS:
            if idx + n < len(s) and o > 0:
                r = (s[idx + n][2] / o - 1) * 100
                hold[n].append(r)
                bn = bmap.get(s[idx + n][0])
                if bo and bn:
                    holdx[n].append(r - (bn / bo - 1) * 100)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_days": len(gaps),
        "avg_gap": round(statistics.mean(gaps), 2) if gaps else None,
        "gap_up_pct": round(sum(1 for g in gaps if g > 0) / len(gaps) * 100, 0) if gaps else None,
        "intraday": summ(intraday),
        "holding": [{"days": n, **(summ(hold[n]) or {})} for n in HOLDS],
        "excess": [{"days": n, **(summ(holdx[n]) or {})} for n in HOLDS],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ 저장", OUT.name, "| 픽", out["n_days"], "일 | 평균갭", out["avg_gap"], "%")


if __name__ == "__main__":
    main()
