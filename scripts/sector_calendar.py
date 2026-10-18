#!/usr/bin/env python3
"""섹터 캘린더 — 매일 장마감 후 섹터/테마 ETF 등락률을 계산해 그날 '오른 섹터'를
날짜별로 누적 기록한다. 사이트 달력(/stock/sector-calendar.html)에서 렌더.

판정: 섹터 ETF 일별 등락 중 +MIN_PCT 이상 오른 것 상위 MAX_CHIPS개를 그 날짜 칩으로.
출력: manddo-site/tools/data/sector_calendar.json  { "YYYY-MM-DD": [{name,icon,pct,code}], ... }
LaunchAgent: com.mandoo.sector-calendar (평일 15:40, 장마감 직후)
"""
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import FinanceDataReader as fdr

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "tools" / "data" / "sector_calendar.json"
LOG = ROOT / "scripts" / "sector_calendar.log"

MIN_PCT = 1.0     # 이 % 이상 오른 섹터만 칩으로
MAX_CHIPS = 4     # 하루 최대 칩 수(많이 오른 순)

# (표시명, 아이콘, ETF코드, 색상키) — ETF 일별 등락으로 섹터 판정
SECTORS = [
    ("반도체",       "🔲", "091160", "blue"),
    ("반도체 소부장", "🔩", "473490", "blue"),
    ("AI반도체",     "🤖", "469150", "blue"),
    ("2차전지",      "🔋", "305720", "green"),
    ("제약/바이오",   "💊", "266420", "yellow"),
    ("바이오",       "🧬", "244580", "yellow"),
    ("화장품",       "💄", "228790", "pink"),
    ("방산",         "🛡️", "449450", "gray"),
    ("게임",         "🎮", "300610", "purple"),
    ("로봇",         "🦾", "445290", "teal"),
    ("조선",         "🚢", "441680", "navy"),
    ("해운/운송",     "🚚", "140710", "navy"),
    ("자동차",       "🚗", "091180", "orange"),
    ("금융",         "🏦", "091170", "gray"),
    ("에너지/화학",   "⛽", "117460", "orange"),
    ("원자력",       "⚛️", "442320", "teal"),
    ("인터넷",       "🌐", "365000", "purple"),
]

# ETF가 없는 세부 테마 — 대표종목 바스켓의 평균 등락으로 판정 (공개 종목 시세)
THEMES = [
    ("보안",     "🔒", ["053800", "136540", "042510"], "blue"),    # 안랩·윈스·라온시큐어
    ("엔터",     "🎤", ["352820", "035900", "041510"], "purple"),  # 하이브·JYP·에스엠
    ("전력설비", "⚡", ["010120", "267260", "298040"], "orange"),  # LS일렉트릭·HD현대일렉트릭·효성중공업
    ("우주항공", "🛰️", ["099320", "189300", "012450"], "navy"),    # 쎄트렉아이·인텔리안테크·한화에어로
    ("미용의료", "💉", ["214150", "214450", "287410"], "pink"),    # 클래시스·파마리서치·제이시스메디칼
]


def log(m):
    from datetime import datetime
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def latest_change(code):
    """가장 최근 거래일의 전일 대비 등락률(%)과 그 거래일 날짜."""
    df = fdr.DataReader(code, (date.today() - timedelta(days=12)).isoformat())
    df = df.dropna(subset=["Close"])
    if len(df) < 2:
        return None, None
    chg = (df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100
    return round(float(chg), 2), df.index[-1].date().isoformat()


def basket_change(codes):
    """바스켓(여러 종목) 평균 등락률과 거래일."""
    chgs, day = [], None
    for c in codes:
        try:
            v, d = latest_change(c)
            if v is not None:
                chgs.append(v)
                day = day or d
        except Exception:
            pass
    if not chgs:
        return None, None
    return round(sum(chgs) / len(chgs), 2), day


def main():
    rows = []
    trade_day = None
    for name, icon, code, color in SECTORS:
        try:
            chg, d = latest_change(code)
            if chg is None:
                continue
            trade_day = trade_day or d
            rows.append({"name": name, "icon": icon, "pct": chg, "code": code, "color": color})
        except Exception as e:
            log(f"{name}({code}) 실패: {e}")
    for name, icon, codes, color in THEMES:
        try:
            chg, d = basket_change(codes)
            if chg is None:
                continue
            trade_day = trade_day or d
            rows.append({"name": name, "icon": icon, "pct": chg, "code": ",".join(codes), "color": color})
        except Exception as e:
            log(f"{name}(테마) 실패: {e}")
    if not trade_day:
        log("거래일 데이터 없음 — 중단")
        return

    # 그날 +MIN_PCT 이상 오른 섹터 상위 MAX_CHIPS개
    ups = sorted([r for r in rows if r["pct"] >= MIN_PCT], key=lambda r: -r["pct"])[:MAX_CHIPS]

    try:
        data = json.loads(OUT.read_text())
    except Exception:
        data = {"days": {}, "sectors": [s[0] for s in SECTORS]}
    data.setdefault("days", {})
    data["days"][trade_day] = ups
    data["updated_at"] = date.today().isoformat()
    data["sectors"] = [s[0] for s in SECTORS] + [t[0] for t in THEMES]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log(f"{trade_day}: 오른섹터 {len(ups)}개 기록 — " + ", ".join(f"{r['name']}{r['pct']:+.1f}" for r in ups))

    # git push
    try:
        rel = str(OUT.relative_to(ROOT))
        subprocess.run(["git", "-C", str(ROOT), "add", rel], check=False)
        r = subprocess.run(["git", "-C", str(ROOT), "commit", "-m",
                            f"chore(sector-calendar): {trade_day} 섹터 등락 갱신"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "-C", str(ROOT), "push"], check=False)
            log("git push 완료")
        else:
            log("변경 없음 — 커밋 스킵")
    except Exception as e:
        log(f"git 실패: {e}")


if __name__ == "__main__":
    main()
