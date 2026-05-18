#!/usr/bin/env python3
"""
바닥종목 스크리너 — 쌍바닥/3바닥 + 거래량 증가 종목을 모아 JSON으로 저장.

판정(엄격도 '보통'):
  - 최근 ~7개월 일봉에서 저점(pivot low) 추출
  - 비슷한 가격대(±5%)의 저점 2개=쌍바닥 / 3개=3바닥
  - 저점 사이 반등(넥라인)이 평균바닥 대비 +7% 이상
  - 최근 7거래일 평균 거래량 ≥ 바닥 구간 평균 거래량 ×1.3 (매집/돌파 신호)
  - 현재가가 마지막 저점 위로 회복 중 (넥라인 +5% 이내까지 허용 = 갓 돌파 포함)
  - 마지막 저점이 최근 40거래일 이내 (실전 유효성)

출력: ~/manddo-site/tools/data/bottom.json
실행: 평일 장마감 후 1회 (LaunchAgent com.mandoo.bottom-collect)
매수·매도 권유 아님. 휴리스틱 스크리너.
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "tools" / "data" / "bottom.json"

MIN_MARCAP = 1_000 * 1e8       # 시총 1,000억 이상 (배당주와 동일 기준)
LOOKBACK_DAYS = 240            # 일봉 조회 기간 (캘린더일)
PIVOT_ORDER = 5                # 좌우 5봉보다 낮으면 저점
LEVEL_TOL = 0.05               # 바닥끼리 가격차 허용 ±5%
REBOUND_MIN = 0.07             # 바닥 사이 반등 최소 +7%
VOL_MULT = 1.3                 # 최근 거래량 / 바닥구간 거래량 ≥ 1.3
RECENT_N = 7                   # 최근 거래량 평균 윈도우
NECK_OVERSHOOT = 0.05          # 넥라인 +5% 위까지는 '갓 돌파'로 포함
MAX_BOTTOM_AGE = 40            # 마지막 저점이 최근 40거래일 이내


def pivot_lows(low: np.ndarray, order: int) -> list[int]:
    """좌우 order봉보다 낮거나 같은 지점의 인덱스"""
    n = len(low)
    out = []
    for i in range(order, n - order):
        seg = low[i - order:i + order + 1]
        if low[i] == seg.min():
            if out and i - out[-1] < order:   # 너무 붙은 저점 dedupe
                if low[i] < low[out[-1]]:
                    out[-1] = i
                continue
            out.append(i)
    return out


def detect(df: pd.DataFrame) -> dict | None:
    """쌍바닥/3바닥 + 거래량 판정. 해당되면 패턴 정보 dict, 아니면 None"""
    if len(df) < PIVOT_ORDER * 2 + 30:
        return None
    low = df["Low"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    vol = df["Volume"].to_numpy(dtype=float)
    close = float(df["Close"].iloc[-1])

    piv = pivot_lows(low, PIVOT_ORDER)
    if len(piv) < 2:
        return None

    n = len(df)
    # 3바닥 우선, 안되면 쌍바닥. 최근 저점부터 역순으로 후보 구성.
    for k in (3, 2):
        if len(piv) < k:
            continue
        bidx = piv[-k:]
        if n - 1 - bidx[-1] > MAX_BOTTOM_AGE:
            continue
        bprices = low[bidx]
        mean_b = bprices.mean()
        if (bprices.max() - bprices.min()) / mean_b > LEVEL_TOL:
            continue
        if any(bidx[j + 1] - bidx[j] < PIVOT_ORDER for j in range(len(bidx) - 1)):
            continue
        # 저점 사이마다 반등(넥라인) 확인
        ok = True
        for j in range(len(bidx) - 1):
            seg_hi = high[bidx[j] + 1:bidx[j + 1]].max() if bidx[j + 1] - bidx[j] > 1 else 0
            if seg_hi < mean_b * (1 + REBOUND_MIN):
                ok = False
                break
        if not ok:
            continue
        neckline = float(high[bidx[0]:bidx[-1] + 1].max())
        # 거래량: 최근 7봉 평균 vs 바닥구간(첫저점~마지막저점) 평균
        base_vol = vol[bidx[0]:bidx[-1] + 1].mean()
        recent_vol = vol[-RECENT_N:].mean()
        if base_vol <= 0:
            continue
        vol_ratio = recent_vol / base_vol
        if vol_ratio < VOL_MULT:
            continue
        # 회복 조건: 마지막 저점 위 + 넥라인 +5% 이내
        low_price = float(bprices.min())
        if close <= low_price:
            continue
        if close > neckline * (1 + NECK_OVERSHOOT):
            continue
        return {
            "pattern": "3바닥" if k == 3 else "쌍바닥",
            "low_price": round(low_price),
            "neckline": round(neckline),
            "close": round(close),
            "from_low_pct": round((close / low_price - 1) * 100, 1),
            "to_neck_pct": round((neckline / close - 1) * 100, 1),
            "vol_ratio": round(vol_ratio, 2),
            "last_bottom": df.index[bidx[-1]].strftime("%Y-%m-%d"),
            "bottoms": len(bidx),
        }
    return None


def main():
    kospi = fdr.StockListing("KOSPI"); kospi["Market"] = "KOSPI"
    kosdaq = fdr.StockListing("KOSDAQ"); kosdaq["Market"] = "KOSDAQ"
    listing = pd.concat([kospi, kosdaq], ignore_index=True)
    listing = listing[listing["Marcap"].fillna(0) >= MIN_MARCAP]
    listing = listing.sort_values("Marcap", ascending=False).reset_index(drop=True)
    total = len(listing)
    start = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    print(f"[바닥] 스캔 시작 — 시총 1,000억 이상 {total}종목 (KOSPI+KOSDAQ)")

    rows = []
    for i, row in enumerate(listing.itertuples(), 1):
        code, name, market = row.Code, row.Name, row.Market
        try:
            df = fdr.DataReader(code, start)
        except Exception as e:
            print(f"  [{i:4d}/{total}] {name}({code}) 조회오류: {e}")
            continue
        time.sleep(0.15)
        if df is None or df.empty:
            continue
        try:
            hit = detect(df)
        except Exception as e:
            print(f"  [{i:4d}/{total}] {name}({code}) 판정오류: {e}")
            continue
        if not hit:
            continue
        hit.update({
            "code": code,
            "name": name,
            "market": market,
            "marcap_eok": round(int(row.Marcap) / 1e8, 0),
        })
        rows.append(hit)
        print(f"  [{i:4d}/{total}] ★ {name}({code},{market}) {hit['pattern']} "
              f"바닥대비+{hit['from_low_pct']}% 거래량{hit['vol_ratio']}배")

    # 거래량비 큰 순(매집 강도) → 넥라인 근접 순
    rows.sort(key=lambda r: (-r["vol_ratio"], r["to_neck_pct"]))
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "FinanceDataReader (일봉)",
        "criteria": "KOSPI+KOSDAQ 시총 ≥ 1,000억 · 쌍바닥/3바닥 + 거래량 ×1.3↑",
        "count": len(rows),
        "stocks": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n✓ {len(rows)}개 종목 저장 → {OUT.relative_to(ROOT)}")

    import subprocess
    try:
        subprocess.run(["git", "-C", str(ROOT), "add", str(OUT.relative_to(ROOT))], check=True)
        subprocess.run(
            ["git", "-C", str(ROOT), "commit", "-m", f"chore(bottom): refresh scan ({len(rows)}종목)"],
            check=True,
        )
        subprocess.run(["git", "-C", str(ROOT), "push"], check=True, timeout=30)
        print("✓ git push 완료")
    except subprocess.CalledProcessError as e:
        print(f"! git 작업 실패 (변경 없음일 수도): {e}")
    except Exception as e:
        print(f"! git 에러: {e}")


if __name__ == "__main__":
    sys.exit(main())
