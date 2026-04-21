#!/usr/bin/env python3
"""
Notion "만또 블로그 글" DB → ~/블로그/*.md 싱크

- 상태가 "게시대기" 인 페이지를 마크다운으로 변환
- ~/블로그/에 파일 저장 (publish_blog.py가 이후 처리)
- 게시 후 Notion 상태를 "게시완료"로 업데이트

15분마다 LaunchAgent가 돌립니다.
"""

import json
import re
import sys
import unicodedata
from datetime import datetime, date
from pathlib import Path

import requests

HOME = Path.home()
_cfg = json.loads((HOME / ".config/manddo/notion.json").read_text())
TOKEN = _cfg["token"]
DB_ID = _cfg["blog_db_id"]
BLOG_SRC = HOME / "블로그"
LOG_FILE = HOME / "manddo-site" / "scripts" / "sync_notion_blog.log"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
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


def slugify(s):
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s가-힣-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s or datetime.now().strftime("post-%Y%m%d%H%M")


def rich_text_plain(rts):
    return "".join(rt.get("plain_text", "") for rt in rts)


def rich_text_md(rts):
    """Notion rich_text → markdown 변환 (bold/italic/code/link)"""
    out = []
    for rt in rts:
        t = rt.get("plain_text", "")
        ann = rt.get("annotations", {})
        href = rt.get("href")
        if ann.get("code"):
            t = f"`{t}`"
        if ann.get("bold"):
            t = f"**{t}**"
        if ann.get("italic"):
            t = f"*{t}*"
        if href:
            t = f"[{t}]({href})"
        out.append(t)
    return "".join(out)


def block_to_md(block, depth=0):
    bt = block.get("type")
    b = block.get(bt, {})
    indent = "  " * depth
    if bt == "paragraph":
        return rich_text_md(b.get("rich_text", []))
    if bt == "heading_1":
        return "## " + rich_text_md(b.get("rich_text", []))
    if bt == "heading_2":
        return "## " + rich_text_md(b.get("rich_text", []))
    if bt == "heading_3":
        return "### " + rich_text_md(b.get("rich_text", []))
    if bt == "bulleted_list_item":
        return f"{indent}- " + rich_text_md(b.get("rich_text", []))
    if bt == "numbered_list_item":
        return f"{indent}1. " + rich_text_md(b.get("rich_text", []))
    if bt == "quote":
        return "> " + rich_text_md(b.get("rich_text", []))
    if bt == "code":
        lang = b.get("language", "")
        code = rich_text_plain(b.get("rich_text", []))
        return f"```{lang}\n{code}\n```"
    if bt == "divider":
        return "---"
    if bt == "image":
        url = b.get("file", {}).get("url") or b.get("external", {}).get("url", "")
        caption = rich_text_plain(b.get("caption", []))
        return f"![{caption}]({url})"
    if bt == "to_do":
        checked = "x" if b.get("checked") else " "
        return f"{indent}- [{checked}] " + rich_text_md(b.get("rich_text", []))
    # 미지원 블록은 무시
    return ""


def fetch_page_blocks(page_id, depth=0):
    """페이지의 블록을 재귀적으로 가져와 md 라인 배열로 반환"""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    lines = []
    while url:
        r = requests.get(url, headers=HEADERS)
        if r.status_code != 200:
            log(f"blocks fetch 실패: {r.status_code} {r.text[:200]}")
            return lines
        data = r.json()
        for blk in data.get("results", []):
            md = block_to_md(blk, depth)
            if md:
                lines.append(md)
            if blk.get("has_children"):
                # 리스트 아이템의 자식 처리
                child_lines = fetch_page_blocks(blk["id"], depth + 1)
                lines.extend(child_lines)
        if data.get("has_more"):
            url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100&start_cursor={data['next_cursor']}"
        else:
            url = None
    return lines


def update_page_status(page_id, status, slug_value=None):
    props = {"상태": {"select": {"name": status}}}
    if slug_value:
        props["슬러그"] = {"rich_text": [{"text": {"content": slug_value}}]}
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"properties": props},
    )
    if r.status_code != 200:
        log(f"상태 업데이트 실패: {r.status_code} {r.text[:200]}")


def query_pending():
    body = {
        "filter": {
            "property": "상태",
            "select": {"equals": "게시대기"},
        }
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{DB_ID}/query",
        headers=HEADERS,
        json=body,
    )
    if r.status_code != 200:
        log(f"DB 쿼리 실패: {r.status_code} {r.text[:200]}")
        return []
    return r.json().get("results", [])


def extract_prop(page, name, kind):
    p = page.get("properties", {}).get(name, {})
    if kind == "title":
        return rich_text_plain(p.get("title", []))
    if kind == "rich_text":
        return rich_text_plain(p.get("rich_text", []))
    if kind == "select":
        sel = p.get("select")
        return sel.get("name") if sel else ""
    if kind == "date":
        d = p.get("date")
        return d.get("start") if d else ""
    return ""


def main():
    BLOG_SRC.mkdir(exist_ok=True)
    pending = query_pending()
    if not pending:
        return
    log(f"게시대기 {len(pending)}건 감지")
    for page in pending:
        try:
            title = extract_prop(page, "제목", "title") or "제목없음"
            category = extract_prop(page, "카테고리", "select") or "투자일기"
            slug = extract_prop(page, "슬러그", "rich_text") or slugify(title)
            date_str = extract_prop(page, "게시일", "date") or date.today().isoformat()
            summary = extract_prop(page, "요약", "rich_text")
            if "T" in date_str:
                date_str = date_str.split("T")[0]

            lines = fetch_page_blocks(page["id"])
            body = "\n\n".join(lines)

            fm = [
                "---",
                f"title: {title}",
                f"category: {category}",
                f"date: {date_str}",
                f"slug: {slug}",
            ]
            if summary:
                fm.append(f"description: {summary}")
            fm.append("---")
            md_text = "\n".join(fm) + "\n\n" + body + "\n"

            # 파일명은 slug 기반
            fname = f"{slug}.md"
            (BLOG_SRC / fname).write_text(md_text)
            log(f"저장: {fname} (제목: {title})")
            update_page_status(page["id"], "게시완료", slug)
        except Exception as e:
            log(f"처리 실패: {e}")
            import traceback
            log(traceback.format_exc())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적 오류: {e}")
        sys.exit(1)
