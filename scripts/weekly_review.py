#!/usr/bin/env python3
"""
주간 AI 회고록 자동 발행

매주 일요일 22:00 LaunchAgent가 호출.
이번 주(월~금) 매매 결과 + NAV 변화 + 일별 픽 + 만또 코멘트 → /ai-project/weekly/{ISO주}.html
"""
import json, os, re, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path
import urllib.request

import anthropic

SITE = Path("/Users/mandoo/manddo-site")
TRADE_LOG = Path("/Users/mandoo/stock_auto_trade/trade_log.jsonl")
NAV_FILE = SITE / "ai-project" / "nav_history.json"
STATS_FILE = SITE / "ai-project" / "stats.json"
PICK_HISTORY = SITE / "ai-log" / ".pick_history.jsonl"
SIGNAL_FILE = SITE / "ai-log" / "signal.json"
WEEKLY_DIR = SITE / "ai-project" / "weekly"
SITEMAP = SITE / "sitemap.xml"
LOG = Path("/Users/mandoo/weekly_review.log")


def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def get_week_range(target: date | None = None) -> tuple[date, date, str]:
    """이번 주(월~일)의 시작·끝 date와 ISO 주차 문자열 'YYYY-wWW'."""
    d = target or date.today()
    iso = d.isocalendar()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday, f"{iso.year}-w{iso.week:02d}"


# ---------- 데이터 로드 ----------
def load_trades(start: date, end: date) -> list[dict]:
    """이번 주 매매 내역."""
    out = []
    if not TRADE_LOG.exists():
        return out
    for line in TRADE_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        ts = t.get("timestamp", "")[:10]
        if not ts:
            continue
        try:
            dt = date.fromisoformat(ts)
        except Exception:
            continue
        if start <= dt <= end:
            out.append(t)
    return out


def load_nav_week(start: date, end: date) -> tuple[list[dict], float, float]:
    """이번 주 NAV 시계열 + 주초·주말 NAV."""
    if not NAV_FILE.exists():
        return [], 0, 0
    arr = json.loads(NAV_FILE.read_text())
    week = [x for x in arr if start.isoformat() <= x["date"] <= end.isoformat()]
    if not week:
        return [], 0, 0
    return week, week[0]["nav"], week[-1]["nav"]


def load_picks(start: date, end: date) -> list[dict]:
    """이번 주 일별 픽."""
    if not PICK_HISTORY.exists():
        return []
    out = []
    for line in PICK_HISTORY.read_text().splitlines():
        if not line.strip():
            continue
        try:
            p = json.loads(line)
        except Exception:
            continue
        d = p.get("date", "")
        if start.isoformat() <= d <= end.isoformat():
            out.append(p)
    return out


def load_signal() -> dict:
    if not SIGNAL_FILE.exists():
        return {}
    return json.loads(SIGNAL_FILE.read_text())


def load_stats() -> dict:
    return json.loads(STATS_FILE.read_text()) if STATS_FILE.exists() else {}


# ---------- 분석 ----------
def aggregate_trades(trades: list[dict]) -> dict:
    """매매 내역 요약."""
    buys = [t for t in trades if t.get("action") == "BUY"]
    sells = [t for t in trades if t.get("action") == "SELL"]
    win_count = sum(1 for t in sells if "TAKE_PROFIT" in (t.get("reason") or ""))
    loss_count = sum(1 for t in sells if "STOP_LOSS" in (t.get("reason") or ""))
    return {
        "total": len(trades),
        "buys": len(buys),
        "sells": len(sells),
        "win": win_count,
        "loss": loss_count,
        "buy_list": buys,
        "sell_list": sells,
    }


