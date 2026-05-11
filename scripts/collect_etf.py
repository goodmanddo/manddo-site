#!/usr/bin/env python3
"""ETF 도감 데이터 수집 — Naver Finance에서 KR ETF 메타·TOP10 보유종목·섹터 분포 수집.

출력: ~/manddo-site/tools/data/etf-holdings.json
실행: 주1회 (LaunchAgent: com.mandoo.etf-collect)

수집 항목 (ETF별):
  - code/name/issuer/base_index/inception/expense_ratio/aum_eok
  - returns (1M/3M/6M/YTD)
  - asset/country/sector breakdown
  - top10 holdings: [{code, name, weight}, ...]

이 한 데이터에서 4가지 페이지 파생 가능:
  1) ETF 도감 (forward: ETF→보유)
  2) 테마별 ETF (sector 기반 분류)
  3) 종목→ETF 역검색 (reverse lookup, SEO 핵심)
  4) 겹침 분석기 (n개 ETF → 종목 노출 합산)
"""

import json
import re
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path.home() / "manddo-site"
OUT = ROOT / "tools" / "data" / "etf-holdings.json"

UA_DESKTOP = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
UA_MOBILE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}

TOP_N = 100  # 시총 상위 N개 ETF만 수집


def fetch_etf_list():
    """Naver Finance ETF 전체 목록 (시가총액 내림차순)."""
    url = "https://finance.naver.com/api/sise/etfItemList.nhn"
    params = {"etfType": 0, "targetColumn": "market_sum", "sortOrder": "desc"}
    r = requests.get(url, params=params, headers=UA_DESKTOP, timeout=10)
    r.raise_for_status()
    # CP949 한글이 가끔 깨져서 들어오므로 itemcode·marketSum만 신뢰
    data = r.json()
    items = data["result"]["etfItemList"]
    return items[:TOP_N]


def fetch_etf_analysis(code: str):
    """모바일 Naver: ETF 상세 분석 JSON. 보유종목 TOP10 + 섹터 분포 포함."""
    url = f"https://m.stock.naver.com/api/stock/{code}/etfAnalysis"
    r = requests.get(url, headers=UA_MOBILE, timeout=10)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def parse_weight(s: str):
    """'32.47%' → 32.47, None → None."""
    if s is None:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)", str(s))
    return float(m.group(1)) if m else None


def parse_marcap_eok(s: str):
    """'26조 1,698억' → 261698 (억원 단위)."""
    if not s:
        return None
    s = s.replace(",", "")
    total = 0
    m_jo = re.search(r"(\d+(?:\.\d+)?)\s*조", s)
    if m_jo:
        total += float(m_jo.group(1)) * 10000
    m_eok = re.search(r"(\d+(?:\.\d+)?)\s*억", s)
    if m_eok:
        total += float(m_eok.group(1))
    return round(total) if total else None


def classify_theme(name: str, sectors: list) -> str:
    """ETF 이름·섹터 분포로 테마 분류 (간단 룰 기반).

    우선순위: 이름 키워드 → 섹터 분포 → '기타'
    """
    n = name.upper()
    # 이름 기반 키워드 매핑 (우선순위 순)
    name_rules = [
        ("미국", "미국"),
        ("S&P", "미국"),
        ("나스닥", "미국"),
        ("NASDAQ", "미국"),
        ("다우", "미국"),
        ("중국", "중국"),
        ("CHINA", "중국"),
        ("차이나", "중국"),
        ("항셍", "중국"),
        ("일본", "일본"),
        ("JAPAN", "일본"),
        ("니케이", "일본"),
        ("인도", "인도"),
        ("INDIA", "인도"),
        ("베트남", "베트남"),
        ("VIETNAM", "베트남"),
        ("유럽", "유럽"),
        ("선진", "선진국"),
        ("신흥", "신흥국"),
        ("EMERGING", "신흥국"),
        ("반도체", "반도체"),
        ("SEMICONDUCTOR", "반도체"),
        ("AI", "AI"),
        ("2차전지", "2차전지"),
        ("BATTERY", "2차전지"),
        ("배터리", "2차전지"),
        ("바이오", "바이오·헬스케어"),
        ("BIO", "바이오·헬스케어"),
        ("헬스케어", "바이오·헬스케어"),
        ("HEALTH", "바이오·헬스케어"),
        ("자동차", "자동차"),
        ("EV", "2차전지"),
        ("전기차", "2차전지"),
        ("금융", "금융"),
        ("FINANCE", "금융"),
        ("리츠", "리츠·부동산"),
        ("REIT", "리츠·부동산"),
        ("부동산", "리츠·부동산"),
        ("배당", "배당·인컴"),
        ("DIVIDEND", "배당·인컴"),
        ("고배당", "배당·인컴"),
        ("커버드콜", "배당·인컴"),
        ("COVER", "배당·인컴"),
        ("로보틱스", "로봇·메타"),
        ("ROBOT", "로봇·메타"),
        ("메타버스", "로봇·메타"),
        ("META", "로봇·메타"),
        ("리튬", "원자재"),
        ("LITHIUM", "원자재"),
        ("우라늄", "원자재"),
        ("URANIUM", "원자재"),
        ("금", "원자재"),
        ("GOLD", "원자재"),
        ("은", "원자재"),
        ("SILVER", "원자재"),
        ("원유", "원자재"),
        ("OIL", "원자재"),
        ("천연가스", "원자재"),
        ("GAS", "원자재"),
        ("국채", "채권"),
        ("BOND", "채권"),
        ("채권", "채권"),
        ("TREASURY", "채권"),
        ("200", "코스피 대표지수"),
        ("KOSPI", "코스피 대표지수"),
        ("KOSDAQ", "코스닥 대표지수"),
        ("코스닥", "코스닥 대표지수"),
        ("MSCI", "글로벌"),
    ]
    for kw, theme in name_rules:
        if kw in n:
            return theme

    # 섹터 분포 상위 비중으로 결정
    if sectors:
        top = max(sectors, key=lambda s: s.get("weight", 0))
        code_map = {
            "IT": "IT·반도체",
            "FINANCIALS": "금융",
            "FINANCE": "금융",
            "INDUSTRIALS": "산업재",
            "ENERGY": "에너지",
            "HEALTHCARE": "바이오·헬스케어",
            "MATERIALS": "소재",
            "CONSUMER": "소비재",
            "REAL_ESTATE": "리츠·부동산",
            "UTILITIES": "유틸리티",
            "TELECOM": "통신",
        }
        return code_map.get(top.get("detailTypeCode", "").upper(), "기타")

    return "기타"


