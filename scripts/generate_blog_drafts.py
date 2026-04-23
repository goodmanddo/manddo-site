#!/usr/bin/env python3
"""
블로그 초안 자동 생성 → Notion DB에 상태='초안'으로 적재

월/수/금 오전 9시 LaunchAgent가 실행. 사용자는 Notion에서 초안을 열어
자기 생각을 덧붙이고 상태를 '게시대기'로 바꿔 게시.
"""

import json
import os
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
LOG_FILE = SITE_ROOT / "scripts" / "generate_blog_drafts.log"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

CATEGORIES = ["투자일기", "시장관찰", "책리뷰", "에세이"]


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


def fetch_existing_titles():
    """Notion DB + 이미 게시된 blog/*.html 에서 제목/슬러그를 모아 중복 방지용 셋 반환"""
    titles = set()
    slugs = set()

    # Notion: 모든 상태 페이지
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
            s = "".join(rt.get("plain_text", "") for rt in p["properties"].get("슬러그", {}).get("rich_text", []))
            if t:
                titles.add(t)
            if s:
                slugs.add(s)
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]

    # 이미 게시된 HTML
    blog_dir = SITE_ROOT / "blog"
    if blog_dir.exists():
        for f in blog_dir.glob("*.html"):
            if f.name in ("index.html", "_template.html"):
                continue
            slugs.add(f.stem)

    return titles, slugs


def site_inventory():
    """사이트 재료 목록 (도구·가이드·패턴·주요 리포트)"""
    inv = {
        "tools": [],
        "learn_guides": [],
        "patterns": [],
        "featured_stocks": [],
    }
    for f in (SITE_ROOT / "tools").glob("*.html"):
        if f.stem != "index":
            inv["tools"].append(f.stem)
    for f in (SITE_ROOT / "learn/guide").glob("*.html"):
        if f.stem != "index":
            inv["learn_guides"].append(f.stem)
    for f in (SITE_ROOT / "learn/pattern").glob("*.html"):
        if f.stem != "index":
            inv["patterns"].append(f.stem)
    # 대표 종목 몇 개만 (너무 많으면 프롬프트 비대)
    featured = [
        "samsung-sdi", "samsung-electronics", "sk-hynix", "lg-chem",
        "naver", "kakao", "hyundai-motor", "celltrion", "ncsoft",
    ]
    for name in featured:
        if (SITE_ROOT / f"stock/{name}.html").exists():
            inv["featured_stocks"].append(name)
    return inv


REFERENCE_SAMPLE = """
### 샘플 (이 톤으로 작성)

---
title: FIRE 계산기에 내 숫자 넣어본 날
category: 투자일기
slug: fire-calculator-my-numbers
description: Lean·Regular·Fat FIRE를 직접 돌려보고 깨달은 것. 수익률보다 지출이 먼저였다.
---

FIRE(경제적 자유) 얘기가 유튜브에서 흔해진 지 꽤 됐다. "월 500만원 쓰려면 15억"이라는 식의 공식은 외웠지만, 막상 **내 숫자**를 대입해본 적은 없었다.

그래서 [만또 FIRE 계산기](/tools/fire.html)에 현재 자산, 월 저축, 기대 수익률을 넣어봤다.

결과가 좀 당혹스러웠다. Lean FIRE는 생각보다 가까웠는데, Fat FIRE는 정년보다 늦었다. 문제는 저축액이 아니라 **지출 수준**이었다.

## 수익률보다 지출이 먼저

투자 커뮤니티에서 대부분의 논쟁은 "어떻게 수익률을 높일까"에 머문다. 그런데 수익률 8% → 10%는 노력과 운이 다 맞아야 가능한 일이고, 지출 컨트롤은 **오늘 당장** 할 수 있는 일이다.

## 계산기의 역할

계산기는 결론을 주지 않는다. 다만 지금의 선택이 10년 뒤 어디로 연결되는지는 보여준다. 그거면 충분하다.
"""


