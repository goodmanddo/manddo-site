#!/usr/bin/env python3
"""
오늘의 매크로 브리핑 생성 → /macro/ (한국어) + /macro/mn/ (몽골어) → git push

평일 07:45 LaunchAgent 실행. 06:00 morning-signal이 만든 signal.json
(미 증시·환율·섹터·요약)과 weekly_calendar.json(경제 일정)을 재료로 삼는다.

핵심: 숫자 나열이 아니라 "무슨 일이 있었나 → 뭐가 좋고 나빴나 → 왜 그런가"를
쉬운 말로 풀어 쓴다. 숫자는 보조로만.

토큰 절약 설계:
  - 데이터 수집/표 렌더는 순수 파이썬 (Claude 호출 0)
  - 하루 1회 Haiku 호출로 [쉬운 한국어 해설 + 몽골어 버전]을 한 번에 생성
  - 이미 생성한 날짜는 캐시에서 읽어 재호출하지 않음
  - 호출 실패 시 signal 원문 요약을 그대로 노출하고 페이지는 발행(내구성)
"""

import json
import os
import subprocess
from datetime import date, datetime
from pathlib import Path

# 키 파일을 진실의 소스로 우선 사용 (환경변수에 옛 키가 박혀 있어도 파일 키로 덮어씀)
_kf = os.path.expanduser("~/stock_auto_trade/.anthropic_key")
if os.path.isfile(_kf):
    os.environ["ANTHROPIC_API_KEY"] = open(_kf).read().strip()

ROOT = Path.home() / "manddo-site"
AILOG = ROOT / "ai-log"
MACRO = ROOT / "macro"
MACRO_MN = MACRO / "mn"
CACHE = MACRO / ".cache"            # 날짜별 생성 콘텐츠 캐시(JSON) — git 미포함
LOG_FILE = ROOT / "scripts" / "macro_brief.log"
SITEMAP = ROOT / "sitemap.xml"

HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ── 공통 조각 ────────────────────────────────────────────────────────────────
NAV = """<header class="site-header">
  <div class="site-header-inner">
    <a href="/" class="logo">만또<span>.kr</span></a>
    <nav class="nav">
      <a href="/">홈</a>
      <a href="/ai-project/">🧪 AI 1년 실험</a>
      <a href="/crypto/">₿ 비트코인</a>
      <a href="/macro/" class="active">📰 오늘 매크로</a>
      <a href="/vs/">🏁 휴먼 vs AI</a>
      <a href="/nps/">🏛️ 국민연금</a>
      <a href="/etf/">📊 ETF</a>
      <a href="/stock/">차트분석 리포트</a>
      <a href="/learn/">학습</a>
      <a href="/blog/">블로그</a>
      <a href="/tools/">머니 툴</a>
    </nav>
  </div>
</header>"""

FOOTER = """<footer class="site-footer">
  <div class="site-footer-inner">
    <a href="/about.html">소개</a>·
    <a href="/privacy.html">개인정보처리방침</a>·
    <a href="/terms.html">이용약관</a>·
    <a href="/contact.html">문의</a>
    <div class="copy">© 2026 만또 (manddo.kr) · 본 사이트는 투자 자문업자가 아닙니다</div>
  </div>
</footer>"""

