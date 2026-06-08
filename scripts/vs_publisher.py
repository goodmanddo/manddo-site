#!/usr/bin/env python3
"""
종목 vs 종목 비교 자동 발행

매주 수요일 09:00 LaunchAgent가 호출.
vs_topics.json에서 status=pending이고 scheduled_date<=today인 가장 빠른 토픽 1개를
Claude API로 비교 분석 글 생성 → /vs/stock/{slug}.html 발행 → 인덱스/사이트맵 갱신 → git push → status=published.
"""
import json, os, re, subprocess, sys
from datetime import date, datetime
from pathlib import Path

import anthropic

SITE = Path("/Users/mandoo/manddo-site")
TOPICS = SITE / "scripts" / "vs_topics.json"
OUT_DIR = SITE / "vs" / "stock"
SITEMAP = SITE / "sitemap.xml"
LOG = Path("/Users/mandoo/vs_publisher.log")


def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


# ---------- 토픽 ----------
def load_topics() -> dict:
    return json.loads(TOPICS.read_text())


def save_topics(data: dict):
    TOPICS.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def pick_topic(data: dict) -> dict | None:
    today = date.today().isoformat()
    candidates = [
        t for t in data["topics"]
        if t.get("status") == "pending" and t.get("scheduled_date", "9999") <= today
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.get("scheduled_date", ""))
    return candidates[0]


# ---------- Claude ----------
SYSTEM = """너는 만또(manddo.kr)의 종목 비교 시리즈 필자다. 같은 업종·테마의 두 종목을 비교 분석하는 글 한 편을 작성한다.

규칙:
- 한국어, 진지하면서 균형 있는 톤. 한쪽 편들지 않고 데이터·비즈니스 모델 차이를 명확히
- 단순 "어느 게 좋다" 결론보다 "어떤 투자자에게 어느 쪽이 맞다" 식 조건부 결론
- 추측이나 모르는 숫자는 적지 말 것. 일반적으로 알려진 사업 구조·재무 성격 위주
- HTML 안에서는 <p>, <b>, <ul>, <ol>, <li>, <code>만 사용 (svg/script/img 금지)
- 출력은 아래 JSON 스키마만, 그 외 텍스트 금지

JSON 스키마:
{
  "lead": "히어로 lead 2~3문장. 두 종목 비교의 의미를 한 문단으로.",
  "tldr": "한 줄 요약 (각 종목 강점 비교, 누구에게 어느 쪽 맞는지)",
  "comparison_table": [
    {"항목": "사업 영역", "stock_a": "...", "stock_b": "..."},
    {"항목": "주력 매출원", "stock_a": "...", "stock_b": "..."},
    {"항목": "재무 특징", "stock_a": "...", "stock_b": "..."},
    {"항목": "배당 성향", "stock_a": "...", "stock_b": "..."},
    {"항목": "주가 변동성", "stock_a": "...", "stock_b": "..."},
    {"항목": "성장 동력", "stock_a": "...", "stock_b": "..."}
  ],
  "business_model_a": "<p>종목 A 비즈니스 모델 설명 본문 (3~4문장)</p>",
  "business_model_b": "<p>종목 B 비즈니스 모델 설명 본문 (3~4문장)</p>",
  "financial_view": "<p>두 종목 재무 성격 비교 본문 (5~7문장). 매출 구조·이익률 성격·부채·현금흐름 등.</p>",
  "technical_view": "<p>두 종목 주가·수급 성격 비교 본문 (3~5문장). 변동성·외인 비중·거래량 차이 등.</p>",
  "risks_a": ["종목 A 리스크 1", "리스크 2", "리스크 3"],
  "risks_b": ["종목 B 리스크 1", "리스크 2", "리스크 3"],
  "verdict": {
    "summary": "최종 결론 2~3문장 (균형 잡힌 톤)",
    "for_growth": "성장주 선호 투자자에게 추천 (A 또는 B + 이유 1문장)",
    "for_dividend": "배당·안정 선호 투자자에게 추천 (A 또는 B + 이유 1문장)",
    "for_swing": "단기 트레이딩 선호 투자자에게 추천 (A 또는 B + 이유 1문장)"
  },
  "faq": [
    {"q": "...", "a": "..."},
    {"q": "...", "a": "..."},
    {"q": "...", "a": "..."}
  ]
}
"""


