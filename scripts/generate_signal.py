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
HISTORY_FILE = ROOT / "tools" / "data" / "history.json"
LOG_FILE = ROOT / "scripts" / "generate_signal.log"

_KEY_FILE = Path.home() / "stock_auto_trade" / ".anthropic_key"
if _KEY_FILE.exists():
    ANTHROPIC_KEY = _KEY_FILE.read_text().strip()
else:
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# US 섹터 ETF → 섹터명 매핑
US_SECTOR_ETFS = [
    ("SOXX", "반도체"),
    ("XLK", "기술/테크"),
    ("XLF", "금융"),
    ("XLE", "에너지"),
    ("XLV", "헬스케어"),
    ("XBI", "바이오테크"),
    ("XLI", "산업/방산"),
    ("XLU", "유틸리티"),
    ("XLY", "임의소비"),
    ("XLP", "필수소비"),
    ("ITA", "방산/항공우주"),
    ("TAN", "태양광/친환경"),
    ("LIT", "2차전지/리튬"),
    ("XME", "메탈/철강"),
]

# KR 핵심 30종목 → 섹터 매핑 (history.json 기준)
KR_SECTOR_MAP = {
    "005930": ("삼성전자", "반도체"),
    "000660": ("SK하이닉스", "반도체"),
    "009150": ("삼성전기", "반도체"),
    "035420": ("NAVER", "기술/테크"),
    "035720": ("카카오", "기술/테크"),
    "005380": ("현대차", "임의소비"),
    "000270": ("기아", "임의소비"),
    "012330": ("현대모비스", "임의소비"),
    "373220": ("LG에너지솔루션", "2차전지/리튬"),
    "006400": ("삼성SDI", "2차전지/리튬"),
    "086520": ("에코프로", "2차전지/리튬"),
    "247540": ("에코프로비엠", "2차전지/리튬"),
    "068270": ("셀트리온", "바이오테크"),
    "207940": ("삼성바이오로직스", "바이오테크"),
    "196170": ("알테오젠", "바이오테크"),
    "005490": ("POSCO홀딩스", "메탈/철강"),
    "105560": ("KB금융", "금융"),
    "055550": ("신한지주", "금융"),
    "086790": ("하나금융지주", "금융"),
    "316140": ("우리금융지주", "금융"),
    "323410": ("카카오뱅크", "금융"),
    "012450": ("한화에어로스페이스", "방산/항공우주"),
    "034020": ("두산에너빌리티", "산업/방산"),
    "329180": ("HD현대중공업", "산업/방산"),
    "036570": ("엔씨소프트", "기술/테크"),
    "259960": ("크래프톤", "기술/테크"),
    "352820": ("하이브", "임의소비"),
    "015760": ("한국전력", "유틸리티"),
    "017670": ("SK텔레콤", "유틸리티"),
    "030200": ("KT", "유틸리티"),
}


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


def fetch_us_sector_strength():
    """전일 US 섹터 ETF 등락률 → 강한 섹터 식별"""
    import FinanceDataReader as fdr

    start = (date.today() - timedelta(days=10)).isoformat()
    sectors = []
    for etf, sector_name in US_SECTOR_ETFS:
        try:
            df = fdr.DataReader(etf, start)
            if df is not None and len(df) >= 2:
                c = float(df["Close"].iloc[-1])
                p = float(df["Close"].iloc[-2])
                pct = round((c / p - 1) * 100, 2)
                sectors.append({"etf": etf, "sector": sector_name, "change_pct": pct})
        except Exception as e:
            log(f"섹터 ETF {etf} 수집 실패: {e}")
    sectors.sort(key=lambda x: -x["change_pct"])
    return sectors


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

def load_yesterday_picks():
    """전일 signal.json에서 impacted_kr_stocks 종목 추출 (다양성 위해 제외용)"""
    if not SIGNAL_FILE.exists():
        return []
    try:
        prev = json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
        return [s.get("name", "") for s in prev.get("us_market", {}).get("impacted_kr_stocks", []) if s.get("name")]
    except Exception:
        return []


def load_core_stock_pool():
    """history.json 30개 핵심 종목 후보 풀"""
    if not HISTORY_FILE.exists():
        return []
    try:
        d = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return [{"name": v["name"], "code": k} for k, v in d.get("stocks", {}).items()]
    except Exception:
        return []


