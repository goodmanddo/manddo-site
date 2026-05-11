#!/usr/bin/env python3
"""ETF 개별 페이지 정적 생성 — etf-holdings.json → /etf/{code}.html (100개)

각 페이지에 포함되는 정보:
- 메타: 운용사·기초지수·상장일·AUM·보수
- TOP 10 보유종목 표 (비중 막대 시각화)
- 섹터 분포 막대 차트
- 수익률 표 (1M/3M/6M/YTD/1Y)
- 같은 테마 ETF 추천 (AUM 상위 4개)

실행: collect_etf.py 직후 LaunchAgent에서 같이 호출되도록 향후 통합.
"""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path.home() / "manddo-site"
DATA = ROOT / "tools" / "data" / "etf-holdings.json"
OUT_DIR = ROOT / "etf"

SECTOR_KOR = {
    "IT": "IT",
    "FINANCIALS": "금융",
    "FINANCE": "금융",
    "INDUSTRIALS": "산업재",
    "ENERGY": "에너지",
    "HEALTHCARE": "헬스케어",
    "HEALTH_CARE": "헬스케어",
    "MATERIALS": "소재",
    "CONSUMER": "소비재",
    "CONSUMER_DISCRETIONARY": "경기소비재",
    "CONSUMER_STAPLES": "필수소비재",
    "REAL_ESTATE": "리츠·부동산",
    "UTILITIES": "유틸리티",
    "TELECOM": "통신",
    "COMMUNICATION_SERVICES": "통신·미디어",
    "INFORMATION_TECHNOLOGY": "IT",
    "OTHERS": "기타",
    "ETC": "기타",
}

COUNTRY_KOR = {
    "KR": "한국", "US": "미국", "CN": "중국", "JP": "일본",
    "HK": "홍콩", "VN": "베트남", "IN": "인도", "EU": "유럽",
    "MISC": "기타", "OTHERS": "기타",
}

PERIOD_LABEL = {
    "D1": "1일", "W1": "1주", "M1": "1개월", "M3": "3개월",
    "M6": "6개월", "YTD": "YTD", "Y1": "1년", "Y3": "3년", "Y5": "5년", "Y10": "10년"
}
PERIOD_ORDER = ["M1", "M3", "M6", "YTD", "Y1", "Y3"]


def fmt_aum(eok):
    if not eok:
        return "—"
    if eok >= 10000:
        return f"{eok/10000:.1f}조"
    return f"{round(eok):,}억"


def fmt_inception(s):
    if not s or len(s) != 8:
        return "—"
    return f"{s[:4]}.{s[4:6]}.{s[6:8]}"


def render_holdings(holdings):
    if not holdings:
        return '<div class="empty-block">이 ETF는 한국 종목 보유 데이터가 제공되지 않습니다 (해외 자산 / 채권 / 원자재 ETF일 가능성).</div>'
    max_w = max(h["weight"] for h in holdings) if holdings else 100
    rows = []
    for i, h in enumerate(holdings, 1):
        pct = (h["weight"] / max_w) * 100
        stock_link = f'/stock/{h["code"]}.html' if h["code"] and h["code"].isdigit() else "#"
        rows.append(f'''
      <div class="hold-row">
        <div class="hold-rk">{i}</div>
        <div class="hold-info">
          <a class="hold-name" href="{stock_link}">{h["name"] or "—"}</a>
          <span class="hold-code">{h["code"] or ""}</span>
        </div>
        <div class="hold-bar"><div class="hold-bar-fill" style="width:{pct:.1f}%"></div></div>
        <div class="hold-weight">{h["weight"]:.2f}%</div>
      </div>''')
    total = sum(h["weight"] for h in holdings)
    return f'''<div class="holds">
      {''.join(rows)}
      <div class="hold-foot">TOP {len(holdings)} 합계 비중: <b>{total:.2f}%</b></div>
    </div>'''


def render_sectors(sectors):
    if not sectors:
        return ""
    sectors = [s for s in sectors if s.get("weight") and s.get("weight") > 0]
    if not sectors:
        return ""
    sectors.sort(key=lambda s: -(s.get("weight") or 0))
    max_w = sectors[0]["weight"] or 100
    rows = []
    for s in sectors[:8]:
        name = SECTOR_KOR.get((s.get("code") or "").upper(), s.get("code") or "")
        pct = (s["weight"] / max_w) * 100
        rows.append(f'''
      <div class="sec-row">
        <div class="sec-name">{name}</div>
        <div class="sec-bar"><div class="sec-bar-fill" style="width:{pct:.1f}%"></div></div>
        <div class="sec-w">{s["weight"]:.1f}%</div>
      </div>''')
    return f'''<div class="block">
      <h2>섹터 분포</h2>
      <div class="secs">{''.join(rows)}</div>
    </div>'''


