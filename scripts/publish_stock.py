#!/usr/bin/env python3
"""
~/주식차트/완료/*.html → /stock/{slug}.html 발행 + index.html 카드/sitemap 자동 갱신.

사용:
    python3 scripts/publish_stock.py            # 신규만 발행
    python3 scripts/publish_stock.py --rebuild  # 매니페스트 기반으로 카드/사이트맵만 재생성

리포트 HTML <head>에 다음 메타 태그를 넣어두면 자동 인식:
    <meta name="stock-slug"   content="samsung-sdi">
    <meta name="stock-name"   content="삼성SDI">
    <meta name="stock-code"   content="006400">
    <meta name="stock-market" content="코스피 · 대형주">
    <meta name="stock-tags"   content="배터리,ESS,전고체">
    <meta name="stock-desc"   content="2025 창사 최악 실적 바닥 확인...">
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path.home() / "manddo-site"
STOCK_DIR = ROOT / "stock"
MANIFEST = STOCK_DIR / "_manifest.json"
INDEX_HTML = STOCK_DIR / "index.html"
SITEMAP = ROOT / "sitemap.xml"
SOURCE_DIR = Path.home() / "주식차트" / "완료"

META_RE = re.compile(
    r'<meta\s+name=["\']stock-([a-z]+)["\']\s+content=["\']([^"\']+)["\']\s*/?>',
    re.IGNORECASE,
)


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return []


def save_manifest(items):
    MANIFEST.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n")


def parse_meta(html_path):
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    found = {key.lower(): value for key, value in META_RE.findall(text)}
    required = ["slug", "name", "code", "market", "desc"]
    missing = [k for k in required if k not in found]
    if missing:
        return None, missing
    tags = [t.strip() for t in found.get("tags", "").split(",") if t.strip()]
    return {
        "slug": found["slug"],
        "name": found["name"],
        "code": found["code"],
        "market": found["market"],
        "tags": tags,
        "desc": found["desc"],
        "added": date.today().isoformat(),
    }, []


def render_card(item):
    tags_html = "".join(f'<span class="tag">#{t}</span>' for t in item.get("tags", []))
    data_tags = " ".join(item.get("tags", []))
    return (
        f'    <a href="/stock/{item["slug"]}.html" class="card" '
        f'data-name="{item["name"]} {item["slug"]}" '
        f'data-code="{item["code"]}" '
        f'data-tags="{data_tags}">\n'
        f'      <div class="cat">{item["market"]}</div>\n'
        f'      <div class="title">{item["name"]}</div>\n'
        f'      <div class="code">{item["code"]}</div>\n'
        f'      <div class="desc">{item["desc"]}</div>\n'
        f'      <div class="tags">{tags_html}</div>\n'
        f'    </a>'
    )


def replace_block(text, start_marker, end_marker, new_block):
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    return pattern.sub(start_marker + "\n" + new_block + "\n    " + end_marker, text, count=1)


def regenerate_index(items):
    items_sorted = sorted(items, key=lambda x: x.get("added", ""), reverse=True)
    cards = "\n".join(render_card(it) for it in items_sorted)
    text = INDEX_HTML.read_text(encoding="utf-8")
    new_text = replace_block(text, "<!-- STOCK_CARDS_START -->", "<!-- STOCK_CARDS_END -->", cards)
    INDEX_HTML.write_text(new_text, encoding="utf-8")


def regenerate_sitemap(items):
    today = date.today().isoformat()
    lines = [
        f'  <url><loc>https://manddo.kr/stock/{it["slug"]}.html</loc>'
        f'<lastmod>{it.get("added", today)}</lastmod><priority>0.8</priority></url>'
        for it in sorted(items, key=lambda x: x.get("added", ""), reverse=True)
    ]
    block = "\n".join(lines)
    text = SITEMAP.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- STOCK_URLS_START -->.*?<!-- STOCK_URLS_END -->",
        re.DOTALL,
    )
    replacement = "<!-- STOCK_URLS_START -->\n" + block + "\n  <!-- STOCK_URLS_END -->"
    new_text = pattern.sub(replacement, text, count=1)
    SITEMAP.write_text(new_text, encoding="utf-8")


def git_push(added_count):
    res = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    )
    if not res.stdout.strip():
        print("변경사항 없음 — 커밋 스킵")
        return
    subprocess.run(["git", "add", "stock/", "sitemap.xml"], cwd=ROOT, check=True)
    msg = f"종목 리포트 {added_count}건 자동 발행"
    res = subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"git commit 실패: {res.stderr}")
        return
    res = subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"git push 실패: {res.stderr}")
        return
    print(f"git push 완료 ({added_count}건)")


def main():
    rebuild_only = "--rebuild" in sys.argv
    manifest = load_manifest()
    existing_slugs = {it["slug"] for it in manifest}

    added = []
    skipped_no_meta = []
    skipped_dup = []

    if not rebuild_only and SOURCE_DIR.exists():
        for src in sorted(SOURCE_DIR.glob("*.html")):
            meta, missing = parse_meta(src)
            if meta is None:
                skipped_no_meta.append((src.name, missing))
                continue
            if meta["slug"] in existing_slugs:
                skipped_dup.append(src.name)
                continue
            dest = STOCK_DIR / f"{meta['slug']}.html"
            shutil.copy2(src, dest)
            manifest.append(meta)
            existing_slugs.add(meta["slug"])
            added.append(meta["slug"])
            print(f"  + {meta['slug']} ({meta['name']}) ← {src.name}")

    if added or rebuild_only:
        save_manifest(manifest)
        regenerate_index(manifest)
        regenerate_sitemap(manifest)

    print(f"\n발행: {len(added)}건 / 중복 스킵: {len(skipped_dup)}건 / 메타 누락: {len(skipped_no_meta)}건")
    if skipped_no_meta:
        print("\n메타 누락 파일:")
        for name, missing in skipped_no_meta:
            print(f"  - {name}: 누락 = {missing}")
    if skipped_dup:
        print("\n이미 발행된 파일 (스킵):")
        for name in skipped_dup:
            print(f"  - {name}")

    if added:
        git_push(len(added))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"오류: {e}")
        print(traceback.format_exc())
        sys.exit(1)