# ---------- Claude API ----------
SYSTEM = """너는 만또(manddo.kr)의 AI 1년 투자 프로젝트 시즌1의 주간 회고록 필자다.

매주 일요일, 한 주의 매매 결과를 돌아보는 회고 글을 쓴다.

규칙:
- 한국어, 담담하고 솔직한 톤. 과장·낙관 금지
- 수익이 났어도 운인지 실력인지 구분해서 적기
- 손실이 나도 변명·합리화 없이 데이터 그대로
- "만또"가 1인칭으로 적는 느낌 (예: "이번 주는 ~했다", "다음 주는 ~를 봐야겠다")
- 출력은 아래 JSON 스키마만. 그 외 텍스트 금지

JSON 스키마:
{
  "headline": "한 주를 한 줄로 요약 (15자 내외)",
  "summary": "한 주 전체 평가 4~6문장. 수익/손실의 원인, 잘 한 점/못 한 점, 시장 환경 등을 균형있게.",
  "lessons": [
    "이번 주에서 얻은 교훈 1 (구체적, 짧게)",
    "교훈 2",
    "교훈 3"
  ],
  "next_week_focus": "다음 주에 집중할 종목·섹터·전략 2~3문장. 시그널 데이터 참고."
}
"""


def gen_comment(context: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": context}],
    )
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    return json.loads(text)


def build_context(start: date, end: date, trades_agg: dict, nav_arr: list,
                  nav_start: float, nav_end: float, picks: list,
                  stats: dict, signal: dict) -> str:
    week_pnl = nav_end - nav_start if nav_arr else 0
    week_ret = (nav_end / nav_start - 1) * 100 if nav_start else 0

    lines = [
        f"## 주차: {start} ~ {end}",
        f"",
        f"## 자산 변화",
        f"- 주초 NAV: {nav_start:,.0f}원",
        f"- 주말 NAV: {nav_end:,.0f}원",
        f"- 주간 손익: {week_pnl:+,.0f}원 ({week_ret:+.2f}%)",
        f"- 누적 수익률: {stats.get('cumulative_return_pct', 0):+.2f}%",
        f"- 누적 거래 수: {stats.get('trade_count', 0)}",
        f"- 누적 승률: {stats.get('win_rate_pct', 0)}%",
        f"",
        f"## 이번 주 매매 ({trades_agg['total']}건)",
        f"- 매수: {trades_agg['buys']}건",
        f"- 매도: {trades_agg['sells']}건 (익절 {trades_agg['win']}, 손절 {trades_agg['loss']})",
    ]
    if trades_agg["sell_list"]:
        lines.append("\n매도 내역:")
        for t in trades_agg["sell_list"][:10]:
            pnl_label = "익절" if "TAKE_PROFIT" in (t.get("reason") or "") else "손절"
            lines.append(f"  - {t['name']}({t['code']}) {pnl_label} @ {t.get('price', 0):,}원")
    if trades_agg["buy_list"]:
        lines.append("\n매수 내역:")
        for t in trades_agg["buy_list"][:10]:
            lines.append(f"  - {t['name']}({t['code']}) @ {t.get('price', 0):,}원 — {t.get('reason', '')}")

    if picks:
        lines.append(f"\n## 일별 픽 ({len(picks)}일)")
        for p in picks:
            lines.append(f"  - {p['date']}: {p.get('name', '?')}({p.get('code', '?')})")

    # 시그널: 다음 주 관심 종목 후보
    impacted = signal.get("impacted_kr_stocks", [])[:5] if signal else []
    if impacted:
        lines.append(f"\n## 다음 주 시그널 후보 (미장 영향 기준)")
        for s in impacted:
            lines.append(f"  - {s.get('name', '?')}({s.get('code', '?')}) — {s.get('sector', '?')}")

    return "\n".join(lines)


