#!/usr/bin/env python3
"""
게임주 주간 인텔리전스 리포트 — web_search로 신작 일정 + 매출 성과 조사 → 텔레그램.

주 1회 LaunchAgent(com.mandoo.game-intel)로 실행:
  1) 국내 상장(코스피/코스닥) 게임사의 향후 ~3개월 신작 출시 일정 (이미 추적중 외 신규는 🆕)
  2) 추적/주요 상장사 게임의 최근 구글플레이 매출순위 대략 위치·성과 뉴스
결과를 만또에게 텔레그램으로 전송 → 만또가 확인 후 upcoming_game_launches.json에 반영.
(자동으로 사이트에 반영하지 않음 — 게임 일정·순위는 오류·변동이 많아 사람이 한 번 거른다.)

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
위 추적 게임 및 주요 상장사 대표 모바일게임(예: 리니지·오딘·나이트크로우·아키에이지워 등)의 **최근 구글플레이 매출순위 대략 위치**나 성과 뉴스(급상승/급락/신작 흥행·부진)를 검색.
⚠️ **정확한 순위를 확인 못 하면 숫자를 지어내지 말고** '대략 O위권' 또는 '뉴스상 호조/부진'으로만 적어라. 확인된 것만.

## 출력 형식 (텔레그램용 플레인텍스트, 이모지 활용, 450단어 이내, 마크다운 기호 남발 금지)
🎮 게임주 주간 인텔 ({today})
━━━━━━━━━
📅 신작 일정
- (회사/코드/신작/예정일/근거)
📊 매출·성과
- (게임/대략 순위 또는 뉴스)
💡 한 줄 정리
- (이번 주 관전 포인트)

각 항목 끝에 근거 출처를 (매체명)으로 짧게. 확인 안 된 건 넣지 마라."""


def main():
    if not API_KEY:
        log("ANTHROPIC 키 없음 — 종료")
        return
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=3000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
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
    # 서두 잡담 제거: 🎮 헤더부터 시작
    i = text.find("🎮")
    if i > 0:
        text = text[i:]
    log(f"리포트 생성 (검색 {searches}회, {len(text)}자)")
    tg(text)
    # 로컬 보관 + 웹 노출용 JSON (/game-watch/ 하단에서 fetch)
    (SITE / "scripts" / "game_intel_last.txt").write_text(
        f"[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n", encoding="utf-8")
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "report": text}
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