STYLE = """<style>
.lang-toggle{display:flex;gap:8px;margin:16px 0 0}
.lang-toggle a{display:inline-block;padding:6px 14px;border:1px solid #d1d6db;border-radius:20px;font-size:13px;color:#4e5968;text-decoration:none;background:#fff}
.lang-toggle a.active{background:#191f28;color:#fff;border-color:#191f28}
.lang-toggle a:hover{border-color:#3182F6;color:#3182F6}
.lang-toggle a.active:hover{color:#fff}
.msec{margin:36px 0}
.msec h2{font-size:20px;font-weight:800;color:#191f28;letter-spacing:-0.03em;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #3182F6;display:inline-block}
.mhead{font-size:20px;font-weight:800;color:#191f28;letter-spacing:-0.03em;line-height:1.45;margin:0 0 16px}
.mstory p{font-size:16px;line-height:1.85;color:#333d4b;margin:0 0 16px}
.mstory p:last-child{margin-bottom:0}
.mnote{font-size:12.5px;color:#8b95a1;margin-top:8px}
.mstats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:6px 0 0}
@media(min-width:600px){.mstats{grid-template-columns:repeat(4,1fr)}}
.mstat{background:#fff;border:1px solid #eef0f3;border-radius:10px;padding:14px 16px}
.mstat .lbl{font-size:12px;color:#8b95a1;margin-bottom:6px}
.mstat .val{font-size:20px;font-weight:800;color:#191f28;letter-spacing:-0.02em}
.mstat .val.up{color:#e0342b}
.mstat .val.down{color:#1B64DA}
.mcal{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.mcal li{background:#fff;border:1px solid #eef0f3;border-radius:10px;padding:12px 16px}
.mcal .when{font-size:12.5px;color:#8b95a1;margin-bottom:4px}
.mcal .ttl{font-size:15px;font-weight:700;color:#191f28}
.mcal .view{font-size:13.5px;line-height:1.6;color:#4e5968;margin-top:4px}
.mcal .hi{color:#e0342b;font-weight:700}
.march{list-style:none;padding:0;margin:16px 0;display:grid;gap:8px}
.march li a{display:flex;justify-content:space-between;gap:12px;padding:12px 16px;background:#fff;border:1px solid #eef0f3;border-radius:10px;text-decoration:none;color:#333d4b}
.march li a:hover{border-color:#3182F6}
.march .d{font-weight:700;color:#191f28;white-space:nowrap}
.march .s{font-size:13px;color:#8b95a1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style>"""


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def run_git(*args):
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def fmt_pct(v):
    try:
        return f"{'+' if v >= 0 else ''}{v:.2f}%"
    except Exception:
        return "—"


def dir_class(v):
    try:
        return "up" if v > 0 else ("down" if v < 0 else "")
    except Exception:
        return ""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ── 데이터 로드 ──────────────────────────────────────────────────────────────
def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception as e:
        log(f"로드 실패 {path}: {e}")
        return {}


def collect_events(cal, today):
    """오늘 이후 경제 일정, high 우선 최대 5건."""
    out = [e for e in cal.get("events", []) if e.get("date", "") >= today]
    out.sort(key=lambda e: (e.get("date", ""), 0 if e.get("impact") == "high" else 1))
    return out[:5]