# ---------- HTML 렌더 ----------
NAV_HTML = """<header class="site-header">
  <div class="site-header-inner">
    <a href="/" class="logo">만또<span>.kr</span></a>
    <nav class="nav">
      <a href="/">홈</a>
      <a href="/ai-project/" class="active">🧪 AI 1년 실험</a>
      <a href="/ai-log/">오늘 AI의 선택</a>
      <a href="/vs/">🏁 휴먼 vs AI</a>
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


def render_nav_chart(nav_arr: list, base: float) -> str:
    """간단한 SVG NAV 차트 (주간)."""
    if not nav_arr or len(nav_arr) < 2:
        return '<div class="meta">NAV 데이터 부족</div>'
    pts = [(i, x["nav"]) for i, x in enumerate(nav_arr)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = 600, 160
    pad = 24
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys + [base]), max(ys + [base])
    y_range = max(y_max - y_min, 1)

    def sx(x): return pad + (x - x_min) / max(x_max - x_min, 1) * (w - 2 * pad)
    def sy(y): return h - pad - (y - y_min) / y_range * (h - 2 * pad)

    base_y = sy(base)
    path_d = "M " + " L ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
    last_y = ys[-1]
    color = "#0C8A5A" if last_y >= base else "#D63939"
    fill_color = "rgba(12,138,90,0.1)" if last_y >= base else "rgba(214,57,57,0.1)"
    fill_d = (path_d + f" L {sx(x_max):.1f},{h - pad:.1f} L {sx(x_min):.1f},{h - pad:.1f} Z")
    dots = "".join(
        f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="{color}"/>'
        for x, y in pts
    )
    labels = "".join(
        f'<text x="{sx(x):.1f}" y="{h - 6}" text-anchor="middle" font-size="10" fill="#8b95a1">'
        f'{nav_arr[x]["date"][5:]}</text>'
        for x in xs
    )
    return f"""<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;max-width:600px;display:block;margin:8px auto">
  <line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" stroke="#cbd5e1" stroke-dasharray="3 3"/>
  <path d="{fill_d}" fill="{fill_color}" stroke="none"/>
  <path d="{path_d}" fill="none" stroke="{color}" stroke-width="2.5"/>
  {dots}
  {labels}
