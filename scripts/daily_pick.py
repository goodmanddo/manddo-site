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
SIGNAL_JSON = ROOT / "ai-log" / "signal.json"
TODAY_PICK_JSON = ROOT / "ai-log" / "today_pick.json"
CHART_GUIDE = Path.home() / "Desktop" / "클로드 운영노트" / "차트분석가이드.md"
CHART_FILES = ROOT / "scripts" / ".chart_files.json"  # Files API file_id 매니페스트

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


def get_pick_from_signal():
    """야간 시그널(미증시 영향 종목) 1순위를 오늘의 픽으로 선정.

    선정 기준:
      1. small_entry / 진입 권장 stance 우선
      2. 동일 stance면 impacted_kr_stocks 배열 순서 (1안→2안→3안)
      3. 어제 픽과 동일 종목은 건너뛰고 같은 섹터 다음 후보로 회전
         (사용자 노출 다양성 + 섹터 깊이 의도 유지)
    """
    if not SIGNAL_JSON.exists():
        print("[오늘의 픽] signal.json 없음")
        return None
    try:
        sig = json.loads(SIGNAL_JSON.read_text())
    except Exception as e:
        print(f"[오늘의 픽] signal.json 파싱 실패: {e}")
        return None

    impacted = (sig.get("us_market") or {}).get("impacted_kr_stocks") or []
    if not impacted:
        print("[오늘의 픽] 미증시 영향 종목 없음")
        return None

    priority = {"small_entry": 0, "buy": 0, "watch": 1, "avoid": 2}
    sorted_list = sorted(
        enumerate(impacted),
        key=lambda x: (priority.get(x[1].get("stance", "watch"), 1), x[0]),
    )

    # 어제 픽 회전 — pick_history.jsonl 에서 today 이전 최근 코드 조회
    history_file = ROOT / "ai-log" / ".pick_history.jsonl"
    today_iso = date.today().isoformat()
    yesterday_code = None
    if history_file.exists():
        try:
            for line in reversed(history_file.read_text().strip().split("\n")):
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("date") and entry.get("date") < today_iso:
                    yesterday_code = entry.get("code")
                    break
        except Exception as e:
            print(f"[오늘의 픽] 히스토리 파싱 실패: {e}")

    chosen = None
    for _, cand in sorted_list:
        if yesterday_code and cand.get("code") == yesterday_code:
            print(f"[오늘의 픽] 어제 픽 {cand.get('name')}({yesterday_code}) 회전 — 건너뜀")
            continue
        chosen = cand
        break
    if chosen is None:
        # 후보 전부 어제와 동일 (드묾) — 그래도 1순위 사용
        chosen = sorted_list[0][1]

    # 히스토리 append (오늘 이미 있으면 마지막 줄을 갈음하기 위해 append만)
    try:
        with history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "date": today_iso,
                "code": chosen.get("code"),
                "name": chosen.get("name"),
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[오늘의 픽] 히스토리 기록 실패: {e}")
    code = chosen.get("code", "")
    name = chosen.get("name", "")
    if not code or not name:
        return None

    api = KISApi()
    cur_price = 0
    change_pct = 0.0
    try:
        cur = api.get_current_price(code)
        cur_price = int(cur.get("price") or 0)
        change_pct = float(cur.get("change_rate") or 0)
    except Exception as e:
        print(f"[오늘의 픽] 현재가 조회 실패: {e}")

    return {
        "code": code,
        "name": name,
        "slug": chosen.get("slug", "") or code,
        "trigger": chosen.get("trigger", ""),
        "ai_view": chosen.get("ai_view", ""),
        "stance": chosen.get("stance", "watch"),
        "current_price": cur_price,
        "change_pct": round(change_pct, 2),
        "us_summary": (sig.get("us_market") or {}).get("summary", ""),
        "generated_at": sig.get("generated_at") or date.today().isoformat(),
    }