# ── 콘텐츠 생성 (Haiku, 하루 1회, 캐시) ───────────────────────────────────────
def generate_content(today, us, events):
    """숫자를 쉬운 해설로 풀고 몽골어까지 한 번에. 실패 시 None(폴백)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{today}.json"
    if cache_file.exists():
        log("콘텐츠 캐시 사용 — Claude 호출 스킵")
        return json.loads(cache_file.read_text())

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log("ANTHROPIC_API_KEY 없음 — 원문 폴백")
        return None

    data = {
        "indices": us.get("indices", []),
        "fx_usd_krw": us.get("fx_usd_krw"),
        "fx_change_pct": us.get("fx_change_pct"),
        "sector_focus": us.get("sector_focus", ""),
        "raw_summary": us.get("summary", ""),
        "events": [{"title": e.get("title", ""), "view": e.get("view", "")} for e in events],
    }
    prompt = (
        "너는 주식 초보와, 한국어가 서툰 외국인 친구도 이해할 수 있게 매일 아침 "
        "매크로(거시경제·증시) 이슈를 쉽게 풀어주는 에디터야.\n\n"
        "아래는 오늘 새벽 미국 증시 마감 데이터야. 숫자를 그대로 나열하지 말고, "
        "'무슨 일이 있었나 → 뭐가 좋았고 뭐가 나빴나 → 왜 그런가 → 그래서 한국 시장엔 "
        "어떤 의미인가'를 쉬운 말로 풀어줘. 전문용어(예: SOXX, 필수소비)는 괄호로 짧게 "
        "풀이. 3문단, 각 문단 2~3문장. 겁주지 말고 담담하게.\n"
        "말투는 정중한 존댓말 해설체('~합니다/~했어요/~입니다')로. 반말('~했어/~야') 절대 "
        "금지. 쉽지만 예의 있게.\n\n"
        "그리고 똑같은 내용을 자연스러운 현대 몽골어(키릴)로도 써줘(오타·어색한 표현 없이). "
        "events는 몽골어 번역만.\n\n"
        "설명 없이 아래 형식의 JSON만 출력:\n"
        '{"headline_ko":"...", "story_ko":["문단1","문단2","문단3"], '
        '"headline_mn":"...", "story_mn":["...","...","..."], '
        '"events_mn":[{"title":"...","view":"..."}]}\n\n'
        "데이터:\n" + json.dumps(data, ensure_ascii=False)
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = resp.content[0].text.strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1].lstrip("json").strip()
        content = json.loads(txt)
        cache_file.write_text(json.dumps(content, ensure_ascii=False, indent=2))
        log("콘텐츠 생성 완료 (Haiku)")
        return content
    except Exception as e:
        log(f"콘텐츠 생성 실패 — 원문 폴백: {e}")
        return None


# ── 페이지 조각 ──────────────────────────────────────────────────────────────
def stats_html(us):
    cells = []
    for idx in us.get("indices", []):
        v = idx.get("change_pct")
        cells.append(
            f'<div class="mstat"><div class="lbl">{esc(idx.get("name",""))}</div>'
            f'<div class="val {dir_class(v)}">{fmt_pct(v)}</div></div>'
        )
    fx = us.get("fx_usd_krw")
    fxc = us.get("fx_change_pct")
    if fx is not None:
        color = "#e0342b" if (fxc or 0) > 0 else "#1B64DA"
        cells.append(
            f'<div class="mstat"><div class="lbl">원/달러 '
            f'<span style="color:{color}">{fmt_pct(fxc)}</span></div>'
            f'<div class="val">{fx:,.1f}</div></div>'
        )
    return '<div class="mstats">' + "".join(cells) + "</div>"


def cal_html(events, mn_events=None):
    rows = []
    for i, ev in enumerate(events):
        hi = ' <span class="hi">중요</span>' if ev.get("impact") == "high" else ""
        title, view = ev.get("title", ""), ev.get("view", "")
        if mn_events and i < len(mn_events):
            title = mn_events[i].get("title", title)
            view = mn_events[i].get("view", view)
        when = f'{ev.get("date","")} {ev.get("weekday","")} {ev.get("time","")}'.strip()
        rows.append(
            f'<li><div class="when">{esc(when)}{hi}</div>'
            f'<div class="ttl">{esc(title)}</div>'
            f'<div class="view">{esc(view)}</div></li>'
        )
    return '<ul class="mcal">' + "".join(rows) + "</ul>" if rows else ""


def story_html(headline, paragraphs):
    head = f'<p class="mhead">{esc(headline)}</p>' if headline else ""
    body = "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
    return head + f'<div class="mstory">{body}</div>'


# ── 페이지 렌더 ──────────────────────────────────────────────────────────────
def render_page(lang, today, us, events, content):
    is_mn = lang == "mn"
    root = "/macro/mn/" if is_mn else "/macro/"

    if is_mn:
        t = dict(
            title=f"Өнөөдрийн макро тойм ({today}) | Манто",
            desc="Өмнөх шөнийн АНУ-ын зах зээлд юу болов, юу сайн байв, яагаад — Солонгосын зах зээл нээхээс өмнө ойлгомжтой, энгийн хэлээр.",
            back="← Буцах", crumb="Макро тойм", h1="📰 Өнөөдрийн макро тойм",
            when="Өмнөх шөнийн АНУ-ын зах зээлийн хаалт дээр үндэслэв",
            flow="📰 Өнөөдөр юу болов", nums="📊 Тоогоор харвал",
            cal="🗓️ Энэ долоо хоногт анхаарах зүйл",
            disc="Энэ хуудас нь ерөнхий мэдээллийн зорилготой бөгөөд хувьцаа худалдан авах·зарах зөвлөмж биш.",
        )
        if content:
            headline = content.get("headline_mn", "")
            paras = content.get("story_mn") or [us.get("summary", "")]
            cal = cal_html(events, content.get("events_mn"))
        else:
            headline, paras, cal = "", [us.get("summary", "")], cal_html(events)
    else:
        t = dict(
            title=f"오늘의 매크로 브리핑 ({today}) — 밤사이 무슨 일이 있었나 | 만또",
            desc="밤사이 미국 증시에 무슨 일이 있었고 뭐가 좋았는지, 그 이유를 쉬운 말로. 숫자는 보조로. 매일 아침 코스피 개장 전 업데이트.",
            back="← 뒤로", crumb="오늘의 매크로", h1="📰 오늘의 매크로 브리핑",
            when="밤사이 미국 증시 마감 기준",
            flow="📰 오늘 무슨 일이 있었나", nums="📊 숫자로 보면",
            cal="🗓️ 이번 주 챙길 일정",
            disc="본 페이지는 교육·참고용 일반 정보이며 특정 종목의 매수·매도를 권유하지 않습니다.",
        )
        if content:
            headline = content.get("headline_ko", "")
            paras = content.get("story_ko") or [us.get("summary", "")]
        else:
            headline, paras = "", [us.get("summary", "")]
        cal = cal_html(events)

    cal_block = f'<section class="msec"><h2>{t["cal"]}</h2>{cal}</section>' if cal else ""

    return f"""<!DOCTYPE html>
