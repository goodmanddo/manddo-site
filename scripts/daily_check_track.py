#!/usr/bin/env python3
"""종목 수익 일별 체크 — 매주 월요일에 산 종목들의 그 주 일별 종가 수익률을 두 전략으로 나눠 기록.

현재 전략: 위클리 픽 자동매매(메인 1픽 + 서브 3종목 + 섹터 ETF)가 실제로 산 종목.
           진입가 = 실제 매수가, 매수일~매도일(없으면 그 주 마지막 거래일) 종가로 수익률.
           ~/stock_auto_trade/weekly_pick_*_state.json 에서 읽기만 함 (매매 로직 무관).
추가 전략: dividend_sector_pick.py 의 추세섹터 × 배당·국민연금 5종목.
           월요일(그 주 첫 거래일) 시가에 샀다고 보고 금요일 종가까지 (가상).
비교: 같은 주 코스피(KS11)·코스닥(KQ11) 월요일 시가 대비. 공개 JSON엔 수익률(%)만, 수량·금액 없음.
출력: manddo-site/tools/data/daily_check.json → /check/
LaunchAgent: com.mandoo.daily-check-track (평일 15:50 + 일 21:30 — 일요일 21시 종목 선정 직후 다음 주 표를 종목만 미리 띄움)
"""
import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr

ROOT = Path.home() / "manddo-site"
TRADE = Path.home() / "stock_auto_trade"
PICK = ROOT / "tools" / "data" / "dividend_sector_pick.json"
OUT = ROOT / "tools" / "data" / "daily_check.json"
LOG = ROOT / "scripts" / "daily_check_track.log"

MAIN_STATE = TRADE / "weekly_pick_state.json"
ETF_STATE = TRADE / "weekly_pick_etf_state.json"
SUB_STATE = TRADE / "weekly_pick_sub_state.json"
SUB_PLAN = TRADE / "weekly_pick_subs.json"

START_WEEK = "2026-10-05"  # 서브 전략 시작 주 — 이 주부터 현재 전략 = 메인+서브+ETF 구성
BENCH = [("KS11", "코스피"), ("KQ11", "코스닥")]
SLOT = {"main": "메인", "rank2": "서브 · 같은섹터2", "rank3": "서브 · 같은섹터3",
        "other_sector": "서브 · 다른섹터", "etf": "섹터 ETF"}
SLOT_ORDER = list(SLOT)


def log(m):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def friday_of(monday):
    return (date.fromisoformat(monday) + timedelta(days=4)).isoformat()


def week_monday(d):
    d = date.fromisoformat(d[:10])
    return (d - timedelta(days=d.weekday())).isoformat()


def fill_daily(row, start, end):
    """start~end 일봉 종가로 row['daily'] 채움. row에 entry 없으면 첫 거래일 시가를 진입가로."""
    df = fdr.DataReader(row["code"], start, end).dropna(subset=["Close"])
    if df.empty:
        return
    if row.get("entry") is None:
        row["entry"] = float(df["Open"].iloc[0])
    row["daily"] = {i.date().isoformat(): round((float(c) / row["entry"] - 1) * 100, 2)
                    for i, c in df["Close"].items()}


def current_rows(monday):
    """그 주에 위클리 픽 트레이더들이 실제로 산 포지션 (보유중 + 청산 완료)"""
    friday = friday_of(monday)
    rows = []

    def add(slot, p):
        if p and monday <= p.get("buy_date", "") <= friday:
            rows.append({"code": p["code"], "name": p["name"], "slot": slot,
                         "entry": float(p["buy_price"]), "buy_date": p["buy_date"],
                         "sell_date": p.get("sell_date")})

    for path, slot in ((MAIN_STATE, "main"), (ETF_STATE, "etf")):
        st = load(path)
        add(slot, st.get("current_position"))
        for h in st.get("history", []):
            add(slot, h)
    sub = load(SUB_STATE)
    # positions는 종목코드를 '키'로만 저장하고 값엔 code가 없음 → 키에서 주입(history엔 code 있음)
    sub_positions = [{**p, "code": code} for code, p in sub.get("positions", {}).items()]
    for p in sub_positions + sub.get("history", []):
        add(p.get("slot", "rank2"), p)

    # 이번 주 계획(서브 픽 파일)에 있는데 아직 안 산 종목 → 월요일 전이면 '매수 예정', 이후면 '미체결'(화·수 재시도 중일 수 있음)
    plan = load(SUB_PLAN)
    if plan.get("generated_at") and week_monday_after(plan["generated_at"]) == monday:
        bought = {r["code"] for r in rows}
        planned = [("main", plan.get("main"))] + [(s["slot"], s) for s in plan.get("subs", [])]
        before = date.today().isoformat() < monday
        for slot, p in planned:
            if p and p["code"] not in bought:
                rows.append({"code": p["code"], "name": p["name"], "slot": slot,
                             "planned" if before else "unfilled": True})
    rows.sort(key=lambda r: (SLOT_ORDER.index(r["slot"]) if r["slot"] in SLOT_ORDER else 9, r.get("buy_date", "")))
    for r in rows:
        r["slot_label"] = SLOT.get(r["slot"], r["slot"])
    return rows


def week_monday_after(ts):
    """평일이면 그 주 월요일, 주말(일 23:00 생성 등)이면 다음 주 월요일"""
    d = date.fromisoformat(ts[:10])
    d = d + timedelta(days=7 - d.weekday()) if d.weekday() >= 5 else d - timedelta(days=d.weekday())
    return d.isoformat()


