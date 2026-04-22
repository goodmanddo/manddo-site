#!/usr/bin/env python3
"""
~/블로그/*.md → manddo.kr/blog/*.html 자동 게시

- 마크다운 파일 감지
- frontmatter 파싱 (title, category, date, slug, description)
- HTML 렌더링 (blog/_template.html 구조 재사용)
- blog/index.html 목록 갱신
- sitemap.xml 갱신
- 게시 후 원본 md 파일을 ~/블로그/게시완료/로 이동
- git commit/push
"""

import json
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, date
from pathlib import Path

import markdown

HOME = Path.home()
BLOG_SRC = HOME / "블로그"
DONE_DIR = BLOG_SRC / "게시완료"
SITE = HOME / "manddo-site"
BLOG_DIR = SITE / "blog"
INDEX_FILE = BLOG_DIR / "index.html"
HOME_INDEX_FILE = SITE / "index.html"
SITEMAP_FILE = SITE / "sitemap.xml"
HOME_BLOG_START = "<!-- HOME_BLOG_START -->"
HOME_BLOG_END = "<!-- HOME_BLOG_END -->"
LOG_FILE = SITE / "scripts" / "publish_blog.log"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


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


def parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta_raw, body = m.group(1), m.group(2)
    meta = {}
    for line in meta_raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body


def format_korean_date(d):
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{d.year}년 {d.month}월 {d.day}일"


def reading_time(text):
    # 한글 기준 400자/분
    chars = len(re.sub(r"\s", "", text))
    mins = max(1, round(chars / 400))
    return mins


