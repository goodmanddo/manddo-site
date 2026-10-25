#!/usr/bin/env python3
"""주간 추세섹터 × 배당·국민연금 체크 — 섹터 캘린더에서 최근 강했던 섹터를 찾고,
그 섹터의 배당주(dividend.json)·국민연금 지분확대 종목(nps_holdings.json)으로 5개를 한 줄 이유와 함께 고른다.

추세 점수: 최근 20거래일 섹터 칩 등락률 합 × 최근성 가중(1~5일 1.0 / 6~10일 0.6 / 11~20일 0.3)
섹터 판정: KRX 업종(Industry)·주요제품(Products) 키워드 + 대표종목 코드
선정: 배당∩연금 교집합 우선 → 연금 관련 3개 → 나머지 배당률 순 (교집합 없으면 연금3+배당2, 규칙 기반·API 비용 0)
출력: manddo-site/tools/data/dividend_sector_pick.json → /tools/dividend.html 상단 섹션
LaunchAgent: com.mandoo.dividend-sector-pick (일 21:00 — 배당 수집 일 20:00 직후, 위클리 픽 init과 같은 시각)
"""
import json
import re
import subprocess
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr

ROOT = Path.home() / "manddo-site"
CAL = ROOT / "tools" / "data" / "sector_calendar.json"
DIV = ROOT / "tools" / "data" / "dividend.json"
NPS = ROOT / "tools" / "data" / "nps_holdings.json"  # scripts/nps_holdings.py 매일 19:30 (최근 60일분만 들어있음)
NPS_ARCHIVE = ROOT / "tools" / "data" / "nps_pick_archive.json"  # 종목별 최신 공시 누적 (60일 지나도 유지)
NPS_KEEP_DAYS = 120  # 연금 5% 공시는 분기초(1·4·7·10월)에 몰림 → 한 분기+여유분 동안 직전 묶음을 계속 사용
OUT = ROOT / "tools" / "data" / "dividend_sector_pick.json"
LOG = ROOT / "scripts" / "dividend_sector_pick.log"

TG_TOKEN = "8601217415:AAFP0LJDYYLHFWNn0jorKfhZzt2_yiJ31LY"  # 주식분석봇
TG_CHAT = "6579078641"

LOOKBACK = 20     # 추세 판단 거래일 수
PICKS = 5
NPS_PICKS = 3     # 국민연금 지분확대 종목 몫 (교집합 포함)
TREND_SECTORS = 2  # 후보를 뽑는 추세 상위 섹터 수
MIN_IN_SECTOR = 3  # 섹터 후보가 이보다 적으면 그 섹터는 건너뜀

# 캘린더 섹터 → 판정 그룹 (반도체 3종, 바이오 2종은 하나로 묶음)
GROUP = {"반도체 소부장": "반도체", "AI반도체": "반도체", "바이오": "제약/바이오"}
GROUP_ICON = {"반도체": "🔲", "제약/바이오": "💊"}

# 그룹 → (업종 키워드, 제품 키워드, 대표종목 코드)
RULES = {
    "반도체":     (["반도체 제조업"], ["반도체", "웨이퍼", "HBM"], []),
    "2차전지":    (["이차전지"], ["2차전지", "이차전지", "배터리", "양극재", "음극재", "전해액", "분리막"], []),
    "제약/바이오": (["의약품 제조업", "기초 의약물질", "의료용품 및 기타 의약"], ["바이오", "신약"], []),
    "화장품":     ([], ["화장품"], []),
    "방산":       ([], ["방산", "탄약", "유도무기", "방위"], ["012450", "047810", "064350", "079550", "103140"]),
    "게임":       ([], ["게임"], []),
    "로봇":       ([], ["로봇"], []),
    "조선":       (["선박 및 보트 건조업"], ["선박", "조선"], []),
    "해운/운송":  (["해상 운송업", "도로 화물 운송업", "기타 운송관련 서비스업", "항공 여객"], ["해운", "물류", "택배"], []),
    "자동차":     (["자동차"], ["자동차"], []),
    "금융":       (["은행", "보험", "금융 지원 서비스업", "신탁업", "재 보험업"], ["증권", "은행", "보험", "카드"], []),
    "에너지/화학": (["기초 화학물질", "석유 정제품", "합성고무", "화학섬유", "연료용 가스", "비료"], ["정유", "석유화학", "LPG", "도시가스"], []),
    "원자력":     ([], ["원자력", "원전"], ["034020", "052690", "051600", "015760"]),
    "인터넷":     (["포털", "기타 정보 서비스업"], ["포털", "플랫폼"], []),
    "보안":       ([], ["보안"], []),
    "엔터":       (["오디오물 출판"], ["엔터", "음반", "매니지먼트"], []),
    "전력설비":   (["전동기, 발전기 및 전기 변환", "절연선 및 케이블"], ["변압기", "전력기기", "케이블", "차단기", "배전"], []),
    "우주항공":   ([], ["항공기", "위성", "우주"], ["012450", "047810"]),
    "미용의료":   (["의료용 기기"], ["미용", "레이저", "필러", "보톡스", "톡신"], []),
}
# 업종 분류상 섞여 들어오는 비(非)섹터 사업 — 태양광 셀은 KRX 업종이 '반도체 제조업'
NOT_SECTOR = {"반도체": ["태양광"], "자동차": ["보험"]}
EXCLUDE_PRODUCTS = ["지주"]  # 지주사는 업종이 '기타 금융업'·'자동차' 등으로 섞여 들어와 섹터 대표성이 없음