def render_countries(countries):
    if not countries:
        return ""
    countries = [c for c in countries if c.get("weight") and c.get("weight") > 0]
    if not countries:
        return ""
    countries.sort(key=lambda c: -(c.get("weight") or 0))
    pills = []
    for c in countries[:6]:
        name = COUNTRY_KOR.get((c.get("code") or "").upper(), c.get("code") or "")
        pills.append(f'<span class="ctry-pill">{name} <b>{c["weight"]:.1f}%</b></span>')
    return f'<div class="ctry-bar">{" ".join(pills)}</div>'


def render_returns(returns):
    if not returns:
        return ""
    cells = []
    for code in PERIOD_ORDER:
        if code not in returns:
            continue
        val = returns[code]
        cls = "up" if val >= 0 else "down"
        sign = "+" if val >= 0 else ""
        cells.append(f'''
      <div class="ret-cell">
        <div class="ret-label">{PERIOD_LABEL[code]}</div>
        <div class="ret-val {cls}">{sign}{val:.2f}%</div>
      </div>''')
    if not cells:
        return ""
    return f'''<div class="block">
      <h2>기간별 수익률</h2>
      <div class="rets">{''.join(cells)}</div>
    </div>'''


def render_related(etf, all_etfs):
    related = [e for e in all_etfs
               if e["theme"] == etf["theme"] and e["code"] != etf["code"]]
    related.sort(key=lambda e: -(e.get("aum_eok") or 0))
    related = related[:4]
    if not related:
        return ""
    cards = []
    for r in related:
        top1 = r["holdings"][0] if r["holdings"] else None
        top_txt = f'{top1["name"]} {top1["weight"]:.1f}%' if top1 else "—"
        cards.append(f'''
      <a class="rel-card" href="/etf/{r["code"]}.html">
        <div class="rel-name">{r["name"]}</div>
        <div class="rel-meta">AUM {fmt_aum(r["aum_eok"])} · TOP1 {top_txt}</div>
      </a>''')
    return f'''<div class="block">
      <h2>같은 테마 ETF</h2>
      <div class="rels">{''.join(cards)}</div>
    </div>'''


def render_page(etf, all_etfs, generated_at):
    code = etf["code"]
    name = etf["name"]
    theme = etf["theme"]
    title = f"{name} ({code}) — 구성종목·비중·수익률 | 만또 ETF 도감"
    desc = f"{name} ETF의 TOP 10 보유종목, 비중, 섹터 분포, 기간별 수익률. "
    if etf["holdings"]:
        top1 = etf["holdings"][0]
        desc += f"1위 {top1['name']} {top1['weight']:.1f}%. "
    desc += f"운용사 {etf.get('issuer','') or ''}, AUM {fmt_aum(etf.get('aum_eok'))}, 보수 {etf.get('expense_ratio','—')}%."

    meta_rows = [
        ("운용사", etf.get("issuer") or "—"),
        ("기초지수", etf.get("base_index") or "—"),
        ("상장일", fmt_inception(etf.get("inception"))),
        ("AUM", fmt_aum(etf.get("aum_eok"))),
        ("총보수", f"{etf['expense_ratio']:.3f}%" if etf.get("expense_ratio") is not None else "—"),
        ("NAV", f"{etf['nav']:,.2f}" if etf.get("nav") else "—"),
    ]
    meta_html = ''.join(
        f'<div class="meta-row"><span class="meta-k">{k}</span><span class="meta-v">{v}</span></div>'
        for k, v in meta_rows
    )

    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="{name},{code},{theme},ETF,ETF구성종목,ETF비중">