<html lang="{'mn' if is_mn else 'ko'}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t['title']}</title>
<meta name="description" content="{t['desc']}">
<meta property="og:title" content="{t['title']}">
<meta property="og:description" content="{t['desc']}">
<meta property="og:type" content="article">
<meta property="og:url" content="https://manddo.kr{root}{today}.html">
<link rel="canonical" href="https://manddo.kr{root}{today}.html">
<link rel="alternate" hreflang="ko" href="https://manddo.kr/macro/{today}.html">
<link rel="alternate" hreflang="mn" href="https://manddo.kr/macro/mn/{today}.html">
<link rel="stylesheet" href="/css/main.css">
{STYLE}
  <!-- adsense-script -->
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8944533986573467" crossorigin="anonymous"></script>
</head>
<body>
{NAV}
<main class="page">
  <a class="back-btn" href="{root}" onclick="if(document.referrer&&history.length>1){{event.preventDefault();history.back()}}">{t['back']}</a>
  <div class="breadcrumb"><a href="{root}">{t['crumb']}</a><span class="sep">/</span>{today}</div>
  <section class="learn-hero">
    <h1>{t['h1']}</h1>
    <p class="lead">{today} · {t['when']}</p>
    <div class="lang-toggle">
      <a href="/macro/{today}.html"{' class="active"' if not is_mn else ''}>🇰🇷 한국어</a>
      <a href="/macro/mn/{today}.html"{' class="active"' if is_mn else ''}>🇲🇳 Монгол</a>
    </div>
  </section>
  <section class="msec">
    <h2>{t['flow']}</h2>
    {story_html(headline, paras)}
  </section>
  <section class="msec">
    <h2>{t['nums']}</h2>
    {stats_html(us)}
  </section>
  {cal_block}
  <div class="disclaimer"><b>⚠</b> {t['disc']}</div>
</main>
{FOOTER}
</body>
</html>
"""


# ── 인덱스 재생성 ────────────────────────────────────────────────────────────
def snippet_of(path):
    try:
        html = path.read_text()
        seg = html.split('class="mhead">', 1)
        if len(seg) > 1:
            return seg[1].split("</p>", 1)[0][:90]
    except Exception:
        pass
    return ""


def rebuild_index(lang):
    is_mn = lang == "mn"
    folder = MACRO_MN if is_mn else MACRO
    root = "/macro/mn/" if is_mn else "/macro/"
    files = sorted(folder.glob("20??-??-??.html"), key=lambda p: p.stem, reverse=True)
    rows = "".join(
        f'<li><a href="{root}{p.stem}.html"><span class="d">{p.stem}</span>'
        f'<span class="s">{esc(snippet_of(p))}</span></a></li>'
        for p in files[:60]
    )
    empty = "Одоогоор мэдээ алга." if is_mn else "아직 브리핑이 없습니다."
    listing = f'<ul class="march">{rows}</ul>' if rows else \
        f'<p style="color:#8b95a1">{empty}</p>'

    if is_mn:
        title = "Макро тойм — Өдөр бүрийн зах зээлийн энгийн тайлбар | Манто"
        desc = "Өмнөх шөнийн АНУ-ын зах зээлд юу болов, яагаад — өдөр бүр энгийн хэлээр. Монгол хэлээр."
        h1, crumb = "📰 Макро тойм", "Макро тойм"
        lead = ("Өмнөх шөнийн АНУ-ын зах зээлд юу болов, юу сайн байв, яагаад тэр вэ — "
                "Солонгосын зах зээл нээхээс өмнө өдөр бүр энгийн хэлээр тайлбарлана. "
                "Тоо биш, ойлголт руу.")
        toggle = ('<a href="/macro/">🇰🇷 한국어</a>'
                  '<a href="/macro/mn/" class="active">🇲🇳 Монгол</a>')
        arch = "Архив"
    else:
        title = "오늘의 매크로 — 밤사이 무슨 일이 있었나, 쉽게 | 만또"
        desc = "밤사이 미국 증시에 무슨 일이 있었고 뭐가 좋았는지, 그 이유를 쉬운 말로. 평일 아침 8시 업데이트."
        h1, crumb = "📰 오늘의 매크로", "오늘의 매크로"
        lead = ("밤사이 미국 증시에 무슨 일이 있었고, 뭐가 좋았고 왜 그런지를 코스피 개장 전 "
                "아침 8시에 쉬운 말로 풀어 드립니다. 숫자 나열이 아니라 '그래서 무슨 뜻인가'를 중심으로.")
        toggle = ('<a href="/macro/" class="active">🇰🇷 한국어</a>'
                  '<a href="/macro/mn/">🇲🇳 Монгол</a>')
        arch = "지난 브리핑"

    html = f"""<!DOCTYPE html>
