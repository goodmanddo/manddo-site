#!/usr/bin/env python3
"""
AdSense '가치가 별로 없는 콘텐츠' 대응:
- 비핵심 stock/*.html 페이지에 noindex,follow 메타 추가
- mind/today-fortune.html, mind/investor-type.html에 noindex,follow 추가
- sitemap.xml에서 noindex 처리된 URL 제거

핵심 종목 15개는 색인 유지. 나머지는 사용자 직접 링크/내부 링크로는 접근 가능하지만
검색엔진 색인에서는 빠짐.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOCK_DIR = ROOT / "stock"
ETF_DIR = ROOT / "etf"

CORE_STOCKS = {
    "samsung-electronics", "sk-hynix", "samsung-sdi", "alteogen", "ncsoft",
    "ananti", "hanwha-solutions", "oci-holdings", "sdn", "tym",
    "hyundai-motor", "naver", "kakao", "lg-chem", "lg-energy-solution",
}

EXTRA_NOINDEX = [
    ROOT / "mind" / "today-fortune.html",
    ROOT / "mind" / "investor-type.html",
]

NOINDEX_TAG = '<meta name="robots" content="noindex,follow">'


def insert_noindex(html: str) -> tuple[str, bool]:
    if 'name="robots"' in html:
        if 'noindex' in html.lower():
            return html, False
        # robots 메타 있는데 noindex 아님 → 교체
        new = re.sub(
            r'<meta\s+name="robots"[^>]*>',
            NOINDEX_TAG, html, count=1, flags=re.IGNORECASE,
        )
        return new, new != html
    # viewport 다음 줄에 삽입
    m = re.search(r'(<meta\s+name="viewport"[^>]*>)', html, flags=re.IGNORECASE)
    if not m:
        return html, False
    insert_at = m.end()
    new = html[:insert_at] + "\n" + NOINDEX_TAG + html[insert_at:]
    return new, True


def process_file(path: Path) -> bool:
    if not path.exists():
        return False
    src = path.read_text(encoding="utf-8")
    new, changed = insert_noindex(src)
    if changed:
        path.write_text(new, encoding="utf-8")
    return changed


def update_sitemap(blocked_urls: set[str]):
    sm = ROOT / "sitemap.xml"
    if not sm.exists():
        return 0
    # 네임스페이스 보존을 위해 텍스트 처리
    src = sm.read_text(encoding="utf-8")
    removed = 0
    for url in blocked_urls:
        pattern = re.compile(
            r"\s*<url>\s*<loc>" + re.escape(url) + r"</loc>.*?</url>",
            re.DOTALL,
        )
        src, n = pattern.subn("", src)
        removed += n
    sm.write_text(src, encoding="utf-8")
    return removed


def main():
    # 1. stock 페이지 처리
    stock_changed = []
    blocked_urls = set()
    for f in sorted(STOCK_DIR.glob("*.html")):
        if f.stem == "index":
            continue
        if f.stem in CORE_STOCKS:
            continue
        if process_file(f):
            stock_changed.append(f.name)
        blocked_urls.add(f"https://manddo.kr/stock/{f.name}")

    # 1b. etf 페이지 전체 처리 (AdSense scaled-content 대응 — 템플릿 복제)
    etf_changed = []
    for f in sorted(ETF_DIR.glob("*.html")):
        if f.stem == "index":
            continue
        if process_file(f):
            etf_changed.append(f.name)
        blocked_urls.add(f"https://manddo.kr/etf/{f.name}")

    # 2. mind 부가 페이지 처리
    mind_changed = []
    for f in EXTRA_NOINDEX:
        rel = f.relative_to(ROOT).as_posix()
        if process_file(f):
            mind_changed.append(rel)
        blocked_urls.add(f"https://manddo.kr/{rel}")

    # 3. sitemap 정리
    removed = update_sitemap(blocked_urls)

    print(f"stock noindex 적용: {len(stock_changed)}건")
    print(f"etf noindex 적용: {len(etf_changed)}건")
    print(f"mind noindex 적용: {len(mind_changed)}건")
    print(f"sitemap에서 제외: {removed}건")


if __name__ == "__main__":
    main()
