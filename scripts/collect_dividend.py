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
MIN_YIELD = 1.0  # 배당수익률 1% 이상만 포함


def parse_naver(code: str):
    """네이버 금융 종목 페이지에서 배당수익률·PER·PBR 추출

    `.aside_invest_info` 내부 테이블 구조:
      - 한 row의 th: "배당수익률 l 2025.12 ..." / "PER l EPS (2025.12) ..." / "PBR l BPS (2025.12) ..."
      - 같은 row의 td: "0.72 %" / "35.42 배 l 6,564 원" / "3.63 배 l 63,997 원"
    th의 첫 토큰만으로 라벨을 매칭한다 (날짜 2025.12 등 디스크립션 무시).
    """
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    r = requests.get(url, headers=UA, timeout=10)
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    info = soup.select_one(".aside_invest_info")
    if not info:
        return None

    result = {"yield": None, "per": None, "pbr": None}
    for tr in info.select("tr"):
        th = tr.select_one("th")
        td = tr.select_one("td")
        if not th or not td:
            continue
        # th 텍스트의 첫 단어(배당수익률 / PER / PBR)
        label = th.get_text(" ", strip=True).split()[0] if th.get_text(strip=True) else ""
        # td의 첫 숫자(소수 가능). "35.42 배 l 6,564 원" → 35.42
        td_text = td.get_text(" ", strip=True)
        m = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)", td_text)
        if not m:
            continue
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if label == "배당수익률":
            result["yield"] = val
        elif label == "PER":
            result["per"] = val
        elif label == "PBR":
            result["pbr"] = val
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
        if yld < MIN_YIELD:
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
        "source": "Naver Finance + FinanceDataReader",
        "criteria": f"KOSPI+KOSDAQ 시총 ≥ 1,000억, 배당수익률 ≥ {MIN_YIELD}%",
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
