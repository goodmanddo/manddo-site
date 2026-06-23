#!/usr/bin/env python3
"""
이번 주 시장 캘린더 자동 생성 — 매주 일요일 23:00 LaunchAgent로 실행.

Claude API에 "이번 주 한국 주식 투자자가 알아야 할 시장 이벤트 5~7개"를
요청해서 /ai-log/weekly_calendar.json에 저장 후 git push.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import anthropic

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "ai-log" / "weekly_calendar.json"
LOG = Path.home() / "manddo-site" / "scripts" / "weekly_calendar.log"
KEY_FILE = Path.home() / "stock_auto_trade" / ".anthropic_key"

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY") or (
    KEY_FILE.read_text().strip() if KEY_FILE.exists() else ""
)


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def week_bounds(today: date):
    # 일요일(자동 실행일)에는 다음 주(월~일)를 대상으로 한다.
    # 그래야 월요일 이후에도 이벤트가 과거가 되어 프런트에서 숨겨지지 않음.
    monday = today - timedelta(days=today.weekday())
    if today.weekday() == 6:  # 일요일 → 다가오는 주
        monday += timedelta(days=7)
    sunday = monday + timedelta(days=6)
    return monday, sunday


SYSTEM = """너는 한국 주식 투자자를 위한 시장 캘린더 큐레이터다.
이번 주(월~일) 안에 발생하는 한국 시장에 영향이 큰 이벤트를 5~7개 골라준다.

선정 기준 (우선순위):
1. 미국 주요 지표·연준 이벤트 (CPI, PPI, 고용지표, FOMC, 의장 발언)
2. 한국 거래소 옵션 만기일 / 선물 만기일
3. 한국 주요 기업 실적 발표 (코스피200 시가총액 상위 기업)
4. 한국·미국 정부 정책 (금리 결정, 무역 발표)
5. 글로벌 빅테크 실적 (한국 반도체·2차전지·플랫폼주에 영향)

각 이벤트마다 다음을 채워서 JSON 배열로 출력:
{
  "date": "YYYY-MM-DD",  // 한국 시각 기준
  "weekday": "월"|"화"|"수"|"목"|"금"|"토"|"일",
  "time": "HH:MM",  // 한국 시각 (KST), 시간 미정시 빈 문자열
  "title": "이벤트 명 (간결, 25자 이내)",
  "category": "지표"|"실적"|"정책"|"만기"|"컨퍼런스",
  "impact": "high"|"mid"|"low",
  "view": "이 이벤트가 한국 시장(특히 종목·섹터)에 미칠 영향을 1~2문장 (50~80자). 투자 권유 X, 관찰 포인트만."
}

출력 규칙:
- JSON만 출력. 설명 텍스트 X.
- 날짜는 모두 이번 주(월~일) 범위 안.
- 같은 날 여러 이벤트 가능.
- 시간순(이른 날짜·시간) 정렬.
- "view"는 권유가 아닌 관찰 포인트로 작성. 예: "발표 부진 시 반도체 약세 압력 가능", "예상치 부합 시 시장 무관심 흐름".
"""


def call_claude(week_monday: date, week_sunday: date) -> list:
    today_str = date.today().isoformat()
    user = (
        f"오늘은 {today_str} 일요일(또는 월요일 새벽).\n"
        f"이번 주 범위: {week_monday.isoformat()} (월) ~ {week_sunday.isoformat()} (일).\n"
        f"이 범위 안의 한국 시장 영향 이벤트 5~7개를 JSON 배열로만 출력해."
    )
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    # JSON 블록 추출
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"JSON 배열 파싱 실패: {text[:300]}")
    return json.loads(m.group(0))


def git_push(msg: str):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        subprocess.run(["git", "-C", str(ROOT), "add", "ai-log/weekly_calendar.json"], check=True, env=env)
        diff = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"], env=env
        )
        if diff.returncode == 0:
            log("  - 변경 없음, push skip")
            return
        subprocess.run(["git", "-C", str(ROOT), "commit", "-m", msg], check=True, env=env)
        subprocess.run(["git", "-C", str(ROOT), "push"], check=True, env=env)
        log(f"  ✓ git push: {msg}")
    except subprocess.CalledProcessError as e:
        log(f"  ! git 실패: {e}")


def main():
    if not ANTHROPIC_KEY:
        log("! ANTHROPIC_KEY 없음 — 종료")
        sys.exit(1)

    today = date.today()
    monday, sunday = week_bounds(today)
    log(f"이번 주: {monday} ~ {sunday}")

    events = call_claude(monday, sunday)
    events.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    out = {
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "events": events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    log(f"✓ {len(events)}건 저장 → {OUT.relative_to(ROOT)}")

    git_push(f"chore(ai-log): weekly calendar {monday}~{sunday}")


if __name__ == "__main__":
    main()
