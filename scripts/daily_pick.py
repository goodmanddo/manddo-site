#!/usr/bin/env python3
"""
매일 08:30 실행 — "오늘의 차트 분석" 종목 선정 & 발행 자동화

1. 전날 trade_log.jsonl에서 AI 매수 성공 종목 중 수익 best 1개 선정
2. stock/_manifest.json에 차트분석이 없으면 Claude API로 생성 → 발행
3. stock/index.html 상단에 "오늘의 차트 분석" 하이라이트 삽입
4. git push → Cloudflare 자동 배포
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, str(Path.home() / "stock_auto_trade"))

ROOT = Path.home() / "manddo-site"
STOCK_DIR = ROOT / "stock"
MANIFEST = STOCK_DIR / "_manifest.json"
INDEX_HTML = STOCK_DIR / "index.html"
TRADE_LOG = Path.home() / "stock_auto_trade" / "trade_log.jsonl"
CHART_SOURCE = Path.home() / "주식차트"

# Claude API (키는 환경변수 또는 stock_auto_trade/config에서)
import anthropic
_KEY_FILE = Path.home() / "stock_auto_trade" / ".anthropic_key"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY") or (
    _KEY_FILE.read_text().strip() if _KEY_FILE.exists() else ""
)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# KIS API (현재가 조회용)
from kis_api import KISApi


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return []


def get_best_pick_from_trades():
    """전날 AI 매수 성공 종목 중 현재 수익률 best 1개 선정"""
    if not TRADE_LOG.exists():
        return None

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # 최근 3일까지 확장 (어제 매매 없을 수 있으므로)
    lookback_dates = [
        (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(1, 4)
    ]

    buy_records = []
    for line in TRADE_LOG.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("result") != "success" or entry.get("action") != "BUY":
            continue

        ts = entry.get("timestamp", "")[:10]
        if ts not in lookback_dates:
            continue

        # AI 매수만 (단타 신호 자동매수 제외)
        reason = entry.get("reason", "")
        if "AI" in reason or "합의" in reason or "돌파" in reason:
            buy_records.append(entry)

    if not buy_records:
        # AI 매수가 없으면 일반 매수도 포함
        for line in TRADE_LOG.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("result") != "success" or entry.get("action") != "BUY":
                continue
            ts = entry.get("timestamp", "")[:10]
            if ts in lookback_dates:
                buy_records.append(entry)

    if not buy_records:
        print("[오늘의 차트] 최근 3일 매수 기록 없음")
        return None

    # 현재가 조회해서 수익률 계산
    api = KISApi()
    best = None
    best_rate = -999

    for rec in buy_records:
        code = rec.get("code", "")
        name = rec.get("name", "")
        buy_price = rec.get("price", 0)
        if not code or buy_price <= 0:
            continue
        try:
            current = api.get_current_price(code)
            cur_price = current.get("price", 0)
            if cur_price <= 0:
                continue
            rate = (cur_price - buy_price) / buy_price * 100
            if rate > best_rate:
                best_rate = rate
                best = {
                    "code": code,
                    "name": name,
                    "buy_price": buy_price,
                    "current_price": cur_price,
                    "profit_rate": round(rate, 2),
                    "reason": rec.get("reason", ""),
                    "strategy": rec.get("strategy", "swing"),
                }
        except Exception as e:
            print(f"  현재가 조회 실패: {name}({code}) - {e}")

    if best:
        print(f"[오늘의 차트] 선정: {best['name']}({best['code']}) 수익률 {best['profit_rate']:+.2f}%")
    return best


def has_chart_report(code):
    """manifest에서 해당 종목코드의 차트분석이 있는지 확인"""
    manifest = load_manifest()
    for item in manifest:
        if item.get("code") == code:
            slug = item.get("slug", "")
            report_path = STOCK_DIR / f"{slug}.html"
            if report_path.exists():
                return slug
    # 코드로 직접 파일 확인
    if (STOCK_DIR / f"{code}.html").exists():
        return code
    return None


def generate_chart_analysis(code, name):
    """Claude API로 차트분석 HTML 생성"""
    print(f"[차트생성] {name}({code}) 분석 HTML 생성 중...")

    # KIS API로 데이터 수집
    api = KISApi()
    import FinanceDataReader as fdr

    start_date = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)

    if df is None or len(df) < 60:
        print(f"[차트생성] 데이터 부족: {name}")
        return None

    # 최근 60일 데이터 요약
    recent = df.tail(60)
    price_data = {
        "현재가": int(recent["Close"].iloc[-1]),
        "시가": int(recent["Open"].iloc[-1]),
        "고가": int(recent["High"].iloc[-1]),
        "저가": int(recent["Low"].iloc[-1]),
        "거래량": int(recent["Volume"].iloc[-1]),
        "60일고가": int(recent["High"].max()),
        "60일저가": int(recent["Low"].min()),
        "5일평균": int(recent["Close"].rolling(5).mean().iloc[-1]),
        "20일평균": int(recent["Close"].rolling(20).mean().iloc[-1]),
        "60일평균": int(recent["Close"].rolling(60).mean().iloc[-1]),
    }

    if len(df) >= 120:
        price_data["120일평균"] = int(df["Close"].rolling(120).mean().iloc[-1])

    # RSI
    delta = recent["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    price_data["RSI"] = round(rsi.iloc[-1], 1)

    # 최근 10일 종가
    price_data["최근10일종가"] = recent["Close"].tail(10).tolist()

    prompt = f"""'{name}'({code}) 종목의 세력·차트 분석 HTML을 생성해주세요.