def compute_pick_extras(pick):
    """today_pick.json에 들어갈 보조 필드 계산.

    /ai-voice/ 페이지가 의존하는 필드:
      - year_high, year_low: 52주 고저 (FinanceDataReader)
      - score_short, score_mid: 단기·중장기 매력도 0~100 (휴리스틱)
      - ai_pick_reason, counter_view: 한 마디 + 반론 (Claude Haiku)

    실패 시 None 반환 → save_today_pick_meta가 누락 필드 없이 dump.
    """
    code = pick.get("code", "")
    if not code:
        return None
    try:
        import FinanceDataReader as fdr
    except Exception as e:
        print(f"[오늘의 픽] FinanceDataReader 임포트 실패: {e}")
        return None

    try:
        start = (datetime.now() - timedelta(days=420)).strftime("%Y-%m-%d")
        df = fdr.DataReader(code, start)
        if df is None or len(df) < 60:
            return None
        y1 = df.tail(252) if len(df) >= 252 else df
        year_high = int(y1["High"].max())
        year_low = int(y1["Low"].min())
        cur = int(df["Close"].iloc[-1])
        pos52 = (
            (cur - year_low) / (year_high - year_low) * 100
            if year_high != year_low else 50
        )

        # 추세·모멘텀 보조 지표 (점수 산정용)
        close = df["Close"]
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = float((100 - 100 / (1 + gain / loss)).iloc[-1])
        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        bullish_align = ma5 > ma20 > ma60  # 정배열

        # 단기 매력도: RSI + 52주 위치 + 정배열 (0~100)
        score_short = 50
        if rsi < 30: score_short += 25
        elif rsi < 50: score_short += 12
        elif rsi > 75: score_short -= 20
        elif rsi > 65: score_short -= 8
        if pos52 < 30: score_short += 15
        elif pos52 > 80: score_short -= 12
        if bullish_align: score_short += 8
        score_short = max(0, min(100, score_short))

        # 중장기 매력도: 정배열 가중 + 추세 + 52주 위치
        score_mid = 55
        if bullish_align: score_mid += 18
        if cur > ma20: score_mid += 8
        if cur > ma60: score_mid += 8
        if pos52 < 40: score_mid += 8  # 저점 매력
        elif pos52 > 90: score_mid -= 5
        score_mid = max(0, min(100, score_mid))
    except Exception as e:
        print(f"[오늘의 픽] 가격 메타 계산 실패: {e}")
        return None

    # ai_pick_reason / counter_view: Claude Haiku로 짧게 생성
    ai_pick_reason = ""
    counter_view = ""
    try:
        prompt = (
            f"종목: {pick['name']}({code})\n"
            f"현재가 {cur:,}원, 52주 위치 {pos52:.1f}% (저가 {year_low:,} ~ 고가 {year_high:,})\n"
            f"RSI {rsi:.1f}, 정배열 {'O' if bullish_align else 'X'}\n"
            f"오늘 트리거: {pick.get('trigger', '')}\n"
            f"미증시 요약: {pick.get('us_summary', '')}\n"
            f"AI 시그널 코멘트: {pick.get('ai_view', '')}\n"
            f"stance: {pick.get('stance', 'watch')}\n\n"
            "위 종목에 대해 manddo.kr '오늘의 한 마디' 페이지에 보낼 두 단락을 한국어 JSON으로:\n"
            '{"ai_pick_reason": "<왜 이 종목을 보는가, 3~4문장, 친근한 ~요체>",'
            ' "counter_view": "<주의해야 할 반대 시각, 2~3문장, ~요체>"}\n'
            "JSON만 출력. 종목 매수 권유는 금지, 관찰·검토 톤 유지."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            obj = json.loads(m.group(0))
            ai_pick_reason = obj.get("ai_pick_reason", "")
            counter_view = obj.get("counter_view", "")
    except Exception as e:
        print(f"[오늘의 픽] 한 마디 생성 실패: {e}")

    return {
        "year_high": year_high,
        "year_low": year_low,
        "score_short": score_short,
        "score_mid": score_mid,
        "ai_pick_reason": ai_pick_reason,
        "counter_view": counter_view,
    }


def save_today_pick_meta(pick):
    """/ai-log/today_pick.json 으로 메타 dump (페이지에서 fetch).

    /ai-voice/ 가 기대하는 추가 필드(year_high/year_low/score_*/ai_pick_reason/counter_view)는
    compute_pick_extras로 채워서 합침. ai_view는 내부용이라 출력에서 제거.
    """
    extras = compute_pick_extras(pick) or {}
    out = {"date": date.today().isoformat()}
    # 기본 메타
    for key in ("code", "name", "slug", "stance", "trigger",
                "current_price", "change_pct"):
        if key in pick:
            out[key] = pick[key]
    # 보조 메타 (실패 시 빈 값 대신 키 자체를 누락시키지 않음)
    out["year_low"] = extras.get("year_low")
    out["year_high"] = extras.get("year_high")
    out["score_short"] = extras.get("score_short")
    out["score_mid"] = extras.get("score_mid")
    out["ai_pick_reason"] = extras.get("ai_pick_reason", "")
    out["counter_view"] = extras.get("counter_view", "")
    out["us_summary"] = pick.get("us_summary", "")
    out["generated_at"] = pick.get("generated_at", date.today().isoformat())

    TODAY_PICK_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(
        f"[오늘의 픽] today_pick.json 저장: {pick['name']}({pick['code']}) "
        f"52주 {out.get('year_low')}~{out.get('year_high')} "
        f"단기/중장기 {out.get('score_short')}/{out.get('score_mid')}"
    )


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
    """Claude API로 금호석유 수준의 고품질 차트분석 HTML 생성"""
    print(f"[차트생성] {name}({code}) 분석 HTML 생성 중...")

    # KIS API + FinanceDataReader로 데이터 수집
    api = KISApi()
    import FinanceDataReader as fdr
    from scanner import get_fundamentals, get_institutional_net_buy

    start_date = (datetime.now() - timedelta(days=500)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)

    if df is None or len(df) < 60:
        print(f"[차트생성] 데이터 부족: {name}")
        return None

    close = df["Close"]
    recent = df.tail(60)

    # 기본 시세
    price_data = {
        "현재가": int(close.iloc[-1]),
        "시가": int(df["Open"].iloc[-1]),
        "고가": int(df["High"].iloc[-1]),
        "저가": int(df["Low"].iloc[-1]),
        "거래량": int(df["Volume"].iloc[-1]),
        "평균거래량_20일": int(recent["Volume"].rolling(20).mean().iloc[-1]),
    }

    # 이동평균
    for n in [5, 20, 60]:
        if len(close) >= n:
            price_data[f"MA{n}"] = int(close.rolling(n).mean().iloc[-1])
    if len(close) >= 120:
        price_data["MA120"] = int(close.rolling(120).mean().iloc[-1])

    # 52주 고저
    y1 = df.tail(252) if len(df) >= 252 else df
    price_data["52주고가"] = int(y1["High"].max())
    price_data["52주저가"] = int(y1["Low"].min())
    price_data["52주위치"] = round(
        (close.iloc[-1] - y1["Low"].min()) / (y1["High"].max() - y1["Low"].min()) * 100, 1
    ) if y1["High"].max() != y1["Low"].min() else 50

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    price_data["RSI"] = round(rsi.iloc[-1], 1)

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    price_data["MACD"] = round(macd.iloc[-1], 1)
    price_data["MACD시그널"] = round(signal.iloc[-1], 1)

    # 볼린저밴드
    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    price_data["볼린저상단"] = int((ma20 + 2 * std20).iloc[-1])
    price_data["볼린저하단"] = int((ma20 - 2 * std20).iloc[-1])

    # 피보나치 되돌림 (52주 고저 기준)
    h, l = price_data["52주고가"], price_data["52주저가"]
    diff = h - l
    price_data["피보나치"] = {
        "23.6%": int(h - diff * 0.236),
        "38.2%": int(h - diff * 0.382),
        "50%": int(h - diff * 0.5),
        "61.8%": int(h - diff * 0.618),
        "78.6%": int(h - diff * 0.786),
    }

    # 최근 20일 일봉 (OHLCV)
    candles = []
    for _, row in df.tail(20).iterrows():
        candles.append({
            "O": int(row["Open"]), "H": int(row["High"]),
            "L": int(row["Low"]), "C": int(row["Close"]),
            "V": int(row["Volume"]),
        })
    price_data["최근20일봉"] = candles

    # 주봉 (최근 12주)
    weekly = df.resample("W").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna().tail(12)
    w_candles = []
    for _, row in weekly.iterrows():
        w_candles.append({"O": int(row["Open"]), "H": int(row["High"]), "L": int(row["Low"]), "C": int(row["Close"])})
    price_data["최근12주봉"] = w_candles

    # 펀더멘탈
    pbr, opm, op_amount = get_fundamentals(code)
    price_data["PBR"] = pbr
    price_data["영업이익률"] = opm
    price_data["영업이익"] = op_amount

    # 기관/외국인 수급
    inv = get_institutional_net_buy(code, days=5)
    price_data["기관순매수_5일"] = inv.get("inst_net", 0)
    price_data["외국인순매수_5일"] = inv.get("frgn_net", 0)

    # 차트 분석 가이드 로드 (단일 진실의 소스)
    guide_text = ""
    if CHART_GUIDE.exists():
        guide_text = CHART_GUIDE.read_text(encoding="utf-8")

    prompt = f"""'{name}'({code}) 종목의 세력·차트 종합 분석 HTML을 생성해주세요.
아래 시세 데이터와 일봉/주봉 캔들 데이터를 기반으로 실제 분석을 해주세요.

# 단일 진실의 소스: 차트 분석 가이드

아래 가이드를 반드시 따르세요. 모든 분석 용어·구조·체크리스트·금지사항이 이 가이드를 따라야 합니다.

```markdown
{guide_text}
```

# 종목 시세 데이터

{json.dumps(price_data, ensure_ascii=False, indent=2)}

반드시 아래 구조와 스타일을 정확히 따라주세요:

## 필수 HTML 구조

1. DOCTYPE html, lang="ko", <title>{name} 세력·차트 분석</title>
2. <meta name="stock-name" content="{name}">
   <meta name="stock-code" content="{code}">
3. 모든 CSS는 <style> 태그 안에 인라인으로 (외부 CSS 없음)
4. .wrap{{max-width:740px;margin:0 auto}}

## 필수 CSS 클래스 (그대로 사용)

*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Noto Sans KR',sans-serif;background:#f5f5f0;color:#2c2c2a;padding:16px;font-size:14px}}
.sec{{font-size:13px;font-weight:700;margin:18px 0 8px;padding-bottom:4px;border-bottom:1px solid #d3d1c7}}
.card{{background:#fff;border:1px solid #d3d1c7;border-radius:10px;padding:12px 14px;margin-bottom:8px}}
.g2/.g3/.g4 = grid 레이아웃 (2/3/4열)
.met = 메트릭 박스 (.ml=라벨, .mv=값, .ms=보조텍스트)
.tag = 인라인 태그 (.t-r=빨강, .t-g=녹색, .t-b=파랑, .t-a=주황, .t-navy=네이비, .t-teal=틸)
.zone = 세력 분석 항목 (dot + 설명)
.row = 체크리스트 행
.rr/.rn = 이유 설명 행 (번호박스 + 설명)
.info-box(녹색), .warn-box(주황), .danger-box(빨강), .navy-box(네이비) = 강조 박스
.score-wrap/.score-fill = 점수 프로그레스바
.strategy-box = 매수전략 박스 (.sp=가격)
.chart-area = SVG 차트 영역

## 필수 섹션 (순서대로)

1. **navy-box 요약**: 한 줄 핵심 포인트 + 구조적 강점/리스크 2컬럼 그리드
2. **📊 현재 주가 현황**: .g3 메트릭(현재가, 52주 범위, 52주 위치) + 52주 위치 바 (SVG gradient) + 태그들
3. **📈 일봉 차트 모양**: .rr/.rn으로 "이유①②③④" 형식. 차트가 왜 이런 모양인지 설명
   - chart-area에 SVG 캔들차트 (viewBox="0 0 680 225") — 실제 데이터 기반 캔들 + MA선 + 주요 가격선 + 구간 표시
4. **📈 주봉 차트 모양**: 동일 형식. 엘리어트 파동/추세대 관점 분석 + SVG 주봉 차트
5. **📈 월봉 차트 모양**: 장기 추세 분석 + SVG 월봉 차트 (데이터 없으면 생략 가능)
6. **🧲 세력 관점 분석**: .zone으로 수급 판독, 구조적 경쟁력, 실적/펀더멘탈, 핵심 리스크 4개
7. **✅ 체크리스트 & 📅 카탈리스트**: .row로 강점/약점 각 5개+ 나열 (태그 포함)
8. **🎯 매수 전략 종합**: .g2로 단기/중장기 매력도 점수 + 전략 → .g4로 조정매수/핵심/목표가/손절 4박스 → info-box 핵심 4포인트 + danger-box 리스크

## SVG 차트 규칙

- 실제 데이터 기반으로 캔들 위치 계산 (가짜 데이터 X)
- 상승 캔들: fill="#1D9E75", 하락 캔들: fill="#E24B4A", 현재: fill="#888780"
- 주요 가격선: stroke-dasharray="4 3", 라벨 포함
- MA선: path로 곡선, opacity=".5"
- 구간별 배경 rect (상승구간=#EAF3DE, 하락구간=#FCEBEB 등) + 라벨
- 매수/매도 구간 하이라이트
- 하단에 한 줄 요약 텍스트

## 핵심 분석 포인트 (PDF 기술적 분석 이론 적용)

- **지지/저항**: 이전 고점이 지지로 전환, 저점이 저항으로 전환 여부
- **추세선/추세대**: 상승추세 저점 연결, 하락추세 고점 연결
- **이동평균선 밀집도**: 정배열/역배열/밀집 분석
- **거래량으로 세력활동 판독**: 거래량 급증=세력 진입/이탈, 급감=관망
- **피보나치 되돌림**: 주요 되돌림 비율에서 지지/저항 확인
- **패턴 분석**: 쌍봉/쌍바닥, 삼각수렴, 플래그, 헤드앤숄더 등
- **엘리어트 파동**: 파동 카운팅, 현재 위치 추정
- **볼린저밴드**: 밴드폭 수축/확장, 상하단 접근
- **RSI/MACD**: 과매수/과매도, 다이버전스

색상 규칙: 상승=#A32D2D(빨강계), 하락=#0C447C(파랑계), 중립=#888780, 긍정=#1D9E75(녹색), 경고=#EF9F27(주황)

HTML만 출력하세요. 마크다운 코드블록(```)이나 설명 없이 <!DOCTYPE html>부터 </html>까지만."""

    # 2026-06-08 비용 최소화: PDF 첨부 제거(매일 cold cache로 풀입력 700K 청구되던 주범)
    # 차트분석 이론은 위 prompt에 인라인 포함됨. Files API 비활성.
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,             # 2026-06-08 8000→4000 (출력도 더 압축)
        messages=[{"role": "user", "content": prompt}],
    )
    # 캐시 효과 로깅
    u = response.usage
    print(
        f"[차트생성] 토큰 — input:{u.input_tokens} "
        f"cache_create:{getattr(u, 'cache_creation_input_tokens', 0)} "
        f"cache_read:{getattr(u, 'cache_read_input_tokens', 0)} "
        f"output:{u.output_tokens}"
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
    """stock/index.html에 '오늘의 차트 분석' 하이라이트 섹션 삽입/갱신.

    시그널 기반 픽: 미국 시장 인사이트로 도출된 종목을 강조.
    """
    text = INDEX_HTML.read_text(encoding="utf-8")

    stance_label = {
        "small_entry": "🟢 소액 진입 권장",
        "buy": "🟢 매수 검토",
        "watch": "🟡 관망",
        "avoid": "🔴 진입 주의",
    }.get(pick.get("stance", "watch"), "🟡 관망")

    cur_price_html = f"{pick['current_price']:,}원" if pick.get("current_price") else "—"
    change_pct = pick.get("change_pct", 0)
    change_color = "#90EE90" if change_pct > 0 else ("#FFB4B4" if change_pct < 0 else "#cbd5e1")
    change_html = f"{change_pct:+.2f}%" if change_pct else "—"
    trigger = (pick.get("trigger") or "").replace("\n", " ")

    highlight_html = f"""<!-- TODAY_PICK_START -->
  <div class="today-pick" style="background:linear-gradient(135deg,#1B64DA 0%,#0d47a1 100%);border-radius:16px;padding:28px 24px;margin-bottom:32px;color:#fff;position:relative;overflow:hidden">
    <div style="position:absolute;top:-20px;right:-20px;width:120px;height:120px;background:rgba(255,255,255,0.08);border-radius:50%"></div>
    <div style="font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;opacity:0.85;margin-bottom:8px">🏆 오늘의 픽 · 미국 시장 인사이트</div>
    <a href="/stock/{slug}.html" style="color:#fff;text-decoration:none;display:block">
      <div style="font-size:24px;font-weight:800;margin-bottom:4px">{pick['name']}</div>
      <div style="font-size:13px;opacity:0.75;margin-bottom:14px">{pick['code']} · {stance_label}</div>
      <div style="font-size:13.5px;line-height:1.6;opacity:0.95;margin-bottom:14px;background:rgba(255,255,255,0.10);padding:10px 12px;border-radius:8px">💡 {trigger}</div>
      <div style="display:flex;gap:18px;flex-wrap:wrap">
        <div><div style="font-size:11px;opacity:0.6">현재가</div><div style="font-size:18px;font-weight:700">{cur_price_html}</div></div>
        <div><div style="font-size:11px;opacity:0.6">전일 대비</div><div style="font-size:18px;font-weight:700;color:{change_color}">{change_html}</div></div>
      </div>
      <div style="margin-top:16px;font-size:13px;opacity:0.85">📊 세력·차트 구조 종합 분석 보기 →</div>
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

    # 1. 야간 시그널(미증시 영향 종목)에서 오늘의 픽 선정
    pick = get_pick_from_signal()
    if not pick:
        print("[fallback] signal 기반 픽 실패 — trade_log fallback 시도")
        legacy = get_best_pick_from_trades()
        if not legacy:
            print("선정할 종목이 없습니다. 종료.")
            return
        # legacy 포맷을 시그널 포맷으로 변환
        pick = {
            "code": legacy["code"], "name": legacy["name"],
            "slug": legacy["code"],
            "trigger": legacy.get("reason", ""),
            "ai_view": "AI 매매 종목 (시그널 미생성으로 fallback).",
            "stance": "watch",
            "current_price": legacy["current_price"],
            "change_pct": legacy["profit_rate"],
            "us_summary": "",
            "generated_at": date.today().isoformat(),
        }

    save_today_pick_meta(pick)

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