<meta property="og:title" content="{name} ({code}) 구성종목·비중">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:url" content="https://manddo.kr/etf/{code}.html">
<link rel="canonical" href="https://manddo.kr/etf/{code}.html">
<link rel="stylesheet" href="/css/main.css?v=20260425a">
<style>
.hero{{background:linear-gradient(135deg,#E8F1FF 0%,#D6E5FF 60%,#E0DBFF 100%);border-radius:20px;padding:24px 22px;margin-bottom:18px}}
.hero .theme{{font-size:11px;font-weight:800;color:#1B64DA;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:6px}}
.hero h1{{font-size:24px;font-weight:800;letter-spacing:-0.03em;line-height:1.3;color:#1a2540;margin:0 0 4px}}
.hero h1 .cd{{font-size:13px;color:#5b6480;font-family:'SF Mono',ui-monospace,monospace;font-weight:700;margin-left:8px}}
.hero .updated{{font-size:11.5px;color:#5b6480;margin-top:8px}}

.meta-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0;background:#fff;border:1px solid #eef0f3;border-radius:14px;overflow:hidden;margin-bottom:18px}}
.meta-row{{padding:11px 14px;border-right:1px solid #eef0f3;border-bottom:1px solid #eef0f3;display:flex;flex-direction:column;gap:2px}}
.meta-row:last-child{{border-right:none}}
.meta-k{{font-size:10.5px;color:#8b95a1;font-weight:700;letter-spacing:0.04em;text-transform:uppercase}}
.meta-v{{font-size:14px;color:#191f28;font-weight:800;font-family:'SF Mono',ui-monospace,monospace;letter-spacing:-0.01em}}

.block{{background:#fff;border:1px solid #eef0f3;border-radius:14px;padding:18px 20px;margin-bottom:14px}}
.block h2{{font-size:15px;font-weight:800;color:#191f28;margin:0 0 14px;letter-spacing:-0.02em}}
.ctry-bar{{display:flex;gap:6px;flex-wrap:wrap;margin:-6px 0 12px}}
.ctry-pill{{background:#f7f9fc;color:#4e5968;font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:999px}}
.ctry-pill b{{color:#191f28;margin-left:3px}}

.holds{{display:flex;flex-direction:column;gap:8px}}
.hold-row{{display:grid;grid-template-columns:30px 1.2fr 1.6fr 70px;gap:10px;align-items:center;padding:8px 10px;border-radius:8px;transition:background .12s}}
.hold-row:hover{{background:#f7f9fc}}
.hold-rk{{font-family:'SF Mono',ui-monospace,monospace;font-weight:800;color:#8b95a1;font-size:12px;text-align:center}}
.hold-info{{display:flex;flex-direction:column;gap:1px;overflow:hidden}}
.hold-name{{font-weight:800;color:#191f28;text-decoration:none;font-size:13.5px;letter-spacing:-0.02em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.hold-name:hover{{color:#3182F6}}
.hold-code{{font-family:'SF Mono',ui-monospace,monospace;font-size:10.5px;color:#8b95a1;font-weight:600}}
.hold-bar{{height:8px;background:#f2f4f6;border-radius:4px;overflow:hidden}}
.hold-bar-fill{{height:100%;background:linear-gradient(90deg,#3182F6,#1B64DA);border-radius:4px;transition:width .3s}}
.hold-weight{{font-family:'SF Mono',ui-monospace,monospace;font-weight:800;color:#191f28;text-align:right;font-size:13px;letter-spacing:-0.02em}}
.hold-foot{{padding:12px 10px 4px;font-size:12px;color:#4e5968;border-top:1px dashed #eef0f3;margin-top:6px}}
.hold-foot b{{color:#191f28;font-family:'SF Mono',ui-monospace,monospace}}
.empty-block{{padding:16px;background:#f7f9fc;border-radius:10px;color:#8b95a1;font-size:13px;text-align:center;line-height:1.55}}

.secs{{display:flex;flex-direction:column;gap:7px}}
.sec-row{{display:grid;grid-template-columns:90px 1fr 60px;gap:10px;align-items:center;font-size:13px}}
.sec-name{{color:#4e5968;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.sec-bar{{height:8px;background:#f2f4f6;border-radius:4px;overflow:hidden}}
.sec-bar-fill{{height:100%;background:linear-gradient(90deg,#7B61FF,#3182F6);border-radius:4px}}
.sec-w{{font-family:'SF Mono',ui-monospace,monospace;font-weight:800;color:#191f28;text-align:right;font-size:12.5px}}

.rets{{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px}}
.ret-cell{{background:#f7f9fc;border-radius:10px;padding:11px 8px;text-align:center}}
.ret-label{{font-size:10.5px;color:#8b95a1;font-weight:700;letter-spacing:0.04em;margin-bottom:4px}}
.ret-val{{font-family:'SF Mono',ui-monospace,monospace;font-weight:800;font-size:14.5px;letter-spacing:-0.02em}}
.ret-val.up{{color:#D63939}}
.ret-val.down{{color:#1B64DA}}

.rels{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}}
.rel-card{{background:#f7f9fc;border:1px solid transparent;border-radius:10px;padding:11px 13px;text-decoration:none;color:inherit;transition:all .12s}}
.rel-card:hover{{background:#fff;border-color:#3182F6}}
.rel-name{{font-size:13px;font-weight:800;color:#191f28;letter-spacing:-0.02em;margin-bottom:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.rel-meta{{font-size:11px;color:#8b95a1;font-weight:600}}

.summary{{padding:14px 16px;background:#FFF8E6;border:1px solid #FDE9A8;border-radius:12px;font-size:13px;color:#6b4a00;line-height:1.7;margin-bottom:14px}}
.summary b{{color:#4a3300}}

@media(max-width:600px){{
  .hero h1{{font-size:20px}}
  .meta-row{{padding:9px 12px}}
  .meta-v{{font-size:13px}}
  .hold-row{{grid-template-columns:24px 1.1fr 1fr 60px;gap:6px;padding:6px 4px}}
  .hold-bar{{height:6px}}
  .sec-row{{grid-template-columns:70px 1fr 50px}}
}}
</style>
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8944533986573467" crossorigin="anonymous"></script>
</head>
<body>
<header class="site-header">
  <div class="site-header-inner">
    <a href="/" class="logo">만또<span>.kr</span></a>
    <nav class="nav">
      <a href="/">홈</a>
      <a href="/ai-project/">🧪 AI 1년 실험</a>
      <a href="/ai-log/">오늘 AI의 선택</a>
      <a href="/vs/">🏁 휴먼 vs AI</a>
      <a href="/etf/" class="active">📊 ETF</a>
      <a href="/tools/dividend.html">💰 배당주</a>
      <a href="/stock/">차트분석 리포트</a>
      <a href="/learn/">학습</a>
      <a href="/blog/">블로그</a>
      <a href="/tools/">머니 툴</a>
      <a href="/about.html">소개</a>
    </nav>
  </div>
</header>
<main class="page">
  <a class="back-btn" href="/etf/" onclick="if(document.referrer&&history.length>1){{event.preventDefault();history.back()}}">← 뒤로</a>

  <div class="hero">
    <div class="theme">{theme}</div>
    <h1>{name}<span class="cd">{code}</span></h1>
    <div class="updated">기초지수 {etf.get("base_index") or "—"} · 갱신 {generated_at[:10]}</div>
  </div>

  {render_summary(etf)}

  <div class="meta-grid">
    {meta_html}
  </div>

  {render_countries(etf.get("countries", []))}

  <div class="block">
    <h2>TOP 10 보유종목</h2>
    {render_holdings(etf.get("holdings", []))}
  </div>

  {render_sectors(etf.get("sectors", []))}

  {render_returns(etf.get("returns", {}))}

  {render_related(etf, all_etfs)}

  <div style="margin:18px 0 12px;font-size:11px;color:#8b95a1;line-height:1.7;padding:12px 14px;background:#f7f9fc;border-radius:10px">
    <b>⚠ 참고용 안내</b><br>
    본 페이지는 정보 제공용이며 매수·매도 권유가 아닙니다. 비중·수익률·NAV는 매일 변동하며 운용사 공시가 우선합니다. 데이터: Naver Finance 모바일 API (매주 일요일 자동 갱신).
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
'''


def render_summary(etf):
    """1줄 요약 — top 1 + 운용사 + 보수."""
    holdings = etf.get("holdings", [])
    parts = []
    if holdings:
        top = holdings[:3]
        names = " · ".join(f'{h["name"]} {h["weight"]:.1f}%' for h in top)
        parts.append(f"<b>상위 3종목:</b> {names}")
    if etf.get("expense_ratio") is not None:
        parts.append(f"<b>총보수 {etf['expense_ratio']:.3f}%</b>")
    if etf.get("aum_eok"):
        parts.append(f"AUM <b>{fmt_aum(etf['aum_eok'])}</b>")
    if not parts:
        return ""
    return f'<div class="summary">{" · ".join(parts)}</div>'


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    etfs = data["etfs"]
    generated_at = data["generated_at"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    n = 0
    for etf in etfs:
        code = etf["code"]
        if not code:
            continue
        html = render_page(etf, etfs, generated_at)
        (OUT_DIR / f"{code}.html").write_text(html, encoding="utf-8")
        n += 1
    print(f"✓ {n}개 ETF 페이지 생성 → /etf/")


if __name__ == "__main__":
    main()
