#!/usr/bin/env python3
"""
장 마감 후 AI 시장 판단 초안 생성 → Notion DB에 상태='초안'으로 적재

평일 16:00 LaunchAgent 실행 (15:30 update_ai_log.py 후 30분).
data.json의 오늘 매매·수익률 데이터를 근거로 "오늘 AI의 시장 판단" 초안 작성.
사용자는 Notion에서 열어 자기 관찰을 덧붙이고 '게시대기'로 바꿔 게시.
"""

import json
import os
# 자동: 환경변수 없으면 .anthropic_key 파일에서 로드
_kf=os.path.expanduser("~/stock_auto_trade/.anthropic_key");os.environ.setdefault("ANTHROPIC_API_KEY", open(_kf).read().strip()) if os.path.isfile(_kf) else None
import re
import sys
from datetime import datetime, date
from pathlib import Path

import anthropic
import requests

HOME = Path.home()
NOTION_CFG = json.loads((HOME / ".config/manddo/notion.json").read_text())
NOTION_TOKEN = NOTION_CFG["token"]
DB_ID = NOTION_CFG["blog_db_id"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

DATA_FILE = HOME / "manddo-site" / "ai-log" / "data.json"
LOG_FILE = HOME / "manddo-site" / "scripts" / "market_judgment.log"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def fmt_pct(v):
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def summarize_data(d):
    """data.json → 프롬프트에 넣을 요약 텍스트"""
    parts = []
    parts.append(f"- 기준일: {d.get('date')}")
    parts.append(f"- 오늘 포트폴리오 변화: {fmt_pct(d.get('day_return'))}")
    parts.append(f"- 누적 수익률: {fmt_pct(d.get('cumulative_return'))} (시작 {d.get('start_date')})")
    parts.append(f"- 이번 주 변화: {fmt_pct(d.get('week_return'))}")
    parts.append(f"- 승률: {d.get('win_rate', 0)}% (누적 {d.get('total_trades', 0)}건)")

    tc = d.get("today_trade_count", 0)
    if tc:
        parts.append(
            f"- 오늘 매도: {tc}건 (익절 {d.get('today_win_count', 0)} · 손절 {d.get('today_loss_count', 0)} · 평균 {fmt_pct(d.get('today_realized_avg'))})"
        )
    else:
        parts.append("- 오늘 매도: 없음 (관망)")

    exits = d.get("exits", [])
    if exits:
        parts.append("\n**오늘 매도 상세:**")
        for e in exits[:5]:
            parts.append(f"- {e['name']} ({e.get('market', '')}) · {fmt_pct(e.get('return_pct'))} · 보유 {e.get('hold_days', 0)}일 · {'익절' if e.get('type') == 'win' else '손절'}")

    buys = d.get("new_buys", [])
    if buys:
        parts.append("\n**최근 7일 신규 편입:**")
        for b in buys[:5]:
            parts.append(f"- {b['name']} ({b.get('market', '')}) · 진입가 대비 {fmt_pct(b.get('entry_to_current_pct'))} · 비중 {b.get('weight_pct', 0)}% · 보유 {b.get('hold_days', 0)}일")

    holds = d.get("holdings", [])
    if holds:
        parts.append("\n**현재 보유 상위 5:**")
        for h in holds[:5]:
            parts.append(f"- {h['name']} · 비중 {h.get('weight_pct', 0)}%")

    losses = d.get("weekly_losses", [])
    if losses:
        parts.append("\n**이번 주 손실 사례:**")
        for l in losses[:3]:
            parts.append(f"- {l['name']} · {fmt_pct(l.get('return_pct'))}")

    return "\n".join(parts)


def build_prompt(data):
    summary = summarize_data(data)
    today_kor = date.today().strftime("%Y년 %-m월 %-d일")
    return f"""manddo.kr 사이트의 "오늘 AI의 시장 판단" 연재 블로그 초안을 작성해주세요.
아래는 오늘({today_kor}) AI 자동매매 봇의 결과 데이터입니다.

{summary}

## 글의 역할
- 매일 장 마감 후 AI가 **오늘 시장을 어떻게 읽었는지** 관찰자 시점에서 해석
- 블로그 연재 포맷. 사용자(사람 운영자)가 나중에 자기 관찰을 덧붙일 것
- 독자 흥미: "AI가 저 매매를 왜 했는지" + "시장을 어떻게 보고 있는지" 궁금한 사람

## 톤·형식
- 한국어, 3인칭 관찰자 (AI를 "AI" 또는 "그"로 지칭, 1인칭 금지)
- 본문 분량: 600~800자 (한글 기준)
- 마크다운. 소제목(##) 2개 권장
- 내부 링크 1개 이상: `[AI 매매 일지](/ai-log/)` 형태
- 숫자는 1~2개만 핵심적으로 인용 (나열 금지)
- 마지막 단락: **독자가 자기 생각을 덧붙일 여지**를 남길 것. 열린 질문 또는 단정하지 않는 톤.
- "오늘 AI는 ~했다. 이건 ~한 시장을 읽은 것으로 보인다" 류의 해석 중심

## 제목 스타일
- 구체적이고 호기심 자극. "AI가 오늘 {{구체적 행동}} 이유" 류.
- 예: "AI가 오늘 들어가지 않은 이유", "반도체를 털어낸 날, AI가 본 것", "손절 1건, AI의 기준선"

## 출력 형식
**JSON 한 개만** 반환 (배열 아님). 코드블럭·다른 텍스트 금지.
{{
  "title": "제목",
  "slug": "url-safe-slug (영문 또는 한글-하이픈)",
  "summary": "SEO용 1문장 요약 (100자 이내)",
  "body": "마크다운 본문"
}}
"""


def generate_draft(data):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = build_prompt(data)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError(f"JSON 응답 파싱 실패: {text[:200]}")
    return json.loads(m.group(0))


def md_to_blocks(body):
    blocks = []
    for line in body.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]}})
        elif line.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}})
        elif line.startswith("- "):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": rich_from_md(line[2:])}})
        elif line.startswith("> "):
            blocks.append({"object": "block", "type": "quote",
                           "quote": {"rich_text": rich_from_md(line[2:])}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": rich_from_md(line)}})
    return blocks


def rich_from_md(text):
    parts = []
    i = 0
    s = text
    while i < len(s):
        m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", s[i:])
        if m:
            url = m.group(2)
            if url.startswith("/"):
                url = "https://manddo.kr" + url
            parts.append({"type": "text", "text": {"content": m.group(1), "link": {"url": url}}})
            i += m.end()
            continue
        m = re.match(r"\*\*([^*]+)\*\*", s[i:])
        if m:
            parts.append({"type": "text", "text": {"content": m.group(1)}, "annotations": {"bold": True}})
            i += m.end()
            continue
        m = re.match(r"\*([^*]+)\*", s[i:])
        if m:
            parts.append({"type": "text", "text": {"content": m.group(1)}, "annotations": {"italic": True}})
            i += m.end()
            continue
        m = re.match(r"[^\[\*]+|[\[\*]", s[i:])
        if m:
            parts.append({"type": "text", "text": {"content": m.group(0)}})
            i += m.end()
            continue
        i += 1
    return parts if parts else [{"type": "text", "text": {"content": text}}]


def slugify(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s가-힣-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s or datetime.now().strftime("market-%Y%m%d")


def create_notion_page(draft):
    slug_raw = draft.get("slug") or draft["title"]
    slug = slugify(slug_raw)
    # 같은 날 중복 생성 방지: 날짜 접두
    today_tag = date.today().strftime("%y%m%d")
    if not slug.startswith(today_tag):
        slug = f"market-{today_tag}-{slug}"

    props = {
        "제목": {"title": [{"text": {"content": draft["title"]}}]},
        "카테고리": {"select": {"name": "시장관찰"}},
        "슬러그": {"rich_text": [{"text": {"content": slug}}]},
        "게시일": {"date": {"start": date.today().isoformat()}},
        "상태": {"select": {"name": "초안"}},
    }
    if draft.get("summary"):
        props["요약"] = {"rich_text": [{"text": {"content": draft["summary"][:500]}}]}

    children = md_to_blocks(draft.get("body", ""))
    payload = {
        "parent": {"database_id": DB_ID},
        "properties": props,
        "children": children[:100],
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload)
    if r.status_code != 200:
        log(f"페이지 생성 실패 ({draft['title']}): {r.status_code} {r.text[:300]}")
        return False
    log(f"시장판단 초안 생성: {draft['title']} ({slug})")
    return True


def main():
    if not ANTHROPIC_KEY:
        log("ANTHROPIC_API_KEY 없음 — 종료")
        sys.exit(1)

    if not DATA_FILE.exists():
        log("data.json 없음 — 종료")
        sys.exit(0)

    data = json.loads(DATA_FILE.read_text())

    # 오늘 장 마감 데이터가 아니면 스킵 (주말·공휴일에 LaunchAgent가 실행돼도 안전)
    today = date.today().isoformat()
    if data.get("date") != today:
        log(f"data.json 기준일={data.get('date')}, 오늘({today}) 아님 — 스킵")
        return

    draft = generate_draft(data)
    create_notion_page(draft)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적 오류: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
