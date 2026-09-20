#!/usr/bin/env python3
"""
배당주 스크리너 데이터 수집 — KOSPI 시총 상위 종목의 배당 정보를 모아 JSON으로 저장.

출력: ~/manddo-site/tools/data/dividend.json
실행: 주 1회 정도 수동 실행 (배당정책 자주 바뀌지 않음)
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "tools" / "data" / "dividend.json"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
MIN_MARCAP = 1_000 * 1e8  # 시총 1,000억원 이상만 (코스닥 소형주 노이즈 컷)
MIN_YIELD = 1.0   # 배당수익률 1% 이상만 포함
MAX_YIELD = 12.0  # 12% 초과는 일회성 특별배당·주가급락·데이터오류로 보고 제외


def parse_naver(code: str):
    """네이버 모바일 API(JSON)에서 배당수익률·PER·PBR 추출.

    구 방식(finance.naver.com 종목페이지 HTML의 .aside_invest_info 테이블)은
    네이버가 페이지를 JS 렌더로 바꾸면서 '배당수익률' 문자열이 사라져 전부 실패했다.
    → m.stock.naver.com 통합 API의 totalInfos(code: dividendYieldRatio/per/pbr)로 교체.
    값 예: '0.64%', '11.66배', '3.02배'.
    """
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    r = requests.get(url, headers={**UA, "Referer": "https://m.stock.naver.com/"}, timeout=10)
    if r.status_code != 200:
        return None
    key_map = {"dividendYieldRatio": "yield", "per": "per", "pbr": "pbr"}
    result = {"yield": None, "per": None, "pbr": None}
    for item in r.json().get("totalInfos", []):
        k = key_map.get(item.get("code"))
        if not k:
            continue
        m = re.search(r"-?[0-9]+(?:\.[0-9]+)?", (item.get("value") or "").replace(",", ""))
        if m:
            result[k] = float(m.group(0))
    return result


def main():
    kospi = fdr.StockListing("KOSPI")
    kospi["Market"] = "KOSPI"
    kosdaq = fdr.StockListing("KOSDAQ")
    kosdaq["Market"] = "KOSDAQ"
    listing = pd.concat([kospi, kosdaq], ignore_index=True)
    listing = listing[listing["Marcap"].fillna(0) >= MIN_MARCAP]
    listing = listing.sort_values("Marcap", ascending=False).reset_index(drop=True)
    total = len(listing)
    print(f"[배당] 수집 시작 — 시총 1,000억 이상 {total}종목 (KOSPI+KOSDAQ)")

    rows = []
    for i, row in enumerate(listing.itertuples(), 1):
        code = row.Code
        name = row.Name
        market = row.Market
        marcap = int(row.Marcap)
        close = int(row.Close) if row.Close else 0
        try:
            info = parse_naver(code)
        except Exception as e:
            print(f"  [{i:4d}/{total}] {name}({code}) 오류: {e}")
            continue
        time.sleep(0.4)
        if not info or info.get("yield") is None:
            continue
        yld = info["yield"]
        if yld < MIN_YIELD or yld > MAX_YIELD:
            continue
        rows.append({
            "code": code,
            "name": name,
            "market": market,
            "marcap_eok": round(marcap / 1e8, 0),
            "close": close,
            "dividend_yield": yld,
            "per": info.get("per"),
            "pbr": info.get("pbr"),
        })
        print(f"  [{i:4d}/{total}] {name}({code}, {market}) 배당 {yld:.2f}%, PER {info.get('per')}, PBR {info.get('pbr')}")

    rows.sort(key=lambda r: r["dividend_yield"], reverse=True)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "Naver Mobile API + FinanceDataReader",
        "criteria": f"KOSPI+KOSDAQ 시총 ≥ 1,000억, 배당수익률 {MIN_YIELD}~{MAX_YIELD}% (초과분은 일회성·오류로 제외)",
        "count": len(rows),
        "stocks": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n✓ {len(rows)}개 종목 저장 → {OUT.relative_to(ROOT)}")

    # git commit + push
    import subprocess
    try:
        subprocess.run(["git", "-C", str(ROOT), "add", str(OUT.relative_to(ROOT))], check=True)
        subprocess.run(
            ["git", "-C", str(ROOT), "commit", "-m", f"chore(dividend): refresh ranking ({len(rows)}종목)"],
            check=True,
        )
        subprocess.run(["git", "-C", str(ROOT), "push"], check=True, timeout=30)
        print("✓ git push 완료")
    except subprocess.CalledProcessError as e:
        print(f"! git 작업 실패 (변경 없음일 수도): {e}")
    except Exception as e:
        print(f"! git 에러: {e}")


if __name__ == "__main__":
    main()