def log(m):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def trend_ranking(cal):
    days = sorted(cal.get("days", {}))[-LOOKBACK:]
    score, hits, icons = {}, {}, {}
    for i, d in enumerate(reversed(days)):  # i=0 가장 최근
        w = 1.0 if i < 5 else 0.6 if i < 10 else 0.3
        seen = set()
        for chip in cal["days"][d]:
            g = GROUP.get(chip["name"], chip["name"])
            icons.setdefault(g, GROUP_ICON.get(g, chip["icon"]))
            score[g] = score.get(g, 0) + chip["pct"] * w
            if g not in seen:  # 반도체 3종이 같은 날 떠도 1회로
                hits[g] = hits.get(g, 0) + 1
                seen.add(g)
    rank = sorted(score, key=lambda g: -score[g])
    return [{"sector": g, "icon": icons[g], "score": round(score[g], 1), "days_up": hits[g]} for g in rank], days


def match_level(group, code, industry, products):
    """2=본업(대표코드·업종 일치·첫 제품이 섹터), 1=사업 일부가 섹터, 0=무관"""
    inds, prods, codes = RULES.get(group, ([], [], []))
    if code in codes:
        return 2
    if any(x in products for x in EXCLUDE_PRODUCTS + NOT_SECTOR.get(group, [])):
        return 0
    first = re.split(r",(?![^()]*\))", products)[0]
    if any(k in industry for k in inds) or any(k in first for k in prods):
        return 2
    return 1 if any(k in products for k in prods) else 0


def sector_product(group, products):
    """제품 목록 중 섹터 키워드가 들어간 항목(없으면 첫 항목) — 이유 줄의 '무슨 회사' 부분"""
    items = [x.strip() for x in re.split(r",(?![^()]*\))", products) if x.strip()]  # 괄호 안 쉼표는 유지
    prods = RULES.get(group, ([], [], []))[1]
    return next((x for x in items if any(k in x for k in prods)), items[0] if items else "")


def fmt_marcap(eok):
    return f"{eok / 10000:.1f}조" if eok >= 10000 else f"{int(eok):,}억"


def short_what(group, info):
    what = sector_product(group, info["products"]) or info["industry"]
    return what if len(what) <= 22 else what[:21] + "…"


def div_bits(s, rank_in_sector, n_in_sector):
    bits = [f"섹터 배당주 {n_in_sector}개 중 배당률 {rank_in_sector}위"]
    if s.get("pbr") is not None and s["pbr"] < 1:
        bits.append(f"PBR {s['pbr']:.2f} 자산가치 이하")
    elif s.get("per") is not None and 0 < s["per"] < 10:
        bits.append(f"PER {s['per']:.1f} 이익 대비 저렴")
    if s["marcap_eok"] >= 10000:
        bits.append(f"시총 {fmt_marcap(s['marcap_eok'])} 대형주")
    return bits


def nps_bit(n):
    d = f"{n['date'][4:6]}/{n['date'][6:]}"
    if float(n["stkrt_irds"]) >= float(n["stkrt"]) - 0.01:  # 증감 = 보유율 전체 → 이번에 5% 처음 넘음
        return f"국민연금 5% 신규 진입 {float(n['stkrt']):.2f}% ({d} 공시)"
    return f"국민연금 지분 {float(n['stkrt']):.2f}%로 +{float(n['stkrt_irds']):.2f}%p 늘림 ({d} 공시)"


