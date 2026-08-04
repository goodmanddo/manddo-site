#!/usr/bin/env python3
"""
출시일 확정 대기 게임 자동 등록 — 매일 web_search로 출시일 확정 여부 확인.

pending_registrations.json의 각 게임에 대해:
  - web_search로 '정식 출시일이 확정 공개됐는지' 확인
  - 확정(YYYYMMDD)이면 upcoming_game_launches.json에 추가 + pending에서 제거
    + fetch_game_watch.py 실행(카운트다운 갱신) + 텔레그램 알림
  - 미확정이면 대기(다음 실행 재시도)
pending이 비면 API 호출 없이 즉시 종료(비용 0). 앞으로 '쇼케이스 대기' 게임은
pending에 넣기만 하면 확정 시 자동 등록된다.

날짜를 지어내지 않도록 프롬프트로 강제(미확정이면 confirmed=false).
"""
import os
import re
import json
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
SITE = HOME / "manddo-site"
PENDING = SITE / "scripts" / "pending_registrations.json"
UPCOMING = SITE / "scripts" / "upcoming_game_launches.json"
FETCH = SITE / "scripts" / "fetch_game_watch.py"
LOG = SITE / "scripts" / "register_pending_game.log"

_kf = HOME / "stock_auto_trade" / ".anthropic_key"
API_KEY = _kf.read_text().strip() if _kf.exists() else os.environ.get("ANTHROPIC_API_KEY", "")
TG_TOKEN = "8601217415:AAFP0LJDYYLHFWNn0jorKfhZzt2_yiJ31LY"
TG_CHAT = "6579078641"


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {m}\n")
    print(m)


def tg(text):
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"텔레그램 실패: {e}")


def check_launch_date(client, g):
    """web_search로 출시일 확정 여부 확인 → {'confirmed':bool,'date':'YYYYMMDD'|None}."""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""오늘은 {today}. 웹 검색으로 '{g['company']}'의 모바일게임 '{g['game']}'의 **정식 출시일**이 공개·확정됐는지 확인하라.

규칙:
- 구체적 출시일(연·월·일)이 공식 발표됐으면 confirmed=true, date는 YYYYMMDD.
- '월만' 발표(예: 10월)면 해당 월 15일을 date로, confirmed=false(잠정).
- 아직 미공개면 confirmed=false, date=null.
- **날짜를 지어내지 마라.** 검색으로 확인된 것만.

JSON 객체 하나만 출력: {{"confirmed": true|false, "date": "YYYYMMDD"|null, "source": "매체명"}}"""
    r = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1500,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
        messages=[{"role": "user", "content": prompt}],
    )
    txt = "".join(getattr(b, "text", "") for b in r.content if b.type == "text")
    m = re.search(r"\{[^{}]*\}", txt.replace("\n", " "))
    if not m:
        return {"confirmed": False, "date": None}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"confirmed": False, "date": None}


def main():
    data = json.loads(PENDING.read_text())
    pend = data.get("pending", [])
    if not pend:
        log("대기 게임 없음 — 종료")
        return
    if not API_KEY:
        log("키 없음 — 종료")
        return
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)

    up = json.loads(UPCOMING.read_text())
    up_events = up.get("events", [])
    up_keys = {(e["code"], e.get("game", "")) for e in up_events}

    still, registered = [], []
    for g in pend:
        try:
            res = check_launch_date(client, g)
        except Exception as e:
            log(f"{g['company']} 확인 실패: {e}")
            still.append(g)
            continue
        date = res.get("date")
        confirmed = res.get("confirmed") and date and re.fullmatch(r"\d{8}", str(date))
        if not confirmed:
            log(f"{g['company']} {g['game']}: 아직 미확정 — 대기")
            still.append(g)
            continue
        key = (g["code"], g["game"])
        if key not in up_keys:
            up_events.append({
                "company": g["company"], "code": g["code"], "game": g["game"],
                "date": str(date), "status": "confirmed", "bench": g.get("bench", "229200"),
                "note": f"자동등록 {datetime.now():%Y-%m-%d} (출처 {res.get('source','?')})",
            })
            up_keys.add(key)
        registered.append((g, str(date), res.get("source", "?")))
        log(f"✓ 확정 등록: {g['company']} {g['game']} → {date}")

    if registered:
        up["events"] = up_events
        UPCOMING.write_text(json.dumps(up, ensure_ascii=False, indent=2), encoding="utf-8")
        data["pending"] = still
        PENDING.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 카운트다운 데이터 갱신(자체 git push)
        subprocess.run(["/opt/homebrew/bin/python3", str(FETCH)], check=False)
        # upcoming/pending 변경도 커밋
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-C", str(SITE), "add", "scripts/upcoming_game_launches.json",
                        "scripts/pending_registrations.json"], check=False, env=env)
        subprocess.run(["git", "-C", str(SITE), "commit", "-q", "-m",
                        "data(game-watch): 대기 게임 출시일 확정 자동등록"], env=env)
        subprocess.run(["git", "-C", str(SITE), "push", "-q"], check=False, env=env)
        msg = "🎮 게임 자동 등록 완료\n"
        for g, d, src in registered:
            dk = f"{d[:4]}.{d[4:6]}.{d[6:]}"
            msg += f"✅ {g['company']} {g['game']}\n   출시 {dk} (출처 {src})\n"
        msg += "→ manddo.kr/game-watch 카운트다운 반영됨"
        tg(msg)
    else:
        log("이번 실행 신규 확정 없음")


if __name__ == "__main__":
    main()