def gen_content(topic: dict) -> dict:
    client = anthropic.Anthropic(api_key=(os.environ.get("ANTHROPIC_API_KEY") or open(os.path.expanduser("~/stock_auto_trade/.anthropic_key")).read().strip()))
    user = f"""비교 대상:
- 종목 A: {topic['stock_a']['name']} ({topic['stock_a']['code']})
- 종목 B: {topic['stock_b']['name']} ({topic['stock_b']['code']})
- 테마: {topic['theme']}

이 두 종목 비교 글 1편 작성. JSON만 출력. comparison_table의 stock_a/stock_b 필드 키는 그대로 둘 것."""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    return json.loads(text)


# ---------- HTML ----------
NAV_HTML = """<header class="site-header">
  <div class="site-header-inner">
    <a href="/" class="logo">만또<span>.kr</span></a>
    <nav class="nav">
      <a href="/">홈</a>
      <a href="/ai-project/">🧪 AI 1년 실험</a>
      <a href="/ai-log/">오늘 AI의 선택</a>
      <a href="/vs/" class="active">🏁 휴먼 vs AI</a>
      <a href="/stock/">차트분석 리포트</a>
      <a href="/learn/">학습</a>
      <a href="/blog/">블로그</a>
      <a href="/tools/">머니 툴</a>
      <a href="/about.html">소개</a>
    </nav>
  </div>
</header>"""

FOOTER_HTML = """<footer class="site-footer">
  <div class="site-footer-inner">
    <a href="/about.html">소개</a>·
    <a href="/privacy.html">개인정보처리방침</a>·
    <a href="/terms.html">이용약관</a>·
    <a href="/contact.html">문의</a>
    <div class="copy">© 2026 만또 (manddo.kr) · 본 사이트는 투자 자문업자가 아닙니다</div>
  </div>
</footer>"""