시세 데이터:
{json.dumps(price_data, ensure_ascii=False, indent=2)}

기존 분석 HTML 형식을 반드시 따라주세요:
- DOCTYPE html, lang="ko"
- <title>{name} 세력·차트 분석</title>
- 메타 태그 필수: stock-name="{name}", stock-code="{code}"
- 스타일은 인라인 <style>로 포함 (외부 CSS 없음)
- .wrap {{max-width:740px}} 레이아웃
- 섹션(.sec): 기업 개요, 차트 구조 분석, 세력 흐름 분석, 매물대·지지/저항, 기술적 신호 점수판, 종합 시나리오
- .card, .g2, .g3, .met (메트릭), .tag (태그) 클래스 사용
- 색상: 상승 #A32D2D, 하락 #0C447C, 중립 #888780
- 폰트: -apple-system, 'Noto Sans KR'
- 실제 데이터 기반으로 분석 (가짜 데이터 X)

HTML만 출력하세요. 설명이나 마크다운 없이 순수 HTML만."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    html = response.content[0].text.strip()
    # 마크다운 코드블록 제거
    if html.startswith("```"):
        html = html.split("\n", 1)[1]
        if html.endswith("```"):
            html = html[:-3]

    # 소스 디렉토리에 저장 (publish_stock.py가 발행)
    filename = f"{name}_{code}_analysis.html"
    output_path = CHART_SOURCE / "완료" / filename
    output_path.write_text(html, encoding="utf-8")
    print(f"[차트생성] 저장: {output_path}")

    return filename


