#!/usr/bin/env python3
"""
게임주 신작 인텔 — web_search로 '오픈 전' 소식만 조사 → 사이트 + 텔레그램.

하루 2회(09:00·22:00) LaunchAgent(com.mandoo.game-intel)로 실행:
  1) 국내 상장 게임사의 신작 발표·출시 일정(쇼케이스·사전예약·출시일 공개)
  2) 이미 국내에 나온 게임의 해외(글로벌·일본·중국·북미 등) 출시 소식
출시가 끝난 게임의 매출·성과는 다루지 않는다(사용자 결정 2026-10-28: 오픈 후 추적은 의미 없음).

검색으로 찾은 예정 건은 upcoming_game_launches.json에 자동 등록(auto_register)하고,
출시일이 지난 건은 launched_archive로 옮겨 카드에서 뺀다(prune_launched).
자동 등록 오류(옛 전망기사·이미 출시된 게임) 방지: 오늘 이후 날짜만, 구체 일자 아니면 tentative,
종목코드 6자리 확인된 상장사만. 등록·정리 내역은 텔레그램으로 알려 사람이 바로 잡을 수 있게 한다.
"""
import os
import json
import subprocess
import urllib.request
from datetime import datetime, date
from pathlib import Path

HOME = Path.home()
SITE = HOME / "manddo-site"
UPCOMING = SITE / "scripts" / "upcoming_game_launches.json"
LOG = SITE / "scripts" / "game_intel_report.log"

# 키: 파일 우선(launchd 대응 + 죽은 환경변수 회피)
_kf = HOME / "stock_auto_trade" / ".anthropic_key"
API_KEY = _kf.read_text().strip() if _kf.exists() else os.environ.get("ANTHROPIC_API_KEY", "")

TG_TOKEN = "8601217415:AAFP0LJDYYLHFWNn0jorKfhZzt2_yiJ31LY"  # 주식분석봇
TG_CHAT = "6579078641"


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {m}\n")
    print(m)