def render_page(topic: dict, c: dict) -> str:
    a = topic["stock_a"]
    b = topic["stock_b"]
    title = f"{a['name']} vs {b['name']} — 어느 쪽이 나에게 맞을까"
    desc = c["lead"][:140].replace('"', "'")
    keywords = f"{a['name']},{b['name']},{a['name']} vs {b['name']},주식비교,종목비교,{topic['theme']}"

    table_rows = "\n".join(
        f'      <tr><td class="lbl">{r["항목"]}</td>'
        f'<td>{r["stock_a"]}</td>'
        f'<td>{r["stock_b"]}</td></tr>'
        for r in c["comparison_table"]
    )
    risks_a = "\n".join(f"    <li>{x}</li>" for x in c["risks_a"])
    risks_b = "\n".join(f"    <li>{x}</li>" for x in c["risks_b"])
    faq_html = "\n".join(
        f'  <div class="faq-item"><div class="q">{f["q"]}</div><p class="a">{f["a"]}</p></div>'
        for f in c["faq"]
    )
    v = c["verdict"]

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | 만또 종목 비교</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="{keywords}">
<meta property="og:title" content="{a['name']} vs {b['name']}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:url" content="https://manddo.kr/vs/stock/{topic['slug']}.html">
<link rel="canonical" href="https://manddo.kr/vs/stock/{topic['slug']}.html">
<link rel="stylesheet" href="/css/main.css">
<style>
  .vsx-hero {{ background:linear-gradient(135deg,#1B64DA 0%,#7c3aed 100%); border-radius:16px; padding:32px 24px 28px; margin:20px 0 24px; color:#fff; }}
  .vsx-hero .theme-badge {{ display:inline-block; font-size:11.5px; font-weight:700; letter-spacing:1px; opacity:0.85; margin-bottom:10px; }}
  .vsx-hero h1 {{ font-size:28px; margin:0 0 8px; color:#fff; font-weight:800; letter-spacing:-0.02em; }}
  .vsx-hero .lead {{ font-size:14.5px; opacity:0.95; line-height:1.6; margin:0; max-width:640px; }}
  .vsx-tldr {{ background:linear-gradient(135deg,#fff7ed 0%,#fef3c7 100%); border-radius:12px; padding:16px 20px; font-size:14px; line-height:1.7; color:#7c2d12; margin-bottom:28px; }}
  .vsx-tldr b {{ color:#7c2d12; }}
  .vs-vs {{ text-align:center; font-size:24px; font-weight:900; color:#7c3aed; margin:12px 0; letter-spacing:0.1em; }}
  .stock-cards {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:28px; }}
  @media (max-width:560px) {{ .stock-cards {{ grid-template-columns:1fr; }} }}
  .stock-card {{ background:#fff; border:1px solid #eef0f3; border-radius:14px; padding:18px 20px; }}
  .stock-card h3 {{ font-size:18px; font-weight:800; margin:0 0 4px; color:#111827; }}
  .stock-card .code {{ font-size:12px; color:#9ca3af; font-family:'SF Mono',ui-monospace,monospace; margin-bottom:10px; }}
  .stock-card p {{ font-size:13.5px; line-height:1.7; color:#374151; margin:0; }}
  .compare-table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #eef0f3; border-radius:12px; overflow:hidden; }}
  .compare-table th {{ background:#f8fafc; font-size:12px; color:#6b7280; padding:11px 12px; text-align:left; font-weight:700; }}
  .compare-table th.col-a, .compare-table th.col-b {{ text-align:center; font-size:13.5px; color:#111827; }}
  .compare-table th.col-a {{ background:#dbeafe; color:#1e40af; }}
  .compare-table th.col-b {{ background:#ede9fe; color:#6b21a8; }}
  .compare-table td {{ padding:12px; border-top:1px solid #f1f5f9; font-size:13.5px; line-height:1.6; color:#374151; }}
  .compare-table td.lbl {{ font-weight:700; color:#111827; width:130px; background:#fafbfc; }}
  .section-block {{ background:#fff; border:1px solid #eef0f3; border-radius:12px; padding:20px 22px; margin-bottom:16px; }}
  .section-block h2 {{ font-size:18px; margin:0 0 10px; }}
  .section-block p {{ font-size:14px; line-height:1.75; color:#374151; }}
  .risks-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:28px; }}
  @media (max-width:560px) {{ .risks-grid {{ grid-template-columns:1fr; }} }}
  .risk-box {{ background:#fff; border:1px solid #fecaca; border-radius:12px; padding:16px 18px; }}
  .risk-box.b {{ border-color:#ddd6fe; }}
  .risk-box h3 {{ font-size:14px; margin:0 0 8px; color:#b91c1c; }}
  .risk-box.b h3 {{ color:#6b21a8; }}
  .risk-box ul {{ margin:0; padding-left:20px; font-size:13.5px; line-height:1.7; color:#374151; }}
  .verdict {{ background:linear-gradient(135deg,#f0fdf4 0%,#dcfce7 100%); border-radius:14px; padding:22px 24px; margin-bottom:28px; }}
  .verdict h2 {{ font-size:18px; margin:0 0 10px; color:#14532d; }}
  .verdict .v-summary {{ font-size:14.5px; line-height:1.75; color:#14532d; margin-bottom:14px; }}
  .verdict .v-rec {{ display:grid; gap:8px; margin-top:14px; }}
  .verdict .vr-item {{ background:rgba(255,255,255,0.7); padding:12px 14px; border-radius:8px; font-size:13.5px; line-height:1.6; color:#14532d; }}
  .verdict .vr-item b {{ display:inline-block; min-width:140px; color:#15803d; }}
  .faq-item {{ background:#fff; border:1px solid #eef0f3; border-radius:10px; padding:14px 16px; margin-bottom:8px; }}
  .faq-item .q {{ font-weight:700; font-size:14px; margin-bottom:6px; color:#111827; }}
  .faq-item .a {{ font-size:13.5px; line-height:1.65; color:#4b5563; margin:0; }}
  .breadcrumb {{ font-size:12px; color:#8b95a1; margin:12px 0; }}
  .breadcrumb a {{ color:#1B64DA; text-decoration:none; }}
  .breadcrumb .sep {{ margin:0 6px; color:#cbd5e1; }}
  .disclaimer {{ background:#f8fafc; border:1px solid #e2e8f0; padding:14px 16px; border-radius:8px; font-size:12.5px; color:#64748b; line-height:1.6; margin-top:32px; }}
</style>
  <!-- adsense-script -->
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8944533986573467" crossorigin="anonymous"></script>
</head>
<body>
{NAV_HTML}
<main class="page">
  <div class="breadcrumb">
    <a href="/vs/">휴먼 vs AI</a><span class="sep">/</span>
    <a href="/vs/stock/">종목 비교</a><span class="sep">/</span>{a['name']} vs {b['name']}
  </div>
  <section class="vsx-hero">
    <div class="theme-badge">VS · {topic['theme'].upper()}</div>
    <h1>{a['name']} vs {b['name']}</h1>
    <p class="lead">{c['lead']}</p>
  </section>

  <div class="vsx-tldr"><b>⚡ 한 줄 요약</b><br>{c['tldr']}</div>

  <div class="stock-cards">
    <div class="stock-card">
      <h3>🅰 {a['name']}</h3>
      <div class="code">{a['code']}</div>
      {c['business_model_a']}
    </div>
    <div class="stock-card">
      <h3>🅱 {b['name']}</h3>
      <div class="code">{b['code']}</div>
      {c['business_model_b']}
    </div>
  </div>

  <h2>📊 항목별 비교</h2>
  <table class="compare-table">
    <thead><tr><th></th><th class="col-a">{a['name']}</th><th class="col-b">{b['name']}</th></tr></thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

  <h2 style="margin-top:32px">💰 재무·실적 성격</h2>
  <div class="section-block">{c['financial_view']}</div>

  <h2>📈 주가·수급 성격</h2>
  <div class="section-block">{c['technical_view']}</div>

  <h2>⚠ 각 종목의 리스크</h2>
  <div class="risks-grid">
    <div class="risk-box">
      <h3>🅰 {a['name']} 리스크</h3>
      <ul>
{risks_a}
      </ul>
    </div>
    <div class="risk-box b">
      <h3>🅱 {b['name']} 리스크</h3>
      <ul>
{risks_b}
      </ul>
    </div>
  </div>

  <h2>🎯 만또의 결론</h2>
  <div class="verdict">
    <p class="v-summary">{v['summary']}</p>
    <div class="v-rec">
      <div class="vr-item"><b>📈 성장주 선호</b>{v['for_growth']}</div>
      <div class="vr-item"><b>💰 배당·안정 선호</b>{v['for_dividend']}</div>
      <div class="vr-item"><b>⚡ 단기 트레이딩</b>{v['for_swing']}</div>
    </div>
  </div>

  <h2>❓ 자주 묻는 질문</h2>
{faq_html}

  <div class="disclaimer">
    <b>⚠ 참고용 안내</b><br>
    본 비교는 일반적으로 알려진 사업 구조·재무 성격 기준의 분석이며 특정 종목의 매수·매도 권유가 아닙니다. 실제 투자 결정은 최신 재무제표·공시·시장 환경을 직접 확인한 후 본인이 판단해야 합니다.
  </div>
</main>
{FOOTER_HTML}
</body>
</html>
"""


# ---------- 인덱스/사이트맵 ----------
def update_index(topic: dict, headline: str):
    idx = OUT_DIR / "index.html"
    if not idx.exists():
        idx.write_text(_initial_index())
    html = idx.read_text()
    a, b = topic["stock_a"], topic["stock_b"]
    card = f"""    <a href="/vs/stock/{topic['slug']}.html" class="vs-card">
      <div class="vs-cat">{topic['theme'].upper()}</div>
      <div class="vs-title">{a['name']} vs {b['name']}</div>
      <div class="vs-desc">{headline}</div>
    </a>
"""
    marker = "<!-- VS-STOCK-CARDS -->"
    if marker in html:
        html = html.replace(marker, card + marker, 1)
    idx.write_text(html)


def _initial_index() -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>종목 vs 종목 비교 시리즈 | 만또 휴먼 vs AI</title>
<meta name="description" content="같은 업종 양대 산맥 종목을 만또가 직접 비교 분석. 사업 모델·재무·리스크·결론까지 한 페이지에.">
<meta name="keywords" content="종목비교,주식비교,vs종목,한국주식비교">
<link rel="canonical" href="https://manddo.kr/vs/stock/">
<link rel="stylesheet" href="/css/main.css">
<style>
  .vs-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin:24px 0; }}
  .vs-card {{ display:block; background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:18px 20px; text-decoration:none; color:inherit; transition:all .15s; }}
  .vs-card:hover {{ border-color:#7c3aed; box-shadow:0 6px 20px rgba(124,58,237,0.12); transform:translateY(-2px); }}
  .vs-card .vs-cat {{ font-size:11.5px; font-weight:700; color:#7c3aed; letter-spacing:0.05em; margin-bottom:6px; }}
  .vs-card .vs-title {{ font-size:18px; font-weight:800; color:#111827; margin-bottom:4px; }}
  .vs-card .vs-desc {{ font-size:13px; color:#4b5563; line-height:1.6; }}
</style>
  <!-- adsense-script -->
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8944533986573467" crossorigin="anonymous"></script>
</head>
<body>
{NAV_HTML}
<main class="page">
  <h1>🥊 종목 vs 종목 비교</h1>
  <p>같은 업종 양대 산맥 종목을 만또가 데이터·비즈니스 모델·리스크 관점으로 비교 분석합니다. 단순 "어느 게 좋다"가 아니라 "어떤 투자자에게 어느 쪽이 맞다" 식 결론.</p>
  <div class="vs-grid">
<!-- VS-STOCK-CARDS -->
  </div>
</main>
{FOOTER_HTML}
</body>
</html>"""


def update_sitemap(slug: str):
    if not SITEMAP.exists():
        return
    s = SITEMAP.read_text()
    today = date.today().isoformat()
    new_url = f"https://manddo.kr/vs/stock/{slug}.html"
    if new_url in s:
        return
    entry = f'  <url><loc>{new_url}</loc><lastmod>{today}</lastmod><priority>0.8</priority></url>\n'
    idx_url = "https://manddo.kr/vs/stock/"
    if idx_url not in s:
        entry = f'  <url><loc>{idx_url}</loc><lastmod>{today}</lastmod><priority>0.8</priority></url>\n' + entry
    s = s.replace("</urlset>", entry + "</urlset>")
    SITEMAP.write_text(s)


# ---------- git ----------
def git_push(slug: str):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    files = [
        f"vs/stock/{slug}.html",
        "vs/stock/index.html",
        "sitemap.xml",
        "scripts/vs_topics.json",
    ]
    subprocess.run(["git", "-C", str(SITE), "add", *files], check=True, env=env)
    diff = subprocess.run(["git", "-C", str(SITE), "diff", "--cached", "--quiet"], env=env)
    if diff.returncode == 0:
        log("  - no changes")
        return
    subprocess.run(
        ["git", "-c", "user.name=mandoo", "-c", "user.email=goodmanddo@gmail.com",
         "-C", str(SITE), "commit", "-m", f"feat(vs): publish stock comparison {slug}"],
        check=True, env=env,
    )
    subprocess.run(["git", "-C", str(SITE), "push"], check=True, env=env)
    log(f"  ✓ git push: {slug}")


# ---------- main ----------
def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("! ANTHROPIC_API_KEY 미설정")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = load_topics()
    topic = pick_topic(data)
    if not topic:
        log("발행할 토픽 없음")
        return

    out_path = OUT_DIR / f"{topic['slug']}.html"
    if out_path.exists():
        log(f"! 이미 존재: {out_path}")
        topic["status"] = "published"
        save_topics(data)
        return

    log(f"발행 시작: {topic['title']} → /vs/stock/{topic['slug']}.html")
    try:
        c = gen_content(topic)
        html = render_page(topic, c)
        out_path.write_text(html)
        update_index(topic, c["tldr"][:80])
        update_sitemap(topic["slug"])
        topic["status"] = "published"
        topic["published_date"] = date.today().isoformat()
        save_topics(data)
        git_push(topic["slug"])
        log(f"✓ 발행 완료: {topic['title']}")
    except Exception as e:
        log(f"! 발행 실패: {e}")
        topic["status"] = "failed"
        save_topics(data)
        raise


if __name__ == "__main__":
    main()