def publish_new_report():
    """publish_stock.py 실행하여 새 리포트 발행"""
    script = ROOT / "scripts" / "publish_stock.py"
    result = subprocess.run(
        [sys.executable, str(script), "--replace"],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(f"[발행 에러] {result.stderr[:500]}")


def update_today_pick(pick, slug):
    """stock/index.html에 '오늘의 차트 분석' 하이라이트 섹션 삽입/갱신"""
    text = INDEX_HTML.read_text(encoding="utf-8")

    profit_color = "#A32D2D" if pick["profit_rate"] > 0 else "#0C447C"
    strategy_label = "스윙" if pick["strategy"] == "swing" else "단타"

    highlight_html = f"""<!-- TODAY_PICK_START -->
  <div class="today-pick" style="background:linear-gradient(135deg,#1B64DA 0%,#0d47a1 100%);border-radius:16px;padding:28px 24px;margin-bottom:32px;color:#fff;position:relative;overflow:hidden">
    <div style="position:absolute;top:-20px;right:-20px;width:120px;height:120px;background:rgba(255,255,255,0.08);border-radius:50%"></div>
    <div style="font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;opacity:0.8;margin-bottom:8px">🏆 오늘의 차트 분석</div>
    <a href="/stock/{slug}.html" style="color:#fff;text-decoration:none">
      <div style="font-size:24px;font-weight:800;margin-bottom:4px">{pick['name']}</div>
      <div style="font-size:13px;opacity:0.7;margin-bottom:16px">{pick['code']} · AI {strategy_label} 매수 종목</div>
      <div style="display:flex;gap:16px;flex-wrap:wrap">
        <div><div style="font-size:11px;opacity:0.6">매수가</div><div style="font-size:18px;font-weight:700">{pick['buy_price']:,}원</div></div>
        <div><div style="font-size:11px;opacity:0.6">현재가</div><div style="font-size:18px;font-weight:700">{pick['current_price']:,}원</div></div>
        <div><div style="font-size:11px;opacity:0.6">수익률</div><div style="font-size:18px;font-weight:700;color:{'#90EE90' if pick['profit_rate'] > 0 else '#FFB4B4'}">{pick['profit_rate']:+.2f}%</div></div>
      </div>
      <div style="margin-top:16px;font-size:13px;opacity:0.7">📊 세력·차트 구조 종합 분석 보기 →</div>
    </a>
  </div>
  <!-- TODAY_PICK_END -->"""

    # 기존 하이라이트 있으면 교체
    if "<!-- TODAY_PICK_START -->" in text:
        pattern = re.compile(
            r"<!-- TODAY_PICK_START -->.*?<!-- TODAY_PICK_END -->",
            re.DOTALL,
        )
        text = pattern.sub(highlight_html, text)
    else:
        # disclaimer 바로 뒤에 삽입
        marker = '</div>\n\n  <div class="grid"'
        text = text.replace(marker, f'</div>\n\n{highlight_html}\n\n  <div class="grid"', 1)

    INDEX_HTML.write_text(text, encoding="utf-8")
    print(f"[오늘의 차트] index.html 업데이트 완료: {pick['name']}")


def git_push():
    """변경사항 커밋 & 푸시"""
    res = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    )
    if not res.stdout.strip():
        print("[git] 변경사항 없음")
        return

    today = date.today().isoformat()
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"오늘의 차트 분석 업데이트 ({today})"],
        cwd=ROOT, capture_output=True, text=True,
    )
    res = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if res.returncode == 0:
        print("[git] push 완료")
    else:
        print(f"[git] push 실패: {res.stderr[:300]}")


def main():
    print(f"\n{'='*50}")
    print(f"오늘의 차트 분석 자동화 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    # 1. 전날 매수 종목 중 best 선정
    pick = get_best_pick_from_trades()
    if not pick:
        print("선정할 종목이 없습니다. 종료.")
        return

    code = pick["code"]
    name = pick["name"]

    # 2. 최신 데이터로 차트분석 항상 새로 생성 (기존 있어도 업데이트)
    slug = has_chart_report(code)
    print(f"[차트분석] {name}({code}) — 최신 데이터로 {'갱신' if slug else '신규 생성'}")
    generate_chart_analysis(code, name)
    publish_new_report()
    # 발행 후 slug 재확인
    slug = has_chart_report(code)
    if not slug:
        slug = code  # fallback

    print(f"[차트분석 완료] {name} → /stock/{slug}.html")

    # 3. index.html에 하이라이트 삽입
    update_today_pick(pick, slug)

    # 4. git push
    git_push()

    print("\n✅ 완료!")


if __name__ == "__main__":
    main()
