#!/usr/bin/env python3
"""
국민연금(국민연금공단) 최근 지분공시 수집 → tools/data/nps_holdings.json

DART OpenDART API:
  1) list.json (pblntf_ty=D 지분공시) 전 페이지에서 제출인(flr_nm)에 '국민연금' 포함 건 수집
  2) 종목별 최신 공시로 dedupe
  3) majorstock.json 으로 국민연금 보유비율(stkrt)·증감(stkrt_irds) 보강
※ DART엔 5%룰(대량보유·주요주주) 보고만 잡힘 → 5% 미만 소액 보유는 안 나옴.

매일 LaunchAgent(com.mandoo.nps-holdings)로 실행 → git commit/push.
"""
import json
import time
import subprocess
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
SITE = HOME / "manddo-site"
KEY = open(HOME / ".dart_api_key").read().strip()
OUT = SITE / "tools" / "data" / "nps_holdings.json"
LOG = SITE / "scripts" / "fetch_nps_holdings.log"
WINDOW_DAYS = 60
BASE = "https://opendart.fss.or.kr/api"
MARKET = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {m}\n")
    print(m)


def api(path, params):
    params = {"crtfc_key": KEY, **params}
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def collect_disclosures():
    end = date.today()
    bgn = end - timedelta(days=WINDOW_DAYS)
    p = {"bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
         "pblntf_ty": "D", "page_count": 100, "page_no": 1}
    first = api("list.json", p)
    if first.get("status") != "000":
        log(f"list.json 오류: {first.get('status')} {first.get('message')}")
        return []
    total_pages = int(first.get("total_page", 1))
    items = []
    for pg in range(1, total_pages + 1):
        data = first if pg == 1 else api("list.json", {**p, "page_no": pg})
        for x in data.get("list", []):
            if "국민연금" in (x.get("flr_nm") or ""):
                items.append(x)
        if pg > 1:
            time.sleep(0.05)
    log(f"지분공시 {total_pages}p 스캔 → 국민연금 {len(items)}건")
    return items


def enrich_ratio(corp_code):
    """majorstock.json 에서 국민연금 보유비율·증감 (없으면 None)."""
    try:
        m = api("majorstock.json", {"corp_code": corp_code})
    except Exception:
        return None
    if m.get("status") != "000":
        return None
    best = None
    for row in m.get("list", []):
        if "국민연금" not in (row.get("repror") or ""):
            continue
        d = row.get("rcept_dt") or ""
        if best is None or d > best["asof"]:
            best = {
                "stkrt": row.get("stkrt"), "stkrt_irds": row.get("stkrt_irds"),
                "asof": d, "report_tp": row.get("report_tp"),
            }
    return best


def main():
    raw = collect_disclosures()
    # 종목별 최신 공시로 dedupe
    latest = {}
    for x in raw:
        cc = x["corp_code"]
        if cc not in latest or x["rcept_dt"] > latest[cc]["rcept_dt"]:
            latest[cc] = x
    log(f"고유 종목 {len(latest)}개 — 보유비율 보강 중...")

    items = []
    for cc, x in latest.items():
        info = enrich_ratio(cc)
        time.sleep(0.05)
        items.append({
            "date": x["rcept_dt"],
            "corp_name": x["corp_name"],
            "stock_code": x.get("stock_code") or "",
            "market": MARKET.get(x.get("corp_cls"), ""),
            "report_nm": (x.get("report_nm") or "").strip(),
            "rcept_no": x["rcept_no"],
            "stkrt": info["stkrt"] if info else None,
            "stkrt_irds": info["stkrt_irds"] if info else None,
            "stkrt_asof": info["asof"] if info else None,
        })
    items.sort(key=lambda r: r["date"], reverse=True)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": WINDOW_DAYS,
        "count": len(items),
        "source": "DART OpenDART (국민연금공단 5%룰 지분공시)",
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✓ 저장: {OUT.name} ({len(items)}종목)")

    try:
        env = {**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-C", str(SITE), "add", "tools/data/nps_holdings.json"], check=True, env=env)
        r = subprocess.run(["git", "-C", str(SITE), "commit", "-q", "-m",
                            f"chore(nps): 국민연금 지분공시 갱신 ({date.today()})"], env=env)
        if r.returncode == 0:
            subprocess.run(["git", "-C", str(SITE), "push", "-q"], check=True, env=env)
            log("✓ git push")
        else:
            log("변경 없음 — 커밋 스킵")
    except Exception as e:
        log(f"git 실패: {e}")


if __name__ == "__main__":
    main()
