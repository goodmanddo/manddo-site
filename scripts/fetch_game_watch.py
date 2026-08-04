#!/usr/bin/env python3
"""
신작 출시 예정 게임주 '오픈 전 러너업' 관찰 도구 데이터 빌더.

upcoming_game_launches.json(운영자 편집)의 각 종목에 대해:
  - 출시(예정)일까지 D-day
  - 출시 D-40 / D-30 / D-7 시점 종가 대비 현재 수익률 (절대 + 시장초과)
  - 이미 출시됐으면 출시 후 경과·수익률
을 한투 수정주가로 계산 → tools/data/game_watch.json.

숫자는 실시세로 결정론 계산(환각 없음). 출시일 자체는 운영자 큐레이션(잠정)이며
변동될 수 있음 — 페이지에 명시.

평일 장 마감 후 LaunchAgent(com.mandoo.game-watch)로 실행 → git push.
"""
import os
import sys
import json
import subprocess
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, "/Users/mandoo/stock_auto_trade")
from kis_api import KISApi  # noqa: E402

SITE = Path.home() / "manddo-site"
SRC = SITE / "scripts" / "upcoming_game_launches.json"
OUT = SITE / "tools" / "data" / "game_watch.json"
LOG = SITE / "scripts" / "game_watch.log"
MILESTONES = [("d40", -40), ("d30", -30), ("d7", -7)]
BENCH_NAME = {"069500": "코스피200", "229200": "코스닥150"}


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {m}\n")
    print(m)


def candles(api, code, bgn, end):
    d = api._get(
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
         "FID_INPUT_DATE_1": bgn, "FID_INPUT_DATE_2": end,
         "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"})
    r = [(x["stck_bsop_date"], float(x["stck_clpr"])) for x in d.get("output2", []) if x.get("stck_clpr")]
    r.sort()
    return r


def c_before(rows, ymd):
    p = None
    for dd, c in rows:
        if dd <= ymd:
            p = (dd, c)
        else:
            break
    return p


def c_after(rows, ymd):
    for dd, c in rows:
        if dd >= ymd:
            return (dd, c)
    return None


def ymd(d):
    return d.strftime("%Y%m%d")


def ret_from(rows, ms_ymd, cur):
    """ms 시점(그 이전 마지막 거래일) 종가 대비 현재 수익률 %. 미도래/무데이터면 None."""
    b = c_before(rows, ms_ymd)
    if not b or b[1] <= 0:
        return None
    return round((cur / b[1] - 1) * 100, 1)


def main():
    cfg = json.loads(SRC.read_text())
    api = KISApi()
    today = date.today()
    today_s = ymd(today)
    items = []

    for ev in cfg.get("events", []):
        try:
            L = datetime.strptime(ev["date"], "%Y%m%d").date()
        except Exception:
            log(f"! 날짜 오류 스킵: {ev.get('company')}")
            continue
        code = ev["code"]
        bench = ev.get("bench", "229200")
        bgn = ymd(L - timedelta(days=80))
        end = ymd(min(today, L + timedelta(days=30)))
        try:
            srows = candles(api, code, bgn, end)
            brows = candles(api, bench, bgn, end)
        except Exception as e:
            log(f"! {ev['company']} 시세 실패: {e}")
            continue
        if not srows:
            log(f"! {ev['company']} 데이터 없음")
            continue
        cur = srows[-1][1]
        bcur = brows[-1][1] if brows else None
        dday = (L - today).days
        launched = today >= L

        mile = {}
        for key, off in MILESTONES:
            ms = ymd(L + timedelta(days=off))
            future = ms > today_s
            if future:
                mile[key] = {"status": "pending"}  # 아직 안 옴
                continue
            r = ret_from(srows, ms, cur)
            # 시장초과 = 종목수익 − 벤치수익 (같은 기준일)
            exc = None
            if r is not None and bcur:
                br = ret_from(brows, ms, bcur)
                if br is not None:
                    exc = round(r - br, 1)
            mile[key] = {"status": "done", "ret": r, "excess": exc}

        post = None
        if launched:
            d0 = c_after(srows, ymd(L))
            if d0 and d0[1] > 0:
                post = round((cur / d0[1] - 1) * 100, 1)

        items.append({
            "company": ev["company"], "code": code, "game": ev.get("game", ""),
            "launch_date": ev["date"], "status": ev.get("status", "expected"),
            "note": ev.get("note", ""),
            "bench": bench, "bench_name": BENCH_NAME.get(bench, ""),
            "price": int(cur), "dday": dday, "launched": launched,
            "milestones": mile, "post_ret": post,
        })

    # 정렬: 미출시(D-day 임박순) 먼저, 그다음 최근 출시
    items.sort(key=lambda x: (x["launched"], abs(x["dday"]) if not x["launched"] else -x["dday"]))

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(items),
        "milestone_note": "각 수치는 '출시 D-40/D-30/D-7 시점 종가 대비 현재 수익률'. 출시일은 잠정이며 변동될 수 있음.",
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✓ 저장 {OUT.name} ({len(items)}종목)")

    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-C", str(SITE), "add", "tools/data/game_watch.json"], check=True, env=env)
        r = subprocess.run(["git", "-C", str(SITE), "commit", "-q", "-m",
                            f"chore(game-watch): 신작 러너업 추적 갱신 ({date.today()})"], env=env)
        if r.returncode == 0:
            subprocess.run(["git", "-C", str(SITE), "push", "-q"], check=True, env=env)
            log("✓ git push")
    except Exception as e:
        log(f"git 실패: {e}")


if __name__ == "__main__":
    main()
