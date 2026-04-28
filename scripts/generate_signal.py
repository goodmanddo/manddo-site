#!/usr/bin/env python3
"""
매일 06:00 실행 — 전날 미국 시장 분석 + 거래량 급증 종목 → signal.json 생성 → git push

signal.json 구조:
- us_market: 미 증시 요약 + 한국 영향 종목 3개 (stance: watch/small_entry/avoid)
- volume_surge: 거래량 폭증 TOP5 (stance 포함)
- ai_summary: 종합 AI 판단 1줄
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

ROOT = Path.home() / "manddo-site"
SIGNAL_FILE = ROOT / "ai-log" / "signal.json"
LOG_FILE = ROOT / "scripts" / "generate_signal.log"

_KEY_FILE = Path.home() / "stock_auto_trade" / ".anthropic_key"
if _KEY_FILE.exists():
    ANTHROPIC_KEY = _KEY_FILE.read_text().strip()
else:
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

HEADERS = {"User-Agent": "Mozilla/5.0"}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── 1. 미국 시장 데이터 수집 ──

def fetch_us_market():
    """FinanceDataReader로 전일 미 증시 + 환율 데이터 수집"""
    import FinanceDataReader as fdr
    import math

    start = (date.today() - timedelta(days=10)).isoformat()
    indices = {}

    for sym, name in [("DJI", "다우"), ("IXIC", "나스닥"), ("S&P500", "S&P 500")]:
        try:
            df = fdr.DataReader(sym, start)
            if df is not None and len(df) >= 2:
                c = df["Close"].iloc[-1]
                p = df["Close"].iloc[-2]
                pct = round((c / p - 1) * 100, 2)
                indices[name] = pct
        except Exception as e:
            log(f"미 지수 {name} 수집 실패: {e}")

    # 환율
    fx_rate = None
    fx_change = None
    try:
        df = fdr.DataReader("USD/KRW", start)
        if df is not None and len(df) >= 2:
            fx_rate = round(float(df["Close"].iloc[-1]), 1)
            prev = float(df["Close"].iloc[-2])
            if prev > 0:
                fx_change = round((fx_rate / prev - 1) * 100, 2)
                if math.isnan(fx_change):
                    fx_change = 0.0
    except Exception:
        pass

    return indices, fx_rate, fx_change


def fetch_volume_surge_stocks():
    """전일 거래량 급증 종목 (거래대금 상위에서)"""
    try:
        sys.path.insert(0, str(Path.home() / "stock_auto_trade"))
        import FinanceDataReader as fdr

        start = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
        results = []

        for market in ["KOSPI", "KOSDAQ"]:
            listing = fdr.StockListing(market)
            # 거래대금 상위 100
            listing_sorted = listing.sort_values("Amount", ascending=False).head(100)

            for _, row in listing_sorted.iterrows():
                try:
                    code = row["Code"]
                    name = row["Name"]
                    df = fdr.DataReader(code, start)
                    if df is None or len(df) < 22:
                        continue

                    vol = df["Volume"]
                    close = df["Close"]
                    vol_ma20 = vol.rolling(20).mean()

                    curr_vol = vol.iloc[-1]
                    curr_vol_ma = vol_ma20.iloc[-1]
                    if curr_vol_ma <= 0:
                        continue

                    vr = curr_vol / curr_vol_ma
                    if vr < 2.0:
                        continue

                    curr_close = close.iloc[-1]
                    prev_close = close.iloc[-2]
                    change = round((curr_close / prev_close - 1) * 100, 1)

                    # 종목 slug 생성
                    slug = code

                    results.append({
                        "name": name,
                        "code": code,
                        "slug": slug,
                        "volume_ratio": round(vr, 1),
                        "price_change_pct": change,
                    })
                except Exception:
                    continue

        # 거래량 비율 순 정렬
        results.sort(key=lambda x: -x["volume_ratio"])
        return results[:10]  # AI한테 10개 줘서 5개 선별하게 함

    except Exception as e:
        log(f"거래량 급증 종목 수집 실패: {e}")
        return []


def fetch_top_news():
    """네이버 증권 주요뉴스 헤드라인 5개"""
    try:
        url = "https://finance.naver.com/news/mainnews.naver"
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        headlines = []
        for a in soup.select("a"):
            title = a.get_text().strip()
            if len(title) > 15 and len(headlines) < 5:
                headlines.append(title[:80])
        return headlines
    except Exception:
        return []


# ── 2. Claude API로 분석 생성 ──

def generate_signal_with_ai(us_indices, fx_rate, fx_change, volume_stocks, news):
    """Claude API로 signal.json 내용 생성"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    today_str = date.today().strftime("%Y년 %m월 %d일")

    # 미 증시 요약
    us_text = ""
    for name, pct in us_indices.items():
        us_text += f"- {name}: {pct:+.2f}%\n"
    if fx_rate:
        fx_chg_str = f"{fx_change:+.2f}%" if fx_change is not None else "N/A"
        us_text += f"- USD/KRW: {fx_rate:.1f} ({fx_chg_str})\n"

    # 거래량 급증
    vol_text = ""
    for v in volume_stocks:
        vol_text += f"- {v['name']}({v['code']}): 거래량 {v['volume_ratio']}배, 등락 {v['price_change_pct']:+.1f}%\n"

    # 뉴스
    news_text = "\n".join(f"- {n}" for n in news) if news else "뉴스 수집 실패"

    prompt = f"""당신은 manddo.kr의 AI 야간 시그널 생성기입니다.
오늘 날짜: {today_str}

## 전일 미국 시장
{us_text}

## 전일 한국 거래량 급증 종목 (거래대금 상위 중)
{vol_text}

## 최신 증권 뉴스 헤드라인
{news_text}

## 요청
아래 JSON 구조로 정확히 응답해주세요. 다른 텍스트 없이 JSON만.

1. **us_market**: 미 증시 요약 + 한국에 영향줄 종목 3개 선별
   - impacted_kr_stocks: 미국 시장 흐름이 직접 영향을 줄 한국 종목 (반도체, 2차전지, 플랫폼 등)
   - 각 종목에 stance: "watch"(관망), "small_entry"(소액진입), "avoid"(회피) 중 하나

2. **volume_surge**: 위 거래량 급증 종목 중 의미 있는 5개 선별
   - 각각 context(왜 거래량이 터졌는지), ai_view(AI 판단), stance 포함

3. **ai_summary**: 전체 종합 판단 2~3문장

JSON 형식:
{{
  "us_market": {{
    "summary": "미 증시 한줄 요약 — 핵심 포인트.",
    "indices": [{{"name": "다우", "change_pct": 0.0}}, ...],
    "fx_usd_krw": 0.0,
    "fx_change_pct": 0.0,
    "impacted_kr_stocks": [
      {{
        "name": "종목명",
        "code": "000000",
        "slug": "종목코드또는영문슬러그",
        "trigger": "미국 시장 영향 요인",
        "ai_view": "AI 판단 1~2문장",
        "stance": "watch"
      }}
    ]
  }},
  "volume_surge": [
    {{
      "name": "종목명",
      "code": "000000",
      "slug": "",
      "volume_ratio": 0.0,
      "price_change_pct": 0.0,
      "context": "거래량 급증 이유",
      "ai_view": "AI 판단",
      "stance": "watch"
    }}
  ],
  "ai_summary": "종합 판단"
}}"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))

    # JSON 파싱
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError(f"JSON 파싱 실패: {text[:300]}")
    return json.loads(m.group(0))


# ── 3. 저장 & 배포 ──

def save_and_push(signal_data):
    """signal.json 저장 + git push"""
    signal_data["date"] = date.today().isoformat()
    signal_data["generated_at"] = datetime.now().strftime("%H:%M")

    SIGNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(signal_data, f, ensure_ascii=False, indent=2)

    log(f"signal.json 저장 완료 ({SIGNAL_FILE})")

    # ai-log/index.html 업데이트 (날짜 갱신)
    ai_index = ROOT / "ai-log" / "index.html"
    if ai_index.exists():
        html = ai_index.read_text()
        # 날짜 패턴 교체
        old_date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})\s*·\s*\d{2}:\d{2}\s*발행')
        new_date = f"{signal_data['date']} · {signal_data['generated_at']} 발행"
        if old_date_pattern.search(html):
            html = old_date_pattern.sub(new_date, html)
            ai_index.write_text(html)
            log("ai-log/index.html 날짜 갱신")

    # git push
    try:
        os.chdir(ROOT)
        subprocess.run(["git", "add", "ai-log/signal.json", "ai-log/index.html"], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"AI 야간 시그널 업데이트 ({signal_data['date']})"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True, timeout=30)
        log("git push 완료")
    except subprocess.CalledProcessError as e:
        log(f"git push 실패: {e}")
    except Exception as e:
        log(f"git 에러: {e}")


def main():
    if not ANTHROPIC_KEY:
        log("ANTHROPIC_API_KEY 없음 — 종료")
        sys.exit(1)

    # 주말이면 스킵
    if date.today().weekday() >= 5:
        log("주말 — 스킵")
        return

    log("=" * 40)
    log("AI 야간 시그널 생성 시작")

    # 1. 데이터 수집
    log("미 증시 데이터 수집...")
    us_indices, fx_rate, fx_change = fetch_us_market()
    log(f"미 증시: {us_indices}")

    log("거래량 급증 종목 수집...")
    volume_stocks = fetch_volume_surge_stocks()
    log(f"거래량 급증: {len(volume_stocks)}종목")

    log("뉴스 수집...")
    news = fetch_top_news()

    # 2. AI 분석
    log("Claude AI 분석 생성 중...")
    signal_data = generate_signal_with_ai(us_indices, fx_rate, fx_change, volume_stocks, news)

    # 3. 저장 & 배포
    save_and_push(signal_data)

    log("완료!")
    # 요약 출력
    us_stocks = signal_data.get("us_market", {}).get("impacted_kr_stocks", [])
    vol_stocks = signal_data.get("volume_surge", [])
    log(f"미국 영향 종목: {', '.join(s['name'] for s in us_stocks)}")
    log(f"거래량 TOP5: {', '.join(s['name'] for s in vol_stocks[:5])}")
    log(f"AI 종합: {signal_data.get('ai_summary', '')[:100]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적 오류: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