def nps_buys():
    """종목별 최신 5%룰 공시에서 지분이 늘어난 종목.
    nps_holdings.json은 60일 창이라 분기초 공시 묶음이 다음 분기 공시 전(12월·3월 등)에 통째로 빠진다
    → 아카이브에 누적해 NPS_KEEP_DAYS 동안 유지. 같은 종목의 새 공시(감소 포함)가 오면 덮어씀."""
    try:
        latest = json.loads(NPS_ARCHIVE.read_text())
    except Exception:
        latest = {}
    for it in json.loads(NPS.read_text())["items"]:
        c = it["stock_code"]
        if c not in latest or it["date"] >= latest[c]["date"]:
            latest[c] = it
    cutoff = (datetime.now() - timedelta(days=NPS_KEEP_DAYS)).strftime("%Y%m%d")
    latest = {c: n for c, n in latest.items() if n["date"] >= cutoff}
    NPS_ARCHIVE.write_text(json.dumps(latest, ensure_ascii=False, indent=1))
    return {c: n for c, n in latest.items() if float(n.get("stkrt_irds") or 0) > 0}


def week_monday(now=None):
    """평일이면 이번 주 월요일, 주말이면 다음 주 월요일"""
    d = (now or datetime.now()).date()
    d = d + timedelta(days=7 - d.weekday()) if d.weekday() >= 5 else d - timedelta(days=d.weekday())
    return d.isoformat()


def send_telegram(text):
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT, "text": text,
                             "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"텔레그램 실패: {e}")


