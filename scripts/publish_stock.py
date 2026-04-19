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

BACK_BUTTON_MARK = "<!-- manddo-back-button -->"
BACK_BUTTON_HTML = (
    '\n' + BACK_BUTTON_MARK + '\n'
    '<style>#__mdback{position:fixed;top:12px;left:12px;z-index:9999;'
    'display:inline-flex;align-items:center;gap:4px;'
    'background:rgba(255,255,255,.95);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);'
    'border:1px solid #d3d1c7;border-radius:10px;padding:9px 14px;'
    'font-size:13px;font-weight:700;color:#2c2c2a;text-decoration:none;'
    'box-shadow:0 2px 10px rgba(0,0,0,.08);cursor:pointer;'
    'font-family:-apple-system,BlinkMacSystemFont,"Noto Sans KR",sans-serif}'
    '#__mdback:hover{background:#fff;border-color:#888780;transform:translateY(-1px)}'
    '#__mdback:active{transform:translateY(0)}'
    '</style>'
    '<a id="__mdback" href="/stock/" '
    'onclick="if(document.referrer&&history.length>1){event.preventDefault();history.back()}"'
    '>← 뒤로</a>\n'
)


def inject_back_button(html_text):
    if BACK_BUTTON_MARK in html_text:
        return html_text
    m = re.search(r"<body[^>]*>", html_text, re.IGNORECASE)
    if not m:
        return html_text
    idx = m.end()
    return html_text[:idx] + BACK_BUTTON_HTML + html_text[idx:]

# 종목명(한글/영문) → SEO 슬러그
NAME_TO_SLUG = {
    "삼성전자": "samsung-electronics",
    "삼성SDI": "samsung-sdi",
    "삼성물산": "samsung-cnt",
    "삼성바이오로직스": "samsung-biologics",
    "SK하이닉스": "sk-hynix",
    "LG화학": "lg-chem",
    "LG에너지솔루션": "lg-energy-solution",
    "현대차": "hyundai-motor",
    "현대모비스": "hyundai-mobis",
    "기아": "kia",
    "POSCO홀딩스": "posco-holdings",
    "네이버": "naver",
    "카카오": "kakao",
    "셀트리온": "celltrion",
    "에코프로비엠": "ecopro-bm",
    "KB금융": "kb-financial",
    "신한지주": "shinhan-financial",
    "하나금융지주": "hana-financial",
    "우리금융지주": "woori-financial",
    "알테오젠": "alteogen",
    "엔씨소프트": "ncsoft",
    "아난티": "ananti",
    "한화솔루션": "hanwha-solutions",
    "OCI홀딩스": "oci-holdings",
    "SDN": "sdn",
    "TYM": "tym",
}

# 종목코드 → 시장(코스피/코스닥) 간이 매핑. 없으면 코스피로 추정.
KOSDAQ_CODES = {
    "196170",  # 알테오젠
    "025980",  # 아난티
    "099220",  # SDN
    "247540",  # 에코프로비엠
}


def filename_fallback(html_path):
    """파일명 패턴 '종목명_종목코드_analysis.html'에서 메타 추출."""
    stem = html_path.stem  # ex: '삼성전자_005930_analysis'
    parts = stem.split("_")
    name = code = None
    for p in parts:
        if p.isdigit() and len(p) == 6:
            code = p
            break
    name_parts = []
    for p in parts:
        if p == "analysis" or (p.isdigit() and len(p) == 6):
            break
        name_parts.append(p)
    if name_parts:
        name = "_".join(name_parts) if len(name_parts) > 1 else name_parts[0]
        # 영문 슬러그 형식 (samsung_sdi)이면 그대로 사용 → 사람이 읽을 수 있게 변환은 매핑에서
    return name, code


def extract_title(html_text):
    m = re.search(r"<title>([^<]+)</title>", html_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return []


def save_manifest(items):
    MANIFEST.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n")


def parse_meta(html_path):
    """메타 태그 우선, 없으면 파일명·title에서 자동 추출."""
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    found = {key.lower(): value for key, value in META_RE.findall(text)}

    # 파일명·title 폴백
    fname_name, fname_code = filename_fallback(html_path)
    title = extract_title(text)  # ex: "삼성전자 세력·차트 분석"

    name = found.get("name") or fname_name
    code = found.get("code") or fname_code
    if not name or not code:
        return None, ["name 또는 code (파일명 패턴 종목명_종목코드_analysis.html 권장)"]

    # 슬러그: 매핑 테이블 → 메타 → 파일명(영문) → 코드
    slug = found.get("slug") or NAME_TO_SLUG.get(name)
    if not slug:
        if re.fullmatch(r"[a-zA-Z0-9_-]+", fname_name or ""):
            slug = fname_name.lower().replace("_", "-")
        else:
            slug = code  # 한글명 매핑 미등록 → 코드를 슬러그로 사용

    market = found.get("market") or ("코스닥" if code in KOSDAQ_CODES else "코스피")
    desc = found.get("desc") or (title.replace(" 세력·차트 분석", "").strip() + " 세력 흐름·차트 구조·수급 종합 분석.")
    tags = [t.strip() for t in found.get("tags", "").split(",") if t.strip()]

    return {
        "slug": slug,
        "name": name,
        "code": code,
        "market": market,
        "tags": tags,
        "desc": desc,
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
    inject_only = "--inject-back" in sys.argv

    if inject_only:
        changed = 0
        for html in sorted(STOCK_DIR.glob("*.html")):
            if html.name == "index.html":
                continue
            text = html.read_text(encoding="utf-8", errors="ignore")
            new_text = inject_back_button(text)
            if new_text != text:
                html.write_text(new_text, encoding="utf-8")
                changed += 1
                print(f"  ✓ {html.name}")
        print(f"\n뒤로가기 버튼 주입 완료: {changed}건")
        return

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
            src_text = src.read_text(encoding="utf-8", errors="ignore")
            dest.write_text(inject_back_button(src_text), encoding="utf-8")
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