def generate_signal_with_ai(us_indices, fx_rate, fx_change, volume_stocks, news, yesterday_picks, core_pool, us_sectors):
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

    # 미국 섹터 강도 (정렬됨, 강한 순)
    sector_text = ""
    if us_sectors:
        for s in us_sectors:
            sector_text += f"- {s['sector']}({s['etf']}): {s['change_pct']:+.2f}%\n"
    else:
        sector_text = "(섹터 데이터 없음)"

    # 강한 섹터 후보 (상위 2개)
    top_sectors = [s["sector"] for s in us_sectors[:2]] if us_sectors else []
    top_sectors_text = " / ".join(top_sectors) if top_sectors else "(없음)"

    # KR 후보 풀 — 섹터별 그룹핑
    sector_groups = {}
    for code, (name, sector) in KR_SECTOR_MAP.items():
        sector_groups.setdefault(sector, []).append(f"{name}({code})")
    pool_text = ""
    for sector, names in sector_groups.items():
        pool_text += f"- [{sector}] {', '.join(names)}\n"

    # 어제 픽
    yesterday_text = ", ".join(yesterday_picks) if yesterday_picks else "(없음)"

    # 뉴스
    news_text = "\n".join(f"- {n}" for n in news) if news else "뉴스 수집 실패"

    prompt = f"""당신은 manddo.kr의 AI 야간 시그널 생성기입니다.
오늘 날짜: {today_str}

## 전일 미국 시장
{us_text}

## 전일 미국 섹터 ETF 강도 (강한 순)
{sector_text}

## 전일 한국 거래량 급증 종목 (거래대금 상위 중)
{vol_text}

## 최신 증권 뉴스 헤드라인
{news_text}

## 한국 종목 후보 풀 (섹터별)
{pool_text}

## 어제 선정된 종목 (참고용)
{yesterday_text}

## 요청
아래 JSON 구조로 정확히 응답해주세요. 다른 텍스트 없이 JSON만.

### 핵심 원칙 (반드시 따라줘)
- **섹터 깊이 우선**: 어제 미국에서 가장 강했던 섹터 1~2개("{top_sectors_text}") 중심으로, 한국 동일 섹터에서 종목 3개를 1안/2안/3안으로 골라.
- **같은 섹터 깊이 파기**: 예) 반도체 섹터가 강하면 → 삼성전자(1안), SK하이닉스(2안), 삼성전기(3안) 식으로 같은 섹터에서 깊게.
- **섹터 분산은 예외**: 상위 섹터 등락이 모두 ±0.5% 이내로 애매하거나, 강한 섹터에 한국 매핑 종목이 부족할 때만 다른 섹터로 분산.
- **어제와 같은 종목 OK**: 미국에서 같은 섹터가 계속 강세면 같은 종목 다시 고르는 것이 자연스러움. 억지로 바꾸지 마.
- **인사이트 연결**: 각 종목 trigger·ai_view에 **반드시 미국 섹터 강도와의 연결고리** 명시 (예: "미국 SOXX +2.3%로 반도체 강세 → 삼성전자 동조").

1. **us_market**: 미 증시 요약 + impacted_kr_stocks 3개 (1안/2안/3안)
   - 반드시 위 "한국 종목 후보 풀"에서만 선택 (거래량 급증 종목도 가능)
   - 각 종목에 stance: "watch"(관망), "small_entry"(소액진입), "avoid"(회피) 중 하나
   - 각 종목에 sector(섹터명), rank(1/2/3) 필드 추가

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
    "sector_focus": "오늘 집중 섹터 (예: 반도체)",
    "impacted_kr_stocks": [
      {{
        "name": "종목명",
        "code": "000000",
        "slug": "종목코드또는영문슬러그",
        "sector": "섹터명",
        "rank": 1,
        "trigger": "미국 섹터 ETF 변동률 등 구체 인사이트와 연결한 영향 요인",
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

    log("미국 섹터 ETF 강도 수집...")
    us_sectors = fetch_us_sector_strength()
    if us_sectors:
        top3 = us_sectors[:3]
        log(f"강한 섹터 TOP3: {[(s['sector'], s['change_pct']) for s in top3]}")

    log("어제 픽 + 후보 풀 로드...")
    yesterday_picks = load_yesterday_picks()
    core_pool = load_core_stock_pool()
    log(f"어제 픽: {yesterday_picks} / 후보 풀: {len(core_pool)}종목")

    # 2. AI 분석
    log("Claude AI 분석 생성 중...")
    signal_data = generate_signal_with_ai(
        us_indices, fx_rate, fx_change, volume_stocks, news,
        yesterday_picks, core_pool, us_sectors
    )

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