POST_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | 만또 블로그</title>
<meta name="description" content="{description}">
<meta name="keywords" content="{keywords}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="article">
<meta property="og:url" content="https://manddo.kr/blog/{slug}.html">
<link rel="canonical" href="https://manddo.kr/blog/{slug}.html">
<link rel="stylesheet" href="/css/main.css">
<style>
.post-head{{padding:32px 0 20px;border-bottom:1px solid #eef0f3;margin-bottom:28px}}
.post-head .cat{{font-size:12px;color:#3182F6;font-weight:700;letter-spacing:0.04em;margin-bottom:10px}}
.post-head h1{{font-size:30px;font-weight:800;letter-spacing:-0.035em;color:#191f28;line-height:1.3;margin-bottom:12px}}
.post-head .meta{{font-size:13px;color:#8b95a1}}
.post-body{{font-size:15.5px;line-height:1.85;color:#333d4b;max-width:720px}}
.post-body h2{{font-size:21px;font-weight:800;letter-spacing:-0.03em;color:#191f28;margin:36px 0 14px;padding-top:8px}}
.post-body h3{{font-size:17px;font-weight:700;color:#191f28;margin:24px 0 10px}}
.post-body p{{margin:0 0 16px}}
.post-body ul,.post-body ol{{margin:0 0 16px 20px;padding:0}}
.post-body li{{margin-bottom:6px}}
.post-body blockquote{{border-left:3px solid #3182F6;padding:4px 14px;margin:16px 0;color:#4e5968;background:#F7F9FC;border-radius:4px}}
.post-body img{{max-width:100%;border-radius:10px;margin:16px 0;display:block}}
.post-body b,.post-body strong{{color:#191f28}}
.post-body code{{background:#F2F4F6;padding:2px 6px;border-radius:4px;font-size:14px}}
.post-body pre{{background:#F2F4F6;padding:14px 16px;border-radius:8px;overflow-x:auto;font-size:13.5px;line-height:1.6;margin:16px 0}}
.post-body pre code{{background:none;padding:0}}
.post-body a{{color:#1B64DA}}
.post-body table{{border-collapse:collapse;margin:16px 0;font-size:14px}}
.post-body th,.post-body td{{border:1px solid #e5e8eb;padding:8px 12px}}
.post-body th{{background:#F7F9FC;font-weight:700}}
@media(max-width:640px){{.post-head h1{{font-size:24px}}.post-body{{font-size:15px}}}}
</style>
</head>
<body>

<header class="site-header">
  <div class="site-header-inner">
    <a href="/" class="logo">만또<span>.kr</span></a>
    <nav class="nav">
      <a href="/">홈</a>
      <a href="/ai-log/">오늘 AI의 선택</a>
      <a href="/stock/">차트분석 리포트</a>
      <a href="/learn/">학습</a>
      <a href="/blog/" class="active">블로그</a>
      <a href="/tools/">머니 툴</a>
      <a href="/mind/">마인드 랩</a>
    </nav>
  </div>
</header>

<main class="page">

  <div class="breadcrumb">
    <a href="/blog/">블로그</a><span class="sep">/</span>{title}
  </div>

  <header class="post-head">
    <div class="cat">{category}</div>
    <h1>{title}</h1>
    <div class="meta">{date_kor} · 읽는 시간 약 {read_min}분</div>
  </header>

  <article class="post-body">
{body_html}
  </article>

  <div class="disclaimer" style="margin-top:40px">
    <b>⚠ 투자 유의사항</b><br>
    본 글은 개인의 투자 기록·의견이며, 특정 종목의 매수·매도 권유가 아닙니다. 모든 투자 판단과 그 결과는 본인의 책임입니다.
  </div>

</main>

<footer class="site-footer">
  <div class="site-footer-inner">
    <a href="/about.html">소개</a>·
    <a href="/privacy.html">개인정보처리방침</a>·
    <a href="/terms.html">이용약관</a>·
    <a href="/contact.html">문의</a>
    <div class="copy">© 2026 만또 (manddo.kr) · 본 사이트는 투자 자문업자가 아닙니다</div>
  </div>
</footer>

</body>
</html>
"""


def render_post(meta, body_md, slug):
    title = meta.get("title", "제목 없음")
    category = meta.get("category", "투자일기")
    description = meta.get("description") or re.sub(r"\s+", " ", body_md.strip())[:120]
    keywords = meta.get("keywords", "만또블로그,투자일기")
    date_str = meta.get("date") or date.today().isoformat()
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        d = date.today()
    body_html = markdown.markdown(
        body_md,
        extensions=["extra", "sane_lists", "nl2br"],
    )
    body_html = "    " + body_html.replace("\n", "\n    ")
    return POST_TEMPLATE.format(
        title=title,
        category=category,
        description=description.replace('"', "'"),
        keywords=keywords,
        slug=slug,
        date_kor=format_korean_date(d),
        read_min=reading_time(body_md),
        body_html=body_html,
    ), d


def extract_excerpt(body_md, limit=80):
    for line in body_md.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        line = re.sub(r"[*_`#\[\]()]", "", line)
        if len(line) <= limit:
            return line
        return line[:limit].rstrip() + "…"
    return ""


def scan_all_posts():
    """blog/*.html 에서 모든 포스트 메타를 추출 (index/_template 제외)"""
    posts = []
    for f in BLOG_DIR.glob("*.html"):
        if f.name in ("index.html", "_template.html"):
            continue
        html = f.read_text()
        slug = f.stem
        title_m = re.search(r'<h1>([^<]+)</h1>', html)
        cat_m = re.search(r'<div class="cat">([^<]+)</div>', html)
        date_m = re.search(r'(\d{4})년\s*(\d+)월\s*(\d+)일', html)
        if not (title_m and date_m):
            continue
        try:
            d = date(int(date_m.group(1)), int(date_m.group(2)), int(date_m.group(3)))
        except ValueError:
            continue
        posts.append({
            "slug": slug,
            "title": title_m.group(1).strip(),
            "category": (cat_m.group(1).strip() if cat_m else "투자일기"),
            "date": d,
        })
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


BLOG_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>블로그 — 투자일기와 시장 관찰 | 만또 인사이트</title>
<meta name="description" content="만또의 투자일기. 오늘 본 종목, 시장에서 느낀 것, 책 리뷰. 거창한 분석보다 꾸준한 기록 중심.">
<meta name="keywords" content="투자블로그, 투자일기, 주식일기, 시장관찰, 만또">
<meta property="og:title" content="만또 블로그 — 투자일기와 시장 관찰">
<meta property="og:description" content="오늘 본 종목, 시장에서 느낀 것, 책 리뷰.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://manddo.kr/blog/">
<link rel="canonical" href="https://manddo.kr/blog/">
<link rel="stylesheet" href="/css/main.css">
<style>
.blog-hero{{padding:40px 0 24px;border-bottom:1px solid #eef0f3;margin-bottom:8px}}
.blog-hero h1{{font-size:28px;font-weight:800;letter-spacing:-0.035em;color:#191f28;margin-bottom:10px}}
.blog-hero p{{font-size:14.5px;color:#6b7684;line-height:1.7}}
.post-list{{display:flex;flex-direction:column}}
.post-item{{display:flex;align-items:center;gap:18px;padding:18px 4px;border-bottom:1px solid #eef0f3;text-decoration:none;color:inherit;transition:background .12s ease}}
.post-item:hover{{background:#F7F9FC}}
.post-item .cat{{font-size:11px;color:#3182F6;font-weight:700;letter-spacing:0.05em;min-width:76px;flex-shrink:0;text-transform:uppercase}}
.post-item .title{{font-size:16px;font-weight:700;color:#191f28;letter-spacing:-0.02em;flex:1;line-height:1.4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.post-item .date{{font-size:12.5px;color:#8b95a1;font-weight:500;flex-shrink:0;font-variant-numeric:tabular-nums}}
.empty{{background:#F7F9FC;border:1px dashed #cfd8e3;border-radius:12px;padding:28px;text-align:center;color:#8b95a1;font-size:14px}}
@media(max-width:640px){{
  .post-item{{gap:12px;padding:16px 4px;flex-wrap:wrap}}
  .post-item .cat{{min-width:auto;font-size:10.5px}}
  .post-item .title{{font-size:15px;white-space:normal;flex-basis:100%;order:2}}
  .post-item .date{{font-size:12px;order:3;margin-left:auto}}
}}
</style>
</head>
<body>

<header class="site-header">
  <div class="site-header-inner">
    <a href="/" class="logo">만또<span>.kr</span></a>
    <nav class="nav">
      <a href="/">홈</a>
      <a href="/ai-log/">오늘 AI의 선택</a>
      <a href="/stock/">차트분석 리포트</a>
      <a href="/learn/">학습</a>
      <a href="/blog/" class="active">블로그</a>
      <a href="/tools/">머니 툴</a>
      <a href="/mind/">마인드 랩</a>
    </nav>
  </div>
</header>

<main class="page">

  <section class="blog-hero">
    <h1>📝 만또 블로그</h1>
    <p>거창한 분석보다 꾸준한 기록. 오늘 본 종목, 시장에서 느낀 것, 읽은 책의 한 문장.</p>
  </section>

  <div class="post-list">
{items}
  </div>

</main>

<footer class="site-footer">
  <div class="site-footer-inner">
    <a href="/about.html">소개</a>·
    <a href="/privacy.html">개인정보처리방침</a>·
    <a href="/terms.html">이용약관</a>·
    <a href="/contact.html">문의</a>
    <div class="copy">© 2026 만또 (manddo.kr) · 본 사이트는 투자 자문업자가 아닙니다</div>
  </div>
</footer>

</body>
</html>
"""


def rebuild_blog_index():
    """blog/*.html을 스캔해 최신순으로 인덱스 페이지 전체 재생성."""
    posts = scan_all_posts()
    if not posts:
        log("재생성할 포스트 없음")
        return
    items = []
    for p in posts:
        d = p["date"]
        items.append(
            f'    <a href="/blog/{p["slug"]}.html" class="post-item">'
            f'<div class="cat">{p["category"]}</div>'
            f'<div class="title">{p["title"]}</div>'
            f'<div class="date">{d.year}.{d.month:02d}.{d.day:02d}</div>'
            f'</a>'
        )
    html = BLOG_INDEX_TEMPLATE.format(items="\n".join(items))
    INDEX_FILE.write_text(html)
    log(f"blog/index.html 재생성 ({len(posts)}편, 최신순)")


def update_blog_index(new_posts):
    # 레거시 호환용 — 전체 재생성으로 위임
    rebuild_blog_index()


def update_home_blog_section():
    """홈 index.html의 블로그 섹션을 최신 2편으로 갱신."""
    if not HOME_INDEX_FILE.exists():
        return
    posts = scan_all_posts()[:2]
    if not posts:
        return
    items = []
    for p in posts:
        d = p["date"]
        items.append(
            f'      <a href="/blog/{p["slug"]}.html" class="post-item">'
            f'<div class="cat">{p["category"]}</div>'
            f'<div class="title">{p["title"]}</div>'
            f'<div class="date">{d.year}.{d.month:02d}.{d.day:02d}</div>'
            f'</a>'
        )
    html = HOME_INDEX_FILE.read_text()
    pattern = re.compile(
        re.escape(HOME_BLOG_START) + r"[\s\S]*?" + re.escape(HOME_BLOG_END)
    )
    new_block = HOME_BLOG_START + "\n" + "\n".join(items) + "\n" + HOME_BLOG_END
    new_html, n = pattern.subn(new_block, html)
    if n and new_html != html:
        HOME_INDEX_FILE.write_text(new_html)
        log(f"홈 블로그 섹션 갱신 ({len(posts)}편)")


def update_sitemap(new_posts):
    if not SITEMAP_FILE.exists():
        return
    xml = SITEMAP_FILE.read_text()
    lines = []
    for post in new_posts:
        url = f"https://manddo.kr/blog/{post['slug']}.html"
        if url in xml:
            continue
        lines.append(
            f'  <url><loc>{url}</loc><lastmod>{post["date"].isoformat()}</lastmod><priority>0.7</priority></url>'
        )
    if not lines:
        return
    marker = "<!-- STOCK_URLS_START -->"
    if marker in xml:
        xml = xml.replace(marker, "\n".join(lines) + "\n  " + marker)
    else:
        xml = xml.replace("</urlset>", "\n".join(lines) + "\n</urlset>")
    SITEMAP_FILE.write_text(xml)
    log(f"sitemap에 {len(lines)}개 URL 추가")


def run_git(*args):
    res = subprocess.run(
        ["git", *args], cwd=SITE, capture_output=True, text=True
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def git_publish(n):
    code, out, _ = run_git("status", "--porcelain", "blog/", "sitemap.xml", "index.html")
    if not out.strip():
        log("변경 없음 — 커밋 스킵")
        return
    run_git("add", "blog/", "sitemap.xml", "index.html")
    msg = f"블로그 자동 게시 {n}건 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    code, _, err = run_git("commit", "-m", msg)
    if code != 0:
        log(f"commit 실패: {err}")
        return
    code, _, err = run_git("push", "origin", "main")
    if code != 0:
        log(f"push 실패: {err}")
        return
    log("git push 완료")


def process_file(md_path):
    text = md_path.read_text()
    meta, body = parse_frontmatter(text)
    if not meta.get("title"):
        # 첫 H1을 제목으로 추정
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if m:
            meta["title"] = m.group(1).strip()
            body = re.sub(r"^#\s+.+\n?", "", body, count=1, flags=re.MULTILINE)
        else:
            meta["title"] = md_path.stem
    slug = meta.get("slug") or slugify(md_path.stem)
    html, d = render_post(meta, body, slug)
    out_path = BLOG_DIR / f"{slug}.html"
    out_path.write_text(html)
    log(f"게시: {md_path.name} → blog/{slug}.html")
    return {
        "slug": slug,
        "title": meta["title"],
        "category": meta.get("category", "투자일기"),
        "date": d,
        "excerpt": meta.get("description") or extract_excerpt(body),
    }


def main():
    BLOG_SRC.mkdir(exist_ok=True)
    DONE_DIR.mkdir(exist_ok=True)
    md_files = sorted(
        f for f in BLOG_SRC.glob("*.md")
        if f.name.lower() != "readme.md" and not f.name.startswith(".")
    )
    if not md_files:
        return
    log(f"대상 파일 {len(md_files)}개 발견")
    processed = []
    for f in md_files:
        try:
            info = process_file(f)
            processed.append(info)
            # 게시완료로 이동 (같은 이름이면 덮어쓰기)
            dest = DONE_DIR / f.name
            if dest.exists():
                dest.unlink()
            shutil.move(str(f), str(dest))
        except Exception as e:
            log(f"처리 실패 {f.name}: {e}")
    if not processed:
        return
    # 최신이 위로 오도록 역순
    processed.sort(key=lambda p: p["date"], reverse=True)
    update_blog_index(processed)
    update_home_blog_section()
    update_sitemap(processed)
    git_publish(len(processed))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적 오류: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