</svg>"""


def render_page(start: date, end: date, week_label: str, trades_agg: dict,
                nav_arr: list, nav_start: float, nav_end: float, picks: list,
                stats: dict, comment: dict, signal: dict) -> str:
    week_pnl = nav_end - nav_start if nav_arr else 0
    week_ret = (nav_end / nav_start - 1) * 100 if nav_start else 0
    cum_ret = stats.get("cumulative_return_pct", 0)
    pos_neg = "pos" if week_pnl >= 0 else "neg"

    title = f"AI 시즌1 {week_label} 회고 ({start.month}/{start.day}~{end.month}/{end.day})"
    desc = comment.get("summary", "")[:140].replace('"', "'")

    # 매매 내역 표
    trade_rows = ""
    for t in (trades_agg["sell_list"] + trades_agg["buy_list"])[:20]:
        act = t.get("action", "")
        is_win = "TAKE_PROFIT" in (t.get("reason") or "")
        is_loss = "STOP_LOSS" in (t.get("reason") or "")
        badge = "익절" if is_win else ("손절" if is_loss else "")
        badge_cls = "win" if is_win else ("loss" if is_loss else "")
        reason_short = "취익" if is_win else ("손절" if is_loss else t.get("reason", "")[:18])
        act_cls = "act-buy" if act == "BUY" else "act-sell"
        trade_rows += f"""
        <tr>
          <td><span class="act {act_cls}">{act}</span></td>
          <td><b>{t.get('name', '')}</b><span class="code">{t.get('code', '')}</span></td>
          <td class="num">{t.get('price', 0):,}원</td>
          <td class="num">{t.get('qty', 0)}주</td>
          <td><span class="badge {badge_cls}">{badge or reason_short}</span></td>
          <td class="dt">{t.get('timestamp', '')[5:10]}</td>
        </tr>"""

    # 일별 픽
    pick_html = ""
    if picks:
        items = "".join(
            f'<div class="pick-day"><div class="pd-date">{p["date"][5:]}</div>'
            f'<div class="pd-name">{p.get("name", "?")}</div>'
            f'<div class="pd-code">{p.get("code", "?")}</div></div>'
            for p in picks
        )
        pick_html = f"""
  <section class="proj-section">
    <h2>📅 이번 주 일별 픽</h2>
    <p class="meta">매일 아침 06:00 자동 발행된 "오늘 AI의 선택"</p>
    <div class="pick-week">{items}</div>
  </section>"""

    # 교훈
    lessons_html = "".join(f"<li>{x}</li>" for x in comment.get("lessons", []))

    chart_svg = render_nav_chart(nav_arr, nav_start)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | 만또 AI 1년 실험</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="AI주식, AI자동매매, 매매일지, 주간회고, AI투자결과">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:url" content="https://manddo.kr/ai-project/weekly/{week_label}.html">
<link rel="canonical" href="https://manddo.kr/ai-project/weekly/{week_label}.html">
<link rel="stylesheet" href="/css/main.css">
<style>
  .wrev-hero {{ background:linear-gradient(135deg,#1B64DA 0%,#0d47a1 100%); border-radius:16px; padding:28px 24px; margin:24px 0 24px; color:#fff; }}
  .wrev-hero .badge {{ display:inline-block; font-size:11.5px; font-weight:700; letter-spacing:1px; opacity:0.85; margin-bottom:8px; }}
  .wrev-hero h1 {{ font-size:26px; margin:0 0 6px; color:#fff; font-weight:800; letter-spacing:-0.02em; }}
  .wrev-hero .headline {{ font-size:15px; opacity:0.92; margin:0; }}
  .wrev-stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:0 0 28px; }}
  .ws {{ background:#fff; border:1px solid #eef0f3; border-radius:12px; padding:14px 16px; }}
  .ws .l {{ font-size:11.5px; color:#8b95a1; font-weight:600; margin-bottom:6px; }}
  .ws .v {{ font-size:20px; font-weight:800; font-family:'SF Mono',ui-monospace,monospace; letter-spacing:-0.02em; }}
  .ws .v.pos {{ color:#0C8A5A; }}
  .ws .v.neg {{ color:#D63939; }}
  .nav-chart-wrap {{ background:#fff; border:1px solid #eef0f3; border-radius:12px; padding:18px; margin-bottom:28px; }}
  .nav-chart-wrap h3 {{ font-size:14px; margin:0 0 8px; color:#374151; }}
  .summary-block {{ background:#f8fafc; border-left:3px solid #1B64DA; padding:18px 20px; border-radius:8px; margin-bottom:24px; font-size:14.5px; line-height:1.75; color:#374151; }}
  .trade-table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #eef0f3; border-radius:12px; overflow:hidden; }}
  .trade-table th {{ background:#f8fafc; font-size:11.5px; color:#6b7280; text-align:left; padding:10px 12px; font-weight:600; }}
  .trade-table td {{ padding:11px 12px; border-top:1px solid #f1f5f9; font-size:13.5px; }}
  .trade-table td.num {{ font-family:'SF Mono',ui-monospace,monospace; text-align:right; }}
  .trade-table td.dt {{ color:#9ca3af; font-size:12px; }}
  .trade-table .code {{ display:block; font-size:11px; color:#9ca3af; font-family:'SF Mono',ui-monospace,monospace; }}
  .act {{ display:inline-block; font-size:11px; font-weight:700; padding:3px 8px; border-radius:4px; letter-spacing:0.5px; }}
  .act-buy {{ background:#fee2e2; color:#b91c1c; }}
  .act-sell {{ background:#dbeafe; color:#1e40af; }}
  .badge {{ display:inline-block; font-size:11.5px; padding:3px 8px; border-radius:4px; background:#f1f5f9; color:#475569; }}
  .badge.win {{ background:#dcfce7; color:#15803d; }}
  .badge.loss {{ background:#fee2e2; color:#b91c1c; }}
  .pick-week {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:8px; margin-top:12px; }}
  .pick-day {{ background:#fff; border:1px solid #eef0f3; border-radius:10px; padding:12px; text-align:center; }}
  .pick-day .pd-date {{ font-size:11.5px; color:#8b95a1; margin-bottom:4px; }}
  .pick-day .pd-name {{ font-size:13.5px; font-weight:700; }}
  .pick-day .pd-code {{ font-size:11px; color:#9ca3af; font-family:'SF Mono',ui-monospace,monospace; }}
  .lessons {{ list-style:none; padding:0; margin:0; }}
  .lessons li {{ background:#fff; border:1px solid #eef0f3; border-radius:10px; padding:14px 16px; margin-bottom:8px; font-size:14px; line-height:1.6; position:relative; padding-left:42px; }}
  .lessons li::before {{ content:'💡'; position:absolute; left:14px; top:14px; }}
  .next-focus {{ background:linear-gradient(135deg,#fff7ed 0%,#fef3c7 100%); border-radius:12px; padding:18px 20px; font-size:14.5px; line-height:1.75; color:#7c2d12; }}
  .next-focus b {{ color:#7c2d12; }}
  .proj-section {{ margin:32px 0; }}
  .proj-section h2 {{ font-size:20px; margin:0 0 6px; }}
  .meta {{ color:#8b95a1; font-size:12.5px; }}
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
    <a href="/ai-project/">AI 1년 실험</a><span class="sep">/</span>
    <a href="/ai-project/weekly/">주간 회고</a><span class="sep">/</span>{week_label}
  </div>
  <section class="wrev-hero">
    <div class="badge">WEEKLY REVIEW · {week_label.upper()}</div>
    <h1>{title}</h1>
    <p class="headline">{comment.get('headline', '')}</p>
  </section>

  <div class="wrev-stats">
    <div class="ws"><div class="l">주간 손익</div><div class="v {pos_neg}">{week_pnl:+,.0f}원</div></div>
    <div class="ws"><div class="l">주간 수익률</div><div class="v {pos_neg}">{week_ret:+.2f}%</div></div>
    <div class="ws"><div class="l">매매</div><div class="v">{trades_agg['total']}건</div></div>
    <div class="ws"><div class="l">익절 / 손절</div><div class="v">{trades_agg['win']}/{trades_agg['loss']}</div></div>
    <div class="ws"><div class="l">시즌1 누적</div><div class="v {'pos' if cum_ret >= 0 else 'neg'}">{cum_ret:+.2f}%</div></div>
  </div>

  <div class="nav-chart-wrap">
    <h3>📈 이번 주 NAV 추이</h3>
    {chart_svg}
  </div>

  <section class="proj-section">
    <h2>📝 한 주 평가</h2>
    <div class="summary-block">{comment.get('summary', '')}</div>
  </section>

  <section class="proj-section">
    <h2>📋 매매 내역 ({trades_agg['total']}건)</h2>
    <table class="trade-table">
      <thead><tr><th>구분</th><th>종목</th><th>가격</th><th>수량</th><th>사유</th><th>일자</th></tr></thead>
      <tbody>{trade_rows or '<tr><td colspan="6" style="text-align:center;color:#9ca3af;padding:24px">이번 주 매매 없음</td></tr>'}</tbody>
    </table>
  </section>
{pick_html}
  <section class="proj-section">
    <h2>💡 이번 주에서 얻은 교훈</h2>
    <ul class="lessons">{lessons_html}</ul>
  </section>

  <section class="proj-section">
    <h2>🎯 다음 주 관심</h2>
    <div class="next-focus">{comment.get('next_week_focus', '')}</div>
  </section>

  <div class="disclaimer">
    <b>⚠ 참고용 안내</b><br>
    본 글은 실제 진행 중인 1년 AI 자동매매 실험의 회고 기록입니다. 특정 종목 매수·매도 권유가 아니며, 모든 투자 결과는 변동성에 노출됩니다. 자세한 운영 정책은 <a href="/ai-project/">시즌1 프로젝트 페이지</a> 참고.
  </div>
</main>
{FOOTER_HTML}
</body>
</html>
"""