def tg(text):
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [text]:
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=json.dumps({"chat_id": TG_CHAT, "text": chunk,
                                 "disable_web_page_preview": True}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log(f"텔레그램 실패: {e}")


def registered_summary():
    try:
        evs = json.loads(UPCOMING.read_text()).get("events", [])
        return "\n".join(f"- {e['company']}({e['code']}) {e.get('game','')} / 예정 "
                         f"{e.get('date_label') if e.get('tentative') else e['date']}" for e in evs) or "(없음)"
    except Exception:
        return "(목록 읽기 실패)"


def build_prompt():
    today = date.today().isoformat()
    return f"""너는 한국 게임주 투자자를 위한 신작 인텔리전스 리포터다. **웹 검색을 적극 사용**해 '아직 출시 전'인 소식만 조사하고 간결한 한국어 리포트를 작성하라. 오늘 날짜: {today}.

## 조사 대상 (출시 전 소식만)
A) **국내 신작**: 한국 증시 상장(코스피/코스닥) 게임사의 신작 발표·쇼케이스·사전예약·출시일 공개 (향후 약 6개월)
B) **해외 출시**: 상장 게임사 게임의 해외(글로벌·일본·중국 판호·북미/유럽·동남아 등) 출시 예정 소식 — 국내에 이미 나온 게임이라도 해외 출시가 아직이면 포함
각 항목: 회사명·종목코드·게임명·구분(신작/해외출시+지역)·출시예정일(확정 일자/월·분기만 나온 잠정 구분)·한 줄 근거(매체, 기사 날짜).

⚠️ 엄수:
- **오늘 이전 날짜에 이미 출시됐거나 출시일이 지난 건(해외 출시 포함)은 넣지 마라**. **이미 출시된 게임은 넣지 마라**(출시 후 매출·순위·흥행 성과도 쓰지 마라). 넣기 전에 "이미 출시됐는지" 반드시 검색으로 확인.
- 옛 전망기사(몇 달 전 "4분기 출시 준비")만 근거면 잠정으로, 최신 기사로 일정이 확인될 때만 확정.
- 국내 증시 미상장사는 제외: 비상장(스마일게이트 등)과 해외 상장(넥슨=도쿄 상장)도 제외. 단 국내 상장 자회사(넥슨게임즈 225570 등) 게임이면 그 회사로. 종목코드 불확실하면 '코드미상'.
- 확인 안 된 건 넣지 마라. 지어내지 마라.

우리가 이미 추적 중인 목록(이것 외 새로 찾은 건 앞에 🆕):
{registered_summary()}

⚠️ **날짜 변경 감지**: 위 목록의 예정일이 바뀌었거나 이미 출시됐으면 리포트 **맨 위에**
🔔 날짜변경: [게임명] 등록 YYYY-MM-DD → 실제 YYYY-MM-DD (근거 매체)
형식으로 써라. 변경 없으면 쓰지 마라.

## 출력 형식 (플레인텍스트, 이모지 활용, 400단어 이내)
🎮 게임주 신작 인텔 ({today})
━━━━━━━━━
🆕 국내 신작 일정
- (회사/코드/게임/예정일/근거)
🌏 해외 출시 소식
- (회사/코드/게임/지역/예정일/근거)
💡 한 줄 정리
- (가장 가까운 출시 이벤트)

해당 소식이 없으면 그 섹션에 '이번 조사에서 새 소식 없음'이라고 써라."""


def extract_events(client, report_text):
    """리포트에서 출시 예정 건을 JSON 배열로 추출 (2차 호출, web_search 없음)."""
    p = ("다음 게임주 신작 리포트에서 **아직 출시 전인** 예정 건만 JSON 배열로 추출하라. "
         "각 원소: {\"company\":\"회사\",\"code\":\"6자리 종목코드 또는 null\",\"game\":\"게임명\","
         "\"kind\":\"신작|해외출시\",\"region\":\"해외출시면 지역, 아니면 빈칸\","
         "\"date\":\"구체 일자가 있으면 YYYYMMDD, 없으면 null\","
         "\"date_label\":\"일자 없으면 '11월 예정'·'4분기 예정'·'2027년 상반기' 같은 표시, 있으면 빈칸\","
         "\"month_hint\":\"일자 없을 때 가장 이른 예상 월 YYYYMM, 모르면 null\","
         "\"source\":\"매체\"}.\n"
         "리포트에 있는 것만, 이미 출시된 건 제외, 지어내지 말 것. JSON 배열만 출력.\n\n" + report_text)
    try:
        r = client.messages.create(model="claude-sonnet-4-6", max_tokens=1500,
                                    messages=[{"role": "user", "content": p}])
        txt = "".join(getattr(b, "text", "") for b in r.content if b.type == "text")
        import re
        m = re.search(r"\[.*\]", txt, re.S)
        return json.loads(m.group(0)) if m else []
    except Exception as e:
        log(f"events 추출 실패: {e}")
        return []


def _gkey(code, game):
    import re
    return (code, re.sub(r"[^0-9a-z가-힣]", "", re.sub(r"\(.*?\)", "", (game or "").lower())))


def prune_launched(up):
    """출시일 지난(확정 날짜) 건은 events → launched_archive. 옮긴 목록 반환."""
    today = date.today().strftime("%Y%m%d")
    keep, gone = [], []
    for e in up.get("events", []):
        if not e.get("tentative") and str(e.get("date", "")) <= today:
            gone.append(e)
        else:
            keep.append(e)
    if gone:
        up["events"] = keep
        up.setdefault("launched_archive", []).extend(gone)
    return gone


_KRX = None


def krx_lookup(company):
    """회사명 → (코드, 벤치). KRX 상장 목록 기준이라 비상장·해외상장(넥슨 등)은 None."""
    global _KRX
    try:
        if _KRX is None:
            import FinanceDataReader as fdr
            df = fdr.StockListing("KRX")
            _KRX = {r.Name: (r.Code, r.Market) for r in df.itertuples()}
        hit = _KRX.get((company or "").strip())
        if hit:
            return hit[0], ("069500" if hit[1] == "KOSPI" else "229200")
    except Exception as e:
        log(f"KRX 목록 실패: {e}")
    return None, None


def auto_register(up, found):
    """검색으로 찾은 예정 건을 events에 추가. 추가 목록 반환."""
    import re
    import calendar
    today = date.today()
    keys = {_gkey(e["code"], e.get("game")) for e in up.get("events", [])}
    keys |= {_gkey(e["code"], e.get("game")) for e in up.get("launched_archive", [])}
    added = []
    for f in found:
        kcode, bench = krx_lookup(f.get("company"))
        code = kcode or str(f.get("code") or "")   # 상장목록 우선(모델 코드 오기 방지)
        if not re.fullmatch(r"\d{6}", code) or not f.get("game"):
            continue
        game = f["game"] + (f" ({f['region']} 출시)" if f.get("kind") == "해외출시" and f.get("region") else "")
        k = _gkey(code, game)
        if k in keys:
            continue
        d = str(f.get("date") or "")
        if re.fullmatch(r"\d{8}", d):
            if d <= today.strftime("%Y%m%d"):
                continue  # 이미 지났거나 오늘 = 출시 전 소식 아님
            ev = {"date": d, "status": "confirmed"}
        else:
            mh = str(f.get("month_hint") or "")
            if not re.fullmatch(r"\d{6}", mh):
                continue  # 시기조차 모르면 카드로 만들 수 없음 → 리포트 텍스트에만
            y, m = int(mh[:4]), int(mh[4:])
            if (y, m) < (today.year, today.month):
                continue
            last = calendar.monthrange(y, m)[1]
            ev = {"date": f"{y}{m:02d}{last:02d}", "status": "expected", "tentative": True,
                  "date_label": f.get("date_label") or f"{m}월 예정"}
        ev = {"company": f.get("company", ""), "code": code, "game": game, **ev,
              "bench": bench or "229200", "kind": f.get("kind", "신작"),
              "note": f"자동등록 {today} (출처 {f.get('source', '?')})"}
        up.setdefault("events", []).append(ev)
        keys.add(k)
        added.append(ev)
    return added


def dday_telegram():
    """추적 중인 출시 예정 게임의 D-day 간결 요약. 예정 없으면 None."""
    try:
        evs = json.loads(UPCOMING.read_text()).get("events", [])
    except Exception:
        return None
    today = date.today()
    rows = []
    for e in evs:
        ds = str(e.get("date", ""))
        if len(ds) != 8:
            continue
        try:
            d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        except ValueError:
            continue
        dd = (d - today).days
        if dd < 0:
            continue  # 이미 출시된 건 제외
        rows.append((dd, d, e))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    lines = [f"🎮 출시 예정 게임 D-day ({today} 기준)", "━━━━━━━━━━━━"]
    for dd, d, e in rows:
        tag = "🔥" if dd <= 7 else ("⏰" if dd <= 30 else "•")
        dd_txt = "D-DAY" if dd == 0 else f"D-{dd}"
        lines.append(f"{tag} {dd_txt} · {e.get('game','')} — {e.get('company','')}({e.get('code','')}) {d.isoformat()}")
    return "\n".join(lines)


def main():
    if not API_KEY:
        log("ANTHROPIC 키 없음 — 종료")
        return
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=3000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": build_prompt()}],
        )
    except Exception as e:
        log(f"API 실패: {e}")
        tg(f"⚠️ 게임주 주간 인텔 생성 실패: {e}")
        return
    text = "".join(getattr(b, "text", "") for b in r.content if b.type == "text").strip()
    searches = sum(1 for b in r.content if b.type == "server_tool_use")
    if not text:
        log("빈 응답")
        return
    # 🔔 날짜변경 플래그 추출 → 감지되면 즉시 텔레그램(시간무관, 중요)
    import re
    alerts = re.findall(r"🔔[^\n]*", text)
    if alerts:
        log(f"날짜변경 감지 {len(alerts)}건")
        tg("📅 게임 출시일 변경 감지 — upcoming_game_launches.json 갱신 필요\n\n"
           + "\n".join(alerts))
    i = text.find("🎮")
    if i > 0:
        text = text[i:]
    # 페이지는 <pre> 플레인텍스트라 마크다운 기호 제거
    text = re.sub(r"\*\*|__", "", text)
    text = "\n".join(re.sub(r"^>\s?", "", l) for l in text.splitlines()
                     if l.strip() not in ("---", "```"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    found = extract_events(client, text)
    up = json.loads(UPCOMING.read_text())
    gone = prune_launched(up)
    added = auto_register(up, found)
    if gone or added:
        UPCOMING.write_text(json.dumps(up, ensure_ascii=False, indent=2), encoding="utf-8")
        msg = ["🎮 게임 신작 트래커 자동 갱신"]
        msg += [f"➕ {e['company']}({e['code']}) {e['game']} — "
                f"{e.get('date_label') if e.get('tentative') else e['date']} ({e['note']})" for e in added]
        msg += [f"➖ 출시됨 정리: {e['company']} {e.get('game','')} ({e['date']})" for e in gone]
        msg.append("틀린 건 upcoming_game_launches.json에서 지우면 됩니다")
        tg("\n".join(msg))
        subprocess.run(["/opt/homebrew/bin/python3", str(SITE / "scripts" / "fetch_game_watch.py")], check=False)
    log(f"리포트 생성 (검색 {searches}회, 본문 {len(text)}자, 추출 {len(found)}건, "
        f"추가 {len(added)}, 정리 {len(gone)})")
    # 텔레그램: 반복되는 전체 인텔 대신 'D-day 요약'만, 아침 1회(스팸 방지).
    # 전체 리포트/순위는 아래 game_intel.json으로 사이트(/game-watch/)에만 반영.
    if datetime.now().hour < 12:
        dmsg = dday_telegram()
        if dmsg:
            tg(dmsg)
    # 로컬 보관 + 웹 노출용 JSON (/game-watch/ 하단에서 fetch)
    (SITE / "scripts" / "game_intel_last.txt").write_text(
        f"[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n", encoding="utf-8")
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "report": text, "events": found}
    (SITE / "tools" / "data" / "game_intel.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-C", str(SITE), "add", "tools/data/game_intel.json",
                        "scripts/upcoming_game_launches.json"], check=False, env=env)
        subprocess.run(["git", "-C", str(SITE), "commit", "-q", "-m",
                        f"chore(game-intel): 신작 인텔 갱신 ({date.today()})"], env=env)
        subprocess.run(["git", "-C", str(SITE), "push", "-q"], check=False, env=env)
    except Exception as e:
        log(f"git 실패: {e}")
    log("✓ 텔레그램 전송 + 웹 반영 완료")


if __name__ == "__main__":
    main()