def main():
    cal = json.loads(CAL.read_text())
    div = json.loads(DIV.read_text())
    trend, days = trend_ranking(cal)
    if not trend:
        log("최근 강세 섹터 없음 — 중단")
        return

    desc = fdr.StockListing("KRX-DESC").set_index("Code")
    meta = {c: {"industry": str(r["Industry"]) if r["Industry"] == r["Industry"] else "",
                "products": str(r["Products"]) if r["Products"] == r["Products"] else ""}
            for c, r in desc.iterrows()}

    divs = {x["code"]: x for x in div["stocks"]}
    nps = nps_buys()

    # 후보 = 추세 상위 섹터(배당주·연금매수 합쳐 MIN_IN_SECTOR개 이상인 섹터만) 소속 종목
    used_sectors, cands = [], {}
    for rank, t in enumerate(trend):
        g = t["sector"]
        found = {}
        for c in set(divs) | set(nps):
            if c in meta and c not in cands:
                lv = match_level(g, c, **meta[c])
                if lv and (c in divs or lv == 2):  # 연금 단독은 본업 종목만 (현대해상→자동차 같은 오매칭 차단)
                    found[c] = {"code": c, "sector": g, "icon": t["icon"], "rank": rank, "level": lv}
        if len(found) < MIN_IN_SECTOR:
            log(f"{g}: 후보 {len(found)}개뿐 — 다음 섹터로")
            continue
        used_sectors.append(t)
        cands.update(found)
        if len(used_sectors) >= TREND_SECTORS:
            break

    # 섹터 내 배당률 순위 (이유 줄 표기용)
    div_rank = {}
    for g in [t["sector"] for t in used_sectors]:
        pool = sorted([c for c in cands.values() if cands[c["code"]]["sector"] == g and c["code"] in divs],
                      key=lambda c: (-c["level"], -divs[c["code"]]["dividend_yield"]))
        for i, c in enumerate(pool, 1):
            div_rank[c["code"]] = (i, len(pool))

    by_div = lambda c: (c["rank"], -c["level"], -divs[c["code"]]["dividend_yield"])
    by_nps = lambda c: (c["rank"], -c["level"], -float(nps[c["code"]]["stkrt_irds"]))
    inter = sorted([c for c in cands.values() if c["code"] in divs and c["code"] in nps], key=by_nps)
    nps_only = sorted([c for c in cands.values() if c["code"] in nps and c["code"] not in divs], key=by_nps)
    div_only = sorted([c for c in cands.values() if c["code"] in divs and c["code"] not in nps], key=by_div)

    # 교집합 우선 → 연금 관련이 NPS_PICKS개 될 때까지 연금 단독 → 나머지 배당 → 그래도 모자라면 연금
    chosen = [(c, "both") for c in inter[:PICKS]]
    for c in nps_only:
        if len(chosen) >= PICKS or len(chosen) >= NPS_PICKS:
            break
        chosen.append((c, "nps"))
    for c in div_only + nps_only:
        if len(chosen) >= PICKS:
            break
        if c["code"] not in {x["code"] for x, _ in chosen}:
            chosen.append((c, "nps" if c["code"] in nps else "div"))

    picks = []
    for c, kind in chosen:
        code, g = c["code"], c["sector"]
        d, n = divs.get(code), nps.get(code)
        bits = []
        if n:
            bits.append(nps_bit(n))
        if d:
            bits += div_bits(d, *div_rank[code])[: 1 if n else 3]
        picks.append({
            "code": code, "name": d["name"] if d else n["corp_name"], "sector": g, "icon": c["icon"],
            "kind": kind, "core": c["level"] == 2,
            "dividend_yield": d["dividend_yield"] if d else None,
            "nps_stake": float(n["stkrt"]) if n else None,
            "nps_change": float(n["stkrt_irds"]) if n else None,
            "nps_date": n["date"] if n else None,
            "reason": f"{short_what(g, meta[code])} · " + " · ".join(bits),
        })

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "week": week_monday(),  # 이 세트를 사는 주(월요일) — daily_check_track.py가 추가 전략 라운드로 등록
        "window": {"from": days[0], "to": days[-1], "trading_days": len(days)},
        "dividend_as_of": div.get("generated_at"),
        "nps_as_of": json.loads(NPS.read_text()).get("generated_at"),
        "trend": trend[:6],
        "sectors_used": [t["sector"] for t in used_sectors],
        "picks": picks,
        "method": f"섹터 캘린더 최근 {LOOKBACK}거래일 강세 점수(등락률 합 × 최근성 가중) 상위 {TREND_SECTORS}개 섹터에서 "
                  f"배당주∩국민연금 지분확대 종목 우선, 연금 관련 {NPS_PICKS}개·나머지 배당률 높은 순으로 총 {PICKS}개.",
    }
    try:  # 지난주 세트와 비교 → 새로 들어온 종목 🆕
        prev = {p["code"] for p in json.loads(OUT.read_text()).get("picks", [])}
    except Exception:
        prev = set()
    for p in picks:
        p["new"] = bool(prev) and p["code"] not in prev
    out["dropped"] = sorted(prev - {p["code"] for p in picks})
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    log(f"{days[0]}~{days[-1]} 추세: " + ", ".join(f"{t['sector']}{t['score']:+.1f}" for t in trend[:4])
        + f" → 사용 섹터 {out['sectors_used']}, 픽 {len(picks)}개")

    msg = [f"💰 주간 추세섹터 × 배당·국민연금 체크 ({days[0][5:]}~{days[-1][5:]})", ""]
    msg.append("📈 최근 강한 섹터: " + " · ".join(f"{t['icon']}{t['sector']}({t['days_up']}일)" for t in trend[:3]))
    msg.append("")
    for i, p in enumerate(picks, 1):
        tag = {"both": "🎯배당+연금", "nps": "🏛️연금매수", "div": "💰배당"}[p["kind"]]
        y = f" {p['dividend_yield']:.1f}%" if p["dividend_yield"] else ""
        msg.append(f"{i}. {'🆕 ' if p['new'] else ''}{p['icon']} {p['name']}({p['code']}){y} {tag}")
        msg.append(f"   └ {p['reason']}")
    n_new = sum(p["new"] for p in picks)
    if n_new or out["dropped"]:
        msg += ["", f"🔄 지난주 대비: 🆕 {n_new}개 / 유지 {len(picks) - n_new}개"]
    msg += ["", "https://manddo.kr/tools/dividend.html", "※ 규칙 기반 관찰 리스트, 매수 권유 아님"]
    send_telegram("\n".join(msg))

    try:
        rel = [str(f.relative_to(ROOT)) for f in (OUT, NPS_ARCHIVE)]
        subprocess.run(["git", "-C", str(ROOT), "add", *rel], check=False)
        r = subprocess.run(["git", "-C", str(ROOT), "commit", "-m",
                            f"chore(dividend-pick): {days[-1]} 주간 배당×추세섹터 갱신"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "-C", str(ROOT), "push"], check=False)
            log("git push 완료")
        else:
            log("변경 없음 — 커밋 스킵")
    except Exception as e:
        log(f"git 실패: {e}")


if __name__ == "__main__":
    main()