# ---------- 인덱스/사이트맵 ----------
def update_weekly_index(week_label: str, headline: str, week_ret: float):
    idx = WEEKLY_DIR / "index.html"
    if not idx.exists():
        idx.write_text(_initial_weekly_index())
    html = idx.read_text()
    card = f"""    <a href="/ai-project/weekly/{week_label}.html" class="hub-card">
      <div class="hub-cat">{week_label.upper()}</div>
      <div class="hub-title">{headline}</div>
      <div class="hub-desc">주간 수익률 {week_ret:+.2f}%</div>
    </a>
"""
    marker = "<!-- WEEKLY-CARDS -->"
    if marker in html:
        html = html.replace(marker, card + marker, 1)
    idx.write_text(html)


def _initial_weekly_index() -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 1년 실험 주간 회고 모음 | 만또 AI 1년 실험</title>
<meta name="description" content="AI 자동매매 시즌1 매주 회고록 모음. 한 주 매매, 손익, 교훈을 만또가 정리합니다.">
<link rel="canonical" href="https://manddo.kr/ai-project/weekly/">
<link rel="stylesheet" href="/css/main.css">
<style>
  .hub-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:14px; }}
  .hub-card {{ display:block; padding:16px 18px; background:#fff; border:1px solid #e5e7eb; border-radius:12px; text-decoration:none; color:#191f28; }}
  .hub-card:hover {{ border-color:#1B64DA; box-shadow:0 2px 12px rgba(27,100,218,0.1); }}
  .hub-card .hub-cat {{ font-size:11.5px; color:#1B64DA; font-weight:700; margin-bottom:4px; }}
  .hub-card .hub-title {{ font-size:15px; font-weight:700; margin-bottom:4px; }}
  .hub-card .hub-desc {{ font-size:12.5px; color:#6b7280; }}
</style>
</head>
<body>
{NAV_HTML}
<main class="page">
  <h1>AI 1년 실험 주간 회고</h1>
  <p>매주 일요일, 한 주의 매매 결과를 만또가 정리합니다.</p>
  <div class="hub-grid">
<!-- WEEKLY-CARDS -->
  </div>
</main>
{FOOTER_HTML}
</body>
</html>"""


def update_sitemap(week_label: str):
    if not SITEMAP.exists():
        return
    s = SITEMAP.read_text()
    new_url = f"https://manddo.kr/ai-project/weekly/{week_label}.html"
    if new_url in s:
        return
    today = date.today().isoformat()
    entry = f'  <url><loc>{new_url}</loc><lastmod>{today}</lastmod><priority>0.8</priority></url>\n'
    # 주간 인덱스도 한 번만 추가
    idx_url = "https://manddo.kr/ai-project/weekly/"
    if idx_url not in s:
        entry = f'  <url><loc>{idx_url}</loc><lastmod>{today}</lastmod><priority>0.8</priority></url>\n' + entry
    s = s.replace("</urlset>", entry + "</urlset>")
    SITEMAP.write_text(s)


# ---------- git ----------
def git_push(week_label: str):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    files = [
        f"ai-project/weekly/{week_label}.html",
        "ai-project/weekly/index.html",
        "sitemap.xml",
    ]
    subprocess.run(["git", "-C", str(SITE), "add", *files], check=True, env=env)
    diff = subprocess.run(
        ["git", "-C", str(SITE), "diff", "--cached", "--quiet"], env=env
    )
    if diff.returncode == 0:
        log("  - no changes")
        return
    subprocess.run(
        ["git", "-c", "user.name=mandoo", "-c", "user.email=goodmanddo@gmail.com",
         "-C", str(SITE), "commit", "-m", f"feat(ai-project): 주간 회고 {week_label} 자동 발행"],
        check=True, env=env,
    )
    subprocess.run(["git", "-C", str(SITE), "push"], check=True, env=env)
    log(f"  ✓ git push: weekly {week_label}")


# ---------- main ----------
def main(target_date: str | None = None):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("! ANTHROPIC_API_KEY 미설정")
        sys.exit(1)

    target = date.fromisoformat(target_date) if target_date else date.today()
    start, end, week_label = get_week_range(target)
    log(f"주간 회고 생성: {week_label} ({start} ~ {end})")

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WEEKLY_DIR / f"{week_label}.html"

    trades = load_trades(start, end)
    trades_agg = aggregate_trades(trades)
    nav_arr, nav_start, nav_end = load_nav_week(start, end)
    picks = load_picks(start, end)
    signal = load_signal()
    stats = load_stats()

    if not nav_arr:
        log(f"! NAV 데이터 없음 ({start}~{end}) — 발행 스킵")
        return

    ctx = build_context(start, end, trades_agg, nav_arr, nav_start, nav_end, picks, stats, signal)
    log("Claude 코멘트 생성 중...")
    comment = gen_comment(ctx)

    html = render_page(start, end, week_label, trades_agg, nav_arr, nav_start, nav_end,
                       picks, stats, comment, signal)
    out_path.write_text(html)
    log(f"✓ 페이지 저장: {out_path.relative_to(SITE)}")

    week_ret = (nav_end / nav_start - 1) * 100 if nav_start else 0
    update_weekly_index(week_label, comment.get("headline", ""), week_ret)
    update_sitemap(week_label)

    try:
        git_push(week_label)
    except Exception as e:
        log(f"! git 실패: {e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (생략시 오늘)")
    args = ap.parse_args()
    main(args.date)