def collect():
    print(f"[ETF] 시총 상위 {TOP_N}개 수집 시작")
    etf_list = fetch_etf_list()
    print(f"  목록 가져옴: {len(etf_list)}개")

    etfs = []
    for i, item in enumerate(etf_list, 1):
        code = item["itemcode"]
        name_from_list = item.get("itemname", "")
        marcap_from_list = item.get("marketSum")  # 백만원 단위

        info = fetch_etf_analysis(code)
        if not info:
            print(f"  [{i:3d}/{TOP_N}] {code} 분석 데이터 없음 — 스킵")
            continue

        name = info.get("itemName") or name_from_list
        aum_eok = parse_marcap_eok(info.get("marketValue"))
        if aum_eok is None and marcap_from_list:
            aum_eok = round(marcap_from_list / 100)  # 백만원 → 억원

        holdings_raw = info.get("etfTop10MajorConstituentAssets", []) or []
        holdings = []
        for h in holdings_raw:
            w = parse_weight(h.get("etfWeight"))
            if w is None:
                continue
            holdings.append({
                "code": h.get("itemCode"),
                "name": h.get("itemName"),
                "weight": w,
            })

        sectors = info.get("sectorPortfolioList", []) or []
        countries = info.get("countryPortfolioList", []) or []

        theme = classify_theme(name, sectors)

        # 수익률 (periodTypeCode: D1/W1/M1/M3/M6/YTD/Y1/Y3/Y5/Y10)
        returns = {}
        for r in info.get("returnPerformanceList", []) or []:
            pcode = r.get("periodTypeCode")
            val = r.get("value")
            if pcode and val is not None:
                returns[pcode] = val

        etfs.append({
            "code": code,
            "name": name,
            "theme": theme,
            "issuer": info.get("issuerName"),
            "base_index": info.get("etfBaseIndex"),
            "inception": info.get("listedDate"),
            "aum_eok": aum_eok,
            "nav": info.get("nav"),
            "expense_ratio": parse_weight(info.get("totalFee")),
            "returns": returns,
            "holdings": holdings,
            "sectors": [
                {"code": s.get("detailTypeCode"), "weight": s.get("weight")}
                for s in sectors
            ],
            "countries": [
                {"code": c.get("detailTypeCode"), "weight": c.get("weight")}
                for c in countries
            ],
        })

        top_str = ", ".join(f"{h['name']}({h['weight']:.1f}%)" for h in holdings[:3])
        print(f"  [{i:3d}/{TOP_N}] {name}({code}) [{theme}] {top_str}")
        time.sleep(0.3)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "Naver Finance Mobile API",
        "criteria": f"KR 상장 ETF 시총 상위 {TOP_N}, TOP 10 보유종목",
        "count": len(etfs),
        "etfs": etfs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n✓ {len(etfs)}개 ETF 저장 → {OUT.relative_to(ROOT)}")

    # 개별 ETF 페이지 정적 빌드
    try:
        subprocess.run(
            ["/opt/homebrew/bin/python3", str(ROOT / "scripts" / "build_etf_pages.py")],
            check=True,
        )
    except Exception as e:
        print(f"! 페이지 빌드 실패: {e}")

    try:
        subprocess.run(["git", "-C", str(ROOT), "add", "tools/data/etf-holdings.json", "etf/"], check=True)
        subprocess.run(
            ["git", "-C", str(ROOT), "commit", "-m", f"chore(etf): refresh holdings + pages ({len(etfs)} ETFs)"],
            check=True,
        )
        subprocess.run(["git", "-C", str(ROOT), "push"], check=True, timeout=30)
        print("✓ git push 완료")
    except subprocess.CalledProcessError as e:
        print(f"! git 작업 실패 (변경 없음일 수도): {e}")
    except Exception as e:
        print(f"! git 에러: {e}")


if __name__ == "__main__":
    collect()
