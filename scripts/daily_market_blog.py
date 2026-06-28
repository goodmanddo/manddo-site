#!/usr/bin/env python3
"""
일일 시장 블로그 초안 생성 → Notion DB에 상태='초안'으로 적재

평일 20:00 LaunchAgent 실행. 그날 쌓인 실제 데이터(signal.json / data.json /
weekly_calendar.json)만 근거로 블로그 초안 1편을 작성한다. 세 가지 유형
(시장 이슈 정리 / 특정 종목 생각 / 투자 전략)을 날짜별로 자동 순환한다.

사용자는 Notion에서 초안을 열어 자기 의견을 덧붙이고 상태를 '게시대기'로
바꿔 게시한다. (즉시 게시하지 않음 — sync_notion_blog 흐름 유지)
"""

import json
import os
# 환경변수 없으면 키 파일에서 로드 (launchd는 .zprofile을 읽지 않음)
_kf = os.path.expanduser("~/stock_auto_trade/.anthropic_key")
if not os.environ.get("ANTHROPIC_API_KEY") and os.path.isfile(_kf):
    os.environ["ANTHROPIC_API_KEY"] = open(_kf).read().strip()
import re
import sys
import unicodedata
from datetime import datetime, date
from pathlib import Path

import anthropic
import requests

HOME = Path.home()
NOTION_CFG = json.loads((HOME / ".config/manddo/notion.json").read_text())
NOTION_TOKEN = NOTION_CFG["token"]
DB_ID = NOTION_CFG["blog_db_id"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

SITE_ROOT = HOME / "manddo-site"
AILOG = SITE_ROOT / "ai-log"
LOG_FILE = SITE_ROOT / "scripts" / "daily_market_blog.log"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# 날짜별 자동 순환하는 3가지 글 유형
ANGLES = [
    {
        "key": "market",
        "category": "시장관찰",
        "instruction": (
            "오늘의 미국·국내 시장 이슈를 정리하는 글. 제공된 미국 지수 등락·"
            "환율·섹터 흐름과, 그게 한국 시장/관심 종목에 어떤 의미인지 1인칭으로 "
            "풀어쓴다. 단순 뉴스 나열이 아니라 '나는 이걸 이렇게 읽었다'의 관점."
        ),
    },
    {
        "key": "stock",
        "category": "시장관찰",
        "instruction": (
            "오늘 영향받은 종목 또는 거래량 급증 종목 중 하나를 골라 생각을 적는 글. "
            "왜 움직였을지, 나라면 어떻게 볼지(관심/관망/경계)를 1인칭으로. 특정 종목 "
            "추천이 아니라 관찰 기록임을 톤으로 드러낸다."
        ),
    },
    {
        "key": "strategy",
        "category": "투자일기",
        "instruction": (
            "오늘 같은 시장 환경에서 떠오른 투자 전략·원칙에 대한 글. 그날 데이터에서 "
            "출발하되 '이런 장에서 나는 어떻게 행동할까' 같은 원칙·습관 이야기로 확장. "
            "추세추종/위험관리/현금비중 같은 주제 가능."
        ),
    },
]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def slugify(s):
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s가-힣-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s or datetime.now().strftime("post-%Y%m%d%H%M")


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def market_context():
    """그날 쌓인 실제 데이터만 모아 프롬프트용 사실 컨텍스트로 정리."""
    sig = load_json(AILOG / "signal.json")
    data = load_json(AILOG / "data.json")
    cal = load_json(AILOG / "weekly_calendar.json")

    ctx = {}

    us = sig.get("us_market") or {}
    if us:
        ctx["미국시장"] = {
            "요약": us.get("summary"),
            "지수": us.get("indices"),
            "원달러환율": us.get("fx_usd_krw"),
            "환율변동%": us.get("fx_change_pct"),
            "섹터포커스": us.get("sector_focus"),
            "영향받는_한국종목": us.get("impacted_kr_stocks"),
        }
    if sig.get("volume_surge"):
        ctx["거래량급증종목"] = sig["volume_surge"]
    if sig.get("ai_summary"):
        ctx["AI_시장요약"] = sig["ai_summary"]
    ctx["signal_날짜"] = sig.get("date")

    if data:
        ctx["AI실험_포트폴리오"] = {
            "누적수익률": data.get("cumulative_return"),
            "당일수익률": data.get("day_return"),
            "주간수익률": data.get("week_return"),
            "오늘_신규매수": data.get("new_buys"),
            "오늘_청산": data.get("exits"),
            "보유종목수": len(data.get("holdings", []) or []),
        }

    upcoming = []
    today = date.today().isoformat()
    for ev in (cal.get("events") or []):
        if ev.get("date", "") >= today:
            upcoming.append({k: ev.get(k) for k in ("date", "title", "category", "impact")})
    if upcoming:
        ctx["다가오는_시장이벤트"] = upcoming[:6]

    return ctx, sig.get("date")


def fetch_existing_titles():
    """중복 방지용: Notion DB의 모든 제목 + 게시된 blog/*.html 슬러그."""
    titles, slugs = set(), set()
    url = f"https://api.notion.com/v1/databases/{DB_ID}/query"
    body = {}
    while True:
        r = requests.post(url, headers=NOTION_HEADERS, json=body)
        if r.status_code != 200:
            log(f"Notion 쿼리 실패: {r.status_code}")
            break
        data = r.json()
        for p in data.get("results", []):
            t = "".join(rt.get("plain_text", "") for rt in p["properties"].get("제목", {}).get("title", []))
            if t:
                titles.add(t)
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    blog_dir = SITE_ROOT / "blog"
    if blog_dir.exists():
        for f in blog_dir.glob("*.html"):
            if f.name not in ("index.html", "_template.html"):
                slugs.add(f.stem)
    return titles, slugs


def build_prompt(angle, ctx, existing_titles):
    banned = ", ".join(sorted(existing_titles)) or "(없음)"
    facts = json.dumps(ctx, ensure_ascii=False, indent=2)
    return f"""manddo.kr는 한국 주식 분석·투자 학습 사이트다. 블로그에 올릴 **오늘의 글 1편**을 쓴다.
이 글은 사람(사이트 운영자)이 나중에 자기 의견을 덧붙여 게시하는 **초안**이다.
너무 완결하지 말고 1인칭으로 생각·의문을 던져, 운영자가 감상을 덧붙일 여지를 남겨라.

## 오늘 글의 유형
{angle['instruction']}

## 반드시 지킬 규칙
- **아래 '오늘의 사실 데이터'에 있는 숫자·사실만 사용**한다. 지수 등락률·환율·종목명 등을
  새로 지어내지 말 것. 데이터에 없는 구체 수치는 쓰지 않는다.
- 한국어, 1인칭("나", "내가"), 블로그 일기 톤("~다" 평어).
- 본문 700~1100자(한글 기준), 마크다운, 소제목(##) 2~3개.
- 내부 링크 0~1개만 (예: [오늘 AI의 기록](/ai-log/), [이번 주 시장 캘린더](/ai-log/#calendar-block)).
- 마지막은 운영자가 자기 의견을 이어쓸 수 있도록 **열린 질문/여백 한 문장**으로 끝낸다.
- 투자 권유가 아니라 관찰·기록임이 톤에서 드러나게.

## 오늘의 사실 데이터 (이것만 근거로)
{facts}

## 중복 금지 (이미 쓴 제목들, 겹치지 말 것)
{banned}

## 출력 형식
**JSON 객체 하나만** 반환:
{{
  "title": "제목 (오늘 날짜/이슈가 드러나되 낚시성 아님)",
  "slug": "url-safe-slug (영문 권장)",
  "summary": "SEO용 1문장 요약(100자 이내)",
  "body": "마크다운 본문"
}}
JSON 외 텍스트·코드블럭 금지. 오직 `{{` 로 시작해 `}}` 로 끝나는 JSON."""


def generate_draft(angle, ctx, existing_titles):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": build_prompt(angle, ctx, existing_titles)}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError(f"JSON 파싱 실패: {text[:200]}")
    return json.loads(m.group(0))


def rich_from_md(text):
    """**bold**, *italic*, [link](url) → Notion rich_text (얕은 파서)."""
    parts = []
    i, s = 0, text
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


def md_to_blocks(body):
    """얕은 마크다운 → Notion block (heading/paragraph/bullet/quote)."""
    blocks = []
    for line in body.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": rich_from_md(line[4:])}})
        elif line.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": rich_from_md(line[3:])}})
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


def create_notion_page(draft, category):
    slug = slugify(draft.get("slug") or draft["title"])
    props = {
        "제목": {"title": [{"text": {"content": draft["title"]}}]},
        "카테고리": {"select": {"name": category}},
        "슬러그": {"rich_text": [{"text": {"content": slug}}]},
        "게시일": {"date": {"start": date.today().isoformat()}},
        "상태": {"select": {"name": "초안"}},
    }
    if draft.get("summary"):
        props["요약"] = {"rich_text": [{"text": {"content": draft["summary"][:500]}}]}
    payload = {
        "parent": {"database_id": DB_ID},
        "properties": props,
        "children": md_to_blocks(draft.get("body", ""))[:100],
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload)
    if r.status_code != 200:
        log(f"페이지 생성 실패 ({draft['title']}): {r.status_code} {r.text[:300]}")
        return False
    log(f"초안 생성: [{category}] {draft['title']} ({slug})")
    return True


def main():
    if not ANTHROPIC_KEY:
        log("ANTHROPIC_API_KEY 없음 (환경변수·키파일 모두 없음) — 종료")
        sys.exit(1)

    ctx, sig_date = market_context()
    if not ctx.get("미국시장") and not ctx.get("거래량급증종목"):
        log("시장 데이터 없음 (signal.json 비어있음) — 스킵")
        return

    # 날짜로 3유형 순환
    angle = ANGLES[date.today().toordinal() % len(ANGLES)]
    log(f"오늘 유형: {angle['key']} ({angle['category']}) | signal 날짜={sig_date}")

    titles, _ = fetch_existing_titles()
    log(f"기존 제목 {len(titles)}개 수집 (중복 방지)")

    draft = generate_draft(angle, ctx, titles)
    create_notion_page(draft, angle["category"])


if __name__ == "__main__":
    main()
