#!/usr/bin/env python3
"""
게임주 주간 인텔리전스 리포트 — web_search로 신작 일정 + 매출 성과 조사 → 텔레그램.

하루 2회(09:00·22:00) LaunchAgent(com.mandoo.game-intel)로 실행:
  1) 국내 상장(코스피/코스닥) 게임사의 향후 ~3개월 신작 출시 일정 (이미 추적중 외 신규는 🆕)
  2) 추적/주요 상장사 게임의 최근 구글플레이 매출순위 대략 위치·성과 뉴스
텔레그램은 '출시 예정 게임 D-day' 요약만 아침 1회 전송(전체 인텔은 반복 이슈라 미전송).
전체 리포트·순위는 game_intel.json으로 사이트(/game-watch/)에만 반영(자동 commit/push).
신작 출시 '일정'(upcoming_game_launches.json)은 오류가 많아 사람이 확인 후 수동 반영한다.

매출순위 정확도는 무료 web_search 특성상 '대략'이며, 없는 수치를 지어내지 않도록 프롬프트로 강제.
"""
import os
import json
import subprocess
import urllib.request
from datetime import datetime, date
from pathlib import Path

HOME = Path.home()
SITE = HOME / "manddo-site"
UPCOMING = SITE / "scripts" / "upcoming_game_launches.json"
LOG = SITE / "scripts" / "game_intel_report.log"

# 키: 파일 우선(launchd 대응 + 죽은 환경변수 회피)
_kf = HOME / "stock_auto_trade" / ".anthropic_key"
API_KEY = _kf.read_text().strip() if _kf.exists() else os.environ.get("ANTHROPIC_API_KEY", "")

TG_TOKEN = "8601217415:AAFP0LJDYYLHFWNn0jorKfhZzt2_yiJ31LY"  # 주식분석봇
TG_CHAT = "6579078641"


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {m}\n")
    print(m)