def build_prompt(existing_titles, existing_slugs, inv):
    banned_titles = ", ".join(sorted(existing_titles)) or "(없음)"
    banned_slugs = ", ".join(sorted(existing_slugs)) or "(없음)"
    return f"""manddo.kr는 한국 주식 분석·계산기·투자 학습 사이트입니다.
블로그 섹션에 올릴 **블로그 초안 3편**을 작성해 주세요. 초안은 사람이 나중에
자기 경험을 덧붙이는 용도입니다. 너무 완결된 글로 쓰지 말고, 1인칭 관점에서
**생각·의문·숫자**를 던져놓아 독자(=본인 사용자)가 자신의 감상을 덧붙일 여지를
남겨두세요.

## 톤·형식 가이드
- 한국어, 1인칭 ("나", "내가")
- 본문 분량: 각 600~900자 (한글 기준)
- 마크다운. 소제목 (##) 2~3개
- 내부 링크 최소 1개 (예: [배당 가이드](/learn/guide/dividend-compound.html))
- 숫자·구체적 사례를 반드시 하나 이상 포함
- "~~입니다" 보다 "~~다" (평어) 권장, 블로그 일기 톤

## 카테고리 (하나 골라서 사용)
{", ".join(CATEGORIES)}

## 사이트 재료 (내부 링크용)
- 도구 (/tools/{{slug}}.html): {", ".join(inv["tools"])}
- 학습 가이드 (/learn/guide/{{slug}}.html): {", ".join(inv["learn_guides"])}
- 차트 패턴 (/learn/pattern/{{slug}}.html): {", ".join(inv["patterns"])}
- 주요 종목 리포트 (/stock/{{slug}}.html): {", ".join(inv["featured_stocks"])}

## 중복 금지
다음 제목·슬러그는 이미 사용됨. 완전히 다른 주제로 작성:
- 기존 제목: {banned_titles}
- 기존 슬러그: {banned_slugs}

## 출력 형식
**JSON 배열만** 반환. 3개 요소, 각 요소는:
{{
  "title": "제목",
  "category": "투자일기|시장관찰|책리뷰|에세이 중 하나",
  "slug": "url-safe-slug (영문 또는 한글-하이픈)",
  "summary": "SEO용 1문장 요약 (100자 이내)",
  "body": "마크다운 본문"
}}

{REFERENCE_SAMPLE}

JSON 외 다른 텍스트·코드블럭 금지. 오직 `[` 로 시작해 `]` 로 끝나는 JSON."""


def generate_drafts(existing_titles, existing_slugs, inv):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = build_prompt(existing_titles, existing_slugs, inv)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    # JSON 부분만 추출
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        raise RuntimeError(f"JSON 응답 파싱 실패: {text[:200]}")
    drafts = json.loads(m.group(0))
    return drafts


def md_to_blocks(body):
    """매우 얕은 마크다운 → Notion block 변환 (heading, paragraph, bullet만)"""
    blocks = []
    for line in body.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            txt = line[4:]
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": txt}}]}
            })
        elif line.startswith("## "):
            txt = line[3:]
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": txt}}]}
            })
        elif line.startswith("- "):
            txt = line[2:]
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich_from_md(txt)}
            })
        elif line.startswith("> "):
            txt = line[2:]
            blocks.append({
                "object": "block", "type": "quote",
                "quote": {"rich_text": rich_from_md(txt)}
            })
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": rich_from_md(line)}
            })
    return blocks


def rich_from_md(text):
    """**bold**, *italic*, [link](url) 를 Notion rich_text 로 변환 (얕은 파서)"""
    parts = []
    # 토큰화: 링크 → 볼드 → 이탤릭 순
    pattern = re.compile(r"(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(\*[^*]+\*)|([^\[\*]+|[\[\*])")
    i = 0
    s = text
    while i < len(s):
        # 링크
        m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", s[i:])
        if m:
            url = m.group(2)
            if url.startswith("/"):
                url = "https://manddo.kr" + url
            parts.append({
                "type": "text",
                "text": {"content": m.group(1), "link": {"url": url}},
            })
            i += m.end()
            continue
        # 볼드
        m = re.match(r"\*\*([^*]+)\*\*", s[i:])
        if m:
            parts.append({
                "type": "text",
                "text": {"content": m.group(1)},
                "annotations": {"bold": True},
            })
            i += m.end()
            continue
        # 이탤릭
        m = re.match(r"\*([^*]+)\*", s[i:])
        if m:
            parts.append({
                "type": "text",
                "text": {"content": m.group(1)},
                "annotations": {"italic": True},
            })
            i += m.end()
            continue
        # 일반 텍스트 (다음 특수문자 전까지)
        m = re.match(r"[^\[\*]+|[\[\*]", s[i:])
        if m:
            parts.append({"type": "text", "text": {"content": m.group(0)}})
            i += m.end()
            continue
        i += 1
    return parts if parts else [{"type": "text", "text": {"content": text}}]


def create_notion_page(draft):
    slug = slugify(draft.get("slug") or draft["title"])
    props = {
        "제목": {"title": [{"text": {"content": draft["title"]}}]},
        "카테고리": {"select": {"name": draft.get("category", "투자일기")}},
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
        "children": children[:100],  # Notion 100블록 제한
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload)
    if r.status_code != 200:
        log(f"페이지 생성 실패 ({draft['title']}): {r.status_code} {r.text[:300]}")
        return False
    log(f"초안 생성: {draft['title']} ({slug})")
    return True


def main():
    if not ANTHROPIC_KEY:
        log("ANTHROPIC_API_KEY 없음 — 종료")
        sys.exit(1)

    titles, slugs = fetch_existing_titles()
    log(f"기존 제목 {len(titles)}개, 기존 슬러그 {len(slugs)}개 수집")

    inv = site_inventory()
    log(f"재료: tools={len(inv['tools'])}, guides={len(inv['learn_guides'])}, patterns={len(inv['patterns'])}")

    drafts = generate_drafts(titles, slugs, inv)
    log(f"초안 {len(drafts)}편 생성됨")

    for d in drafts:
        try:
            create_notion_page(d)
        except Exception as e:
            log(f"페이지 생성 예외 ({d.get('title', '?')}): {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적 오류: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