<html lang="{'mn' if is_mn else 'ko'}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:url" content="https://manddo.kr{root}">
<link rel="canonical" href="https://manddo.kr{root}">
<link rel="alternate" hreflang="ko" href="https://manddo.kr/macro/">
<link rel="alternate" hreflang="mn" href="https://manddo.kr/macro/mn/">
<link rel="stylesheet" href="/css/main.css">
{STYLE}
  <!-- adsense-script -->
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8944533986573467" crossorigin="anonymous"></script>
</head>
<body>
{NAV}
<main class="page">
  <a class="back-btn" href="/" onclick="if(document.referrer&&history.length>1){{event.preventDefault();history.back()}}">← {'Буцах' if is_mn else '뒤로'}</a>
  <div class="breadcrumb"><a href="/">{'Нүүр' if is_mn else '홈'}</a><span class="sep">/</span>{crumb}</div>
  <section class="learn-hero">
    <h1>{h1}</h1>
    <p class="lead">{lead}</p>
    <div class="lang-toggle">{toggle}</div>
  </section>
  <section class="msec">
    <h2>{arch}</h2>
    {listing}
  </section>
</main>
{FOOTER}
</body>
</html>
"""
    (folder / "index.html").write_text(html)


# ── 사이트맵 ─────────────────────────────────────────────────────────────────
def update_sitemap(today):
    try:
        txt = SITEMAP.read_text()
        if f"/macro/{today}.html" in txt:
            return
        entries = (
            f'  <url><loc>https://manddo.kr/macro/{today}.html</loc>'
            f'<lastmod>{today}</lastmod><priority>0.7</priority></url>\n'
            f'  <url><loc>https://manddo.kr/macro/mn/{today}.html</loc>'
            f'<lastmod>{today}</lastmod><priority>0.6</priority></url>\n'
        )
        SITEMAP.write_text(txt.replace("</urlset>", entries + "</urlset>"))
    except Exception as e:
        log(f"사이트맵 갱신 실패: {e}")


def git_commit_and_push(today):
    code, out, err = run_git("status", "--porcelain", "macro/", "sitemap.xml")
    if not out.strip():
        log("변경사항 없음 — 커밋 스킵")
        return
    run_git("add", "macro/", "sitemap.xml")
    code, out, err = run_git("commit", "-m", f"macro: 오늘의 매크로 브리핑 {today}")
    if code != 0:
        log(f"git commit 실패: {err or out}")
        return
    code, out, err = run_git("push", "origin", "main")
    if code != 0:
        log(f"git push 실패: {err or out}")
        return
    log("git push 완료")


def main():
    MACRO.mkdir(parents=True, exist_ok=True)
    MACRO_MN.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    signal = load_json(AILOG / "signal.json")
    us = signal.get("us_market", {})
    if not us:
        log("signal.json에 us_market 없음 — 중단")
        return
    if signal.get("date") != today:
        log(f"주의: signal.json 날짜({signal.get('date')}) != 오늘({today}) — 최신 데이터로 진행")

    events = collect_events(load_json(AILOG / "weekly_calendar.json"), today)
    content = generate_content(today, us, events)

    (MACRO / f"{today}.html").write_text(render_page("ko", today, us, events, content))
    (MACRO_MN / f"{today}.html").write_text(render_page("mn", today, us, events, content))
    rebuild_index("ko")
    rebuild_index("mn")
    update_sitemap(today)
    log(f"페이지 생성 완료: /macro/{today}.html (+mn)")

    git_commit_and_push(today)


if __name__ == "__main__":
    main()