def tg(text):
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [text]:
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=json.dumps({"chat_id": TG_CHAT, "text": chunk,
                                 "disable_web_page_preview": True}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log(f"텔레그램 실패: {e}")


def registered_summary():
    try:
        evs = json.loads(UPCOMING.read_text()).get("events", [])
        return "\n".join(f"- {e['company']}({e['code']}) {e.get('game','')} / 예정 {e['date']}" for e in evs) or "(없음)"
    except Exception:
        return "(목록 읽기 실패)"


def build_prompt():
    today = date.today().isoformat()
    return f"""너는 한국 게임주 투자자를 위한 주간 인텔리전스 리포터다. **웹 검색을 적극 사용**해 아래를 조사하고, 텔레그램으로 보낼 간결한 한국어 리포트를 작성하라. 오늘 날짜: {today}.

## 1) 신작 출시 일정 (향후 약 3개월)
한국 증시 **상장(코스피/코스닥) 게임사**가 곧 출시 예정인 신작을 검색. 각 항목: 회사명·종목코드·신작명·출시예정일(확정/잠정 구분)·한 줄 근거. **비상장사(스마일게이트 등)는 제외.** 종목코드가 불확실하면 '코드미상'으로.
아래는 우리가 **이미 추적 중**인 게임이다. 이것 외에 **새로 발견된** 신작은 앞에 🆕를 붙여라:
{registered_summary()}

## 2) 매출/성과 신호
아래 게임들의 **최근 구글플레이(및 앱스토어) 매출순위 대략 위치**와 성과 뉴스(급상승/급락/신작 흥행·부진)를 검색:
- ★고정 추적: **데브시스터즈(194480)의 쿠키런 시리즈**(쿠키런: 킹덤/오븐스매시/크럼블 등) — 반드시 현재 매출 성과·순위를 포함하라.
- 추적 중인 게임(위 목록) + 주요 상장사 대표작(리니지·오딘·나이트크로우·아키에이지워 등)
⚠️ **정확한 순위를 확인 못 하면 숫자를 지어내지 말고** '대략 O위권' 또는 '뉴스상 호조/부진'으로만. 확인된 것만.
⚠️ **매출 순위와 무료(인기·다운로드) 순위를 명확히 구분**해서 적어라(예: '애플 무료 1위, 매출 3위' — 이 둘은 다름). 출시 당일·초반 흥행 수치는 '출시 초기'라고 **시점을 밝히고**, 현재 매출 순위가 확인 안 되면 '현재 매출 순위 미확인'이라고 써라.

## 출력 형식 (텔레그램용 플레인텍스트, 이모지 활용, 450단어 이내)
🎮 게임주 주간 인텔 ({today})
━━━━━━━━━
📅 신작 일정
- (회사/코드/신작/예정일/근거)
📊 매출·성과
- (게임/대략 순위 또는 뉴스)
💡 한 줄 정리
- (이번 주 관전 포인트)

각 항목 근거 출처를 (매체명)으로 짧게. 확인 안 된 건 넣지 마라."""


def extract_rankings(client, report_text):
    """리포트 텍스트에서 매출 순위 정보를 JSON 배열로 추출 (2차 호출, web_search 없음)."""
    p = ("다음 게임주 리포트에서 **매출(그로싱) 순위** 정보를 JSON 배열로 추출하라. "
         "각 원소: {\"game\":\"게임명\",\"company\":\"회사\",\"code\":\"종목코드\",\"market\":\"매출 순위\","
         "\"rank\":\"1위권|10위권|30위권|100위권 밖|미상\",\"trend\":\"up|down|flat|new|na\","
         "\"note\":\"짧은 근거\",\"source\":\"매체\"}.\n"
         "⚠️ rank 규칙(엄수):\n"
         "1) **매출 순위만** 기준. 무료·인기·다운로드 순위를 매출 순위로 쓰지 마라(둘은 완전히 다름). "
         "'무료 1위'만 있고 매출 순위가 없으면 그 무료 순위로 rank를 매기지 말 것.\n"
         "2) 출시 당일·업데이트 직후 같은 **초기 피크**는 현재값이 아니다. 현재 매출 순위가 "
         "명확하지 않으면 rank=\"미상\", 시점·피크는 note에만 남겨라(예: '출시 초기 매출 5위').\n"
         "3) 리포트에 언급된 게임만, 지어내지 말 것. 데브시스터즈 쿠키런이 있으면 포함하되 위 규칙 그대로 적용.\n"
         "JSON 배열만 출력.\n\n" + report_text)
    try:
        r = client.messages.create(model="claude-sonnet-4-6", max_tokens=1500,
                                    messages=[{"role": "user", "content": p}])
        txt = "".join(getattr(b, "text", "") for b in r.content if b.type == "text")
        import re
        m = re.search(r"\[.*\]", txt, re.S)
        return json.loads(m.group(0)) if m else []
    except Exception as e:
        log(f"rankings 추출 실패: {e}")
        return []


def dday_telegram():
    """추적 중인 출시 예정 게임의 D-day 간결 요약. 예정 없으면 None."""
    try:
        evs = json.loads(UPCOMING.read_text()).get("events", [])
    except Exception:
        return None
    today = date.today()
    rows = []
    for e in evs:
        ds = str(e.get("date", ""))
        if len(ds) != 8:
            continue
        try:
            d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        except ValueError:
            continue
        dd = (d - today).days
        if dd < 0:
            continue  # 이미 출시된 건 제외
        rows.append((dd, d, e))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    lines = [f"🎮 출시 예정 게임 D-day ({today} 기준)", "━━━━━━━━━━━━"]
    for dd, d, e in rows:
        tag = "🔥" if dd <= 7 else ("⏰" if dd <= 30 else "•")
        dd_txt = "D-DAY" if dd == 0 else f"D-{dd}"
        lines.append(f"{tag} {dd_txt} · {e.get('game','')} — {e.get('company','')}({e.get('code','')}) {d.isoformat()}")
    return "\n".join(lines)


def main():
    if not API_KEY:
        log("ANTHROPIC 키 없음 — 종료")
        return
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=3000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": build_prompt()}],
        )
    except Exception as e:
        log(f"API 실패: {e}")
        tg(f"⚠️ 게임주 주간 인텔 생성 실패: {e}")
        return
    text = "".join(getattr(b, "text", "") for b in r.content if b.type == "text").strip()
    searches = sum(1 for b in r.content if b.type == "server_tool_use")
    if not text:
        log("빈 응답")
        return
    i = text.find("🎮")
    if i > 0:
        text = text[i:]
    rankings = extract_rankings(client, text)
    log(f"리포트 생성 (검색 {searches}회, 본문 {len(text)}자, 순위 {len(rankings)}건)")
    # 텔레그램: 반복되는 전체 인텔 대신 'D-day 요약'만, 아침 1회(스팸 방지).
    # 전체 리포트/순위는 아래 game_intel.json으로 사이트(/game-watch/)에만 반영.
    if datetime.now().hour < 12:
        dmsg = dday_telegram()
        if dmsg:
            tg(dmsg)
    # 로컬 보관 + 웹 노출용 JSON (/game-watch/ 하단에서 fetch)
    (SITE / "scripts" / "game_intel_last.txt").write_text(
        f"[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n", encoding="utf-8")
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "report": text, "rankings": rankings}
    (SITE / "tools" / "data" / "game_intel.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-C", str(SITE), "add", "tools/data/game_intel.json"], check=False, env=env)
        subprocess.run(["git", "-C", str(SITE), "commit", "-q", "-m",
                        f"chore(game-intel): 주간 리포트 갱신 ({date.today()})"], env=env)
        subprocess.run(["git", "-C", str(SITE), "push", "-q"], check=False, env=env)
    except Exception as e:
        log(f"git 실패: {e}")
    log("✓ 텔레그램 전송 + 웹 반영 완료")


if __name__ == "__main__":
    main()