def summarize(block, days):
    rows = [r for r in block["rows"] if r.get("daily")]
    avg = {}
    for d in days:
        # 그날 보유중인 종목만 평균 (매수 전·매도 후 날짜는 빠짐)
        vals = [r["daily"][d] for r in rows if d in r["daily"]]
        if vals:
            avg[d] = round(sum(vals) / len(vals), 2)
    block["avg"] = avg
    # 주간 결과 = 종목별 마지막 기록 수익률의 단순평균 (청산된 종목은 청산일 종가 기준)
    finals = [list(r["daily"].values())[-1] for r in rows]
    block["result"] = round(sum(finals) / len(finals), 2) if finals else None


def track(rnd):
    monday, friday = rnd["week"], friday_of(rnd["week"])
    rnd["current"]["rows"] = current_rows(monday)
    if date.today().isoformat() < monday:  # 일요일 밤 미리 등록된 다음 주 — 종목만 보여주고 수익률은 월요일부터
        rnd.update(days=[], status="대기", bench_result={})
        for key in ("current", "extra"):
            rnd[key].update(avg={}, result=None)
        return
    for r in rnd["current"]["rows"]:
        if not r.get("unfilled") and not r.get("planned"):
            fill_daily(r, r["buy_date"], min(r.get("sell_date") or friday, friday))
    for r in rnd["extra"]["rows"] + rnd["bench"]:
        fill_daily(r, monday, friday)
    days = sorted({d for blk in (rnd["current"], rnd["extra"]) for r in blk["rows"] for d in r.get("daily", {})}
                  | {d for b in rnd["bench"] for d in b.get("daily", {})})
    rnd["days"] = days
    for key in ("current", "extra"):
        summarize(rnd[key], days)
    last = days[-1] if days else None
    rnd["bench_result"] = {b["code"]: b.get("daily", {}).get(last) for b in rnd["bench"]}
    done = bool(days) and (last == friday or date.today().isoformat() > friday)
    rnd["status"] = "완료" if done else "진행중"


def totals(rounds, key):
    done = [r for r in rounds if r["status"] == "완료" and r[key].get("result") is not None]
    t = {"rounds": len(done)}
    if done:
        cum, ks = 1.0, 1.0
        for r in sorted(done, key=lambda r: r["week"]):
            cum *= 1 + r[key]["result"] / 100
            ks *= 1 + (r["bench_result"].get("KS11") or 0) / 100
        t.update(cum=round((cum - 1) * 100, 2), cum_KS11=round((ks - 1) * 100, 2),
                 beat_kospi=sum(r[key]["result"] > (r["bench_result"].get("KS11") or 0) for r in done))
    return t


def main():
    rounds = {r["week"]: r for r in load(OUT).get("rounds", [])}
    this_monday = week_monday(date.today().isoformat())
    pick = load(PICK)
    plan = load(SUB_PLAN)
    # 일요일 21시에 다음 주 종목이 정해지면 그 주 라운드를 미리 열어 종목만 표시
    last_week = max([this_monday, pick.get("week") or this_monday]
                    + ([week_monday_after(plan["generated_at"])] if plan.get("generated_at") else []))

    w = START_WEEK
    while w <= last_week:
        if w not in rounds:
            rounds[w] = {"week": w, "current": {"rows": []}, "extra": {"rows": [], "sectors": []},
                         "bench": [{"code": c, "name": n} for c, n in BENCH]}
        # 추가 전략은 그 주용으로 뽑힌 세트가 있을 때만 (지난 주 소급 X — 그때는 세트가 없었음)
        # 월요일 전(대기)이면 일요일 21시 최종 세트로 갱신, 월요일 이후엔 고정
        if pick.get("week") == w and (not rounds[w]["extra"]["rows"] or date.today().isoformat() < w):
            rounds[w]["extra"] = {"sectors": pick.get("sectors_used", []),
                                  "rows": [{k: p[k] for k in ("code", "name", "sector", "icon", "kind")}
                                           for p in pick["picks"]]}
            log(f"{w} 추가 전략 등록: " + ", ".join(p["name"] for p in pick["picks"]))
        w = (date.fromisoformat(w) + timedelta(days=7)).isoformat()

    for r in rounds.values():
        if r.get("status") != "완료":
            try:
                track(r)
                log(f"{r['week']} {r['status']} — 현재 {r['current']['result']}% / 추가 {r['extra']['result']}%"
                    f" / 코스피 {r['bench_result'].get('KS11')}%")
            except Exception as e:
                log(f"{r['week']} 추적 실패: {e}")

    rs = sorted(rounds.values(), key=lambda r: r["week"], reverse=True)
    for r in rs:  # 공개용: 진입가·날짜 외 매매 세부 제거
        for row in r["current"]["rows"]:
            row.pop("entry", None)
        for row in r["extra"]["rows"]:
            row.pop("entry", None)
    data = {"updated_at": datetime.now().isoformat(timespec="seconds"),
            "total": {"current": totals(rs, "current"), "extra": totals(rs, "extra")},
            "rounds": rs}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    try:
        rel = str(OUT.relative_to(ROOT))
        subprocess.run(["git", "-C", str(ROOT), "add", rel], check=False)
        r = subprocess.run(["git", "-C", str(ROOT), "commit", "-m",
                            f"chore(daily-check): {date.today()} 종목 수익 일별 체크 갱신"],
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
