#!/usr/bin/env python3
"""
AI 매매 복기 '너라면?' 게임 반자동 생성기.

trade_log.jsonl의 실제 매도(SELL)를 골라, 한투 시세로 '그 뒤 실제 결과'를
결정론적으로 계산하고, 서술 프로즈만 실제 데이터에 근거해 Claude로 생성한다.
→ /vs/replay/{slug}.html (인터랙티브 선택형 페이지) + 목록/사이트맵 갱신.

숫자(가격 경로·수익률·최저/최고·판정)는 100% 실데이터에서 계산 → 환각 없음.
Claude는 상황/근거/교훈 '문장'만, 제공된 팩트 안에서 작성.

모드:
  --trade CODE YYYY-MM-DD   특정 매도 복기 생성
  --auto                    아직 안 만든 최근 유의미 매도 1건 자동 생성
  --publish                 생성 후 사이트맵·목록 갱신 + git commit/push
  (--publish 없으면 파일만 쓰고 검토 대기 = 반자동)
"""
import os
_kf = os.path.expanduser("~/stock_auto_trade/.anthropic_key")
if not os.environ.get("ANTHROPIC_API_KEY") and os.path.isfile(_kf):
    os.environ["ANTHROPIC_API_KEY"] = open(_kf).read().strip()

import sys
import json
import re
import argparse
import subprocess
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, "/Users/mandoo/stock_auto_trade")
from kis_api import KISApi  # noqa: E402
import anthropic  # noqa: E402
import requests  # noqa: E402

TG_TOKEN = "8601217415:AAFP0LJDYYLHFWNn0jorKfhZzt2_yiJ31LY"  # 주식분석봇
TG_CHAT = "6579078641"


def tg(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": False},
            timeout=10,
        )
    except Exception as e:
        log(f"텔레그램 실패: {e}")

HOME = Path.home()
SITE = HOME / "manddo-site"
TRADE_LOG = HOME / "stock_auto_trade" / "trade_log.jsonl"
REPLAY_DIR = SITE / "vs" / "replay"
MANIFEST = REPLAY_DIR / "_manifest.json"
SITEMAP = SITE / "sitemap.xml"
LOG = SITE / "scripts" / "generate_replay.log"
MIN_DAYS = 5   # 매도 후 최소 거래일 (결과가 드러날 시간)

# 스케줄성 청산 등 '판단이 아닌' 매도는 복기 대상에서 제외
EXCLUDE_REASON = ("금요일 청산", "라운드", "시즌")

STRATEGY_HINT = {
    "swing": "스윙 매매 — 손절 규칙 또는 3인 AI 토론으로 매도 결정",
    "lab_alloc": "규칙 기반 자산배분 — 이동평균 추세 필터(추세 이탈 시 현금화)",
}


def log(m):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {m}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return []


def date_kor(iso):
    d = date.fromisoformat(iso)
    return f"{d.year}년 {d.month}월 {d.day}일"


def load_sells():
    """성공한 매도 목록 (최신순)."""
    out = []
    for line in TRADE_LOG.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("action") != "SELL" or d.get("result") != "success":
            continue
        reason = d.get("reason", "") or ""
        if any(x in reason for x in EXCLUDE_REASON):
            continue
        out.append(d)
    out.reverse()
    return out


def price_path(code, trade_iso):
    """매도일 직전~현재 종가 경로 [(MM/DD, close), ...] 와 통계."""
    api = KISApi()
    df = api.get_daily_candles(code)
    ymd = trade_iso.replace("-", "")
    rows = [(str(r["date"]), int(r["close"])) for _, r in df.iterrows()]
    rows.sort()
    # 매도일 하루 전부터
    start_i = 0
    for i, (dt, _) in enumerate(rows):
        if dt >= ymd:
            start_i = max(0, i - 1)
            break
    seg = rows[start_i:]
    after = [(dt, v) for dt, v in seg if dt >= ymd]
    return seg, after


def mmdd(ymd):
    return f"{int(ymd[4:6])}/{int(ymd[6:8])}"


def compute_outcome(sell_price, after):
    """매도 후 결과 통계 + 판정(SELL 기준: 하락=옳음)."""
    vals = [v for _, v in after]
    now = vals[-1]
    lo, hi = min(vals), max(vals)
    now_pct = (now / sell_price - 1) * 100
    lo_pct = (lo / sell_price - 1) * 100
    hi_pct = (hi / sell_price - 1) * 100
    right = now < sell_price          # 판 뒤 떨어졌으면 매도가 옳았음
    # 반전(twist) 감지
    if right:
        twist = hi > sell_price * 1.01     # 옳았지만 처음엔 반등
    else:
        twist = lo < sell_price * 0.99     # 틀렸지만 처음엔 더 빠짐
    return {
        "now": now, "lo": lo, "hi": hi,
        "now_pct": round(now_pct, 1), "lo_pct": round(lo_pct, 1), "hi_pct": round(hi_pct, 1),
        "days": len(after), "right": right, "twist": twist,
    }


def gen_prose(fact):
    """제공된 팩트만 근거로 서술 문장 생성 (JSON)."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""만또 사이트의 'AI 매매 복기 — 너라면?' 인터랙티브 게임에 넣을 **문장**을 쓴다.
독자는 상황만 보고 매수/보유/매도를 고른 뒤, AI의 실제 판단과 그 뒤 실제 결과를 확인한다.

## 규칙
- **아래 팩트의 숫자·사실만 사용.** 새 수치·지표·날짜를 지어내지 말 것.
- 한국어, 담백한 1인칭/관찰 톤("~다"). 과장·추천 금지, '기록·복기'임이 드러나게.
- AI는 미래를 안 게 아니라 '규칙/토론대로 실행'했을 뿐이라는 프레임(예측 알파 아님).
- 결과가 right=false면 'AI가 이번엔 틀렸다'를 정직하게 인정하는 톤.

## 팩트
{json.dumps(fact, ensure_ascii=False, indent=2)}

## 출력 (JSON 객체 하나만)
{{
  "title": "페이지 제목 (종목+훅, 낚시성 아님, 40자 이내)",
  "hero_lead": "히어로 리드 2~3문장 (상황 요약, 결과 스포일러 금지)",
  "sit_para": "그날의 상황 설명 1문단 (왜 이 매도가 나왔는지 배경, 스포일러 금지)",
  "sit_para2": "버틸까 끊을까의 딜레마 1~2문장",
  "trend_chip": "추세/사유 칩 문구 (예: '손절선 도달', '추세 이탈 ↓', 'AI 토론 매도') 12자 이내",
  "ai_action_word": "AI가 한 행동 한 단어 ('매도')",
  "ai_reason1": "AI가 왜 그렇게 했는지 1문단 (strategy/reason 근거)",
  "ai_reason2": "회수한 자금/후속 맥락 또는 규칙의 성격 1문단",
  "twist_h2": "반전 소제목 (twist=true일 때만, 아니면 빈 문자열)",
  "twist_para": "반전 문단 (twist=true일 때만; 옳았지만 처음 반등했거나/틀렸지만 처음 더 빠진 이야기, 아니면 빈 문자열)",
  "verdict_h2": "판정 소제목 (right=true면 '규칙이 지켜줬다' 류, false면 'AI가 틀렸다' 류)",
  "verdict_p": "판정 문단 (숫자 근거로, 정직하게)",
  "lesson_para": "이 복기의 교훈 1문단 (규율의 값어치 또는 규칙의 한계)",
  "fb_buy": "'더 산다' 선택 피드백 1문장",
  "fb_hold": "'버틴다' 선택 피드백 1문장 (버텼으면 결과가 어땠는지 숫자로)",
  "fb_sell": "'판다' 선택 피드백 1문장 (AI와 같은 선택)"
}}
JSON 외 텍스트 금지."""
    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError(f"프로즈 JSON 파싱 실패: {text[:200]}")
    return json.loads(m.group(0))


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(trade, out, path, slug):
    p = esc
    sell = trade["price"]
    qty = trade["qty"]
    amount = trade.get("amount", sell * qty)
    date_iso = trade["timestamp"][:10]
    data_js = "[" + ",".join(f"['{mmdd(dt)}',{v}]" for dt, v in path) + "]"

    twist_block = ""
    if out["twist"] and out.get("_twist_h2"):
        twist_block = f"""    <div class="rp-card" style="background:#fffbeb;border-color:#fde68a">
      <h2>{p(out['_twist_h2'])}</h2>
      <p class="rp-sit">{p(out['_twist_para'])}</p>
    </div>
"""
    vclass = "good" if out["right"] else "bad"
    # 스코어라인: 옳았으면 최저, 틀렸으면 최고를 강조
    if out["right"]:
        mid_k, mid_v = "이후 최저", f"{out['lo']:,}원 ({out['lo_pct']:+.0f}%)"
    else:
        mid_k, mid_v = "이후 최고", f"{out['hi']:,}원 ({out['hi_pct']:+.0f}%)"

    labels_js = json.dumps({
        "buy": {"t": "📈 \"더 산다\"를 골랐군요.", "d": out["_fb_buy"]},
        "hold": {"t": "✋ \"버틴다\"를 골랐군요.", "d": out["_fb_hold"]},
        "sell": {"t": "✂️ \"판다\"를 골랐군요 — AI와 같은 선택!", "d": out["_fb_sell"]},
    }, ensure_ascii=False)

    tpl = (SITE / "scripts" / "replay_template.html").read_text(encoding="utf-8")
    rep = {
        "SLUG": slug,
        "TITLE": p(out["_title"]),
        "DESC": p(out["hero_lead_plain"]),
        "HERO_LEAD": p(out["_hero_lead"]),
        "DATE_KOR": date_kor(date_iso),
        "NAME": p(trade["name"]),
        "CODE": trade["code"],
        "SIT_PARA": p(out["_sit_para"]),
        "SIT_PARA2": p(out["_sit_para2"]),
        "CHIP_PRICE": f"{sell:,}원",
        "CHIP_QTY": f"{qty}주 (약 {amount//10000}만원)",
        "CHIP_TREND": p(out["_trend_chip"]),
        "AI_ACTION": p(out["_ai_action_word"]),
        "AI_REASON1": p(out["_ai_reason1"]),
        "AI_REASON2": p(out["_ai_reason2"]),
        "TWIST_BLOCK": twist_block,
        "VCLASS": vclass,
        "VERDICT_H2": p(out["_verdict_h2"]),
        "VERDICT_P": p(out["_verdict_p"]),
        "SC_SELL": f"{sell:,}원",
        "SC_MID_K": mid_k,
        "SC_MID_V": mid_v,
        "SC_NOW": f"{out['now']:,}원 ({out['now_pct']:+.0f}%)",
        "SC_NOW_CLASS": "red" if out["now_pct"] < 0 else "green2",
        "SC_MID_CLASS": "red" if (out['lo_pct'] if out['right'] else out['hi_pct']) < 0 else "green2",
        "LESSON_PARA": p(out["_lesson_para"]),
        "DATA_JS": data_js,
        "SELL_PRICE": str(sell),
        "LABELS_JS": labels_js,
    }
    html = tpl
    for k, v in rep.items():
        html = html.replace("%%" + k + "%%", str(v))
    dest = REPLAY_DIR / f"{slug}.html"
    dest.write_text(html, encoding="utf-8")
    return dest


def build_index(manifest):
    cards = ""
    for r in manifest:
        verdict = "✅ AI 적중" if r["right"] else "❌ AI 오판"
        cards += f"""    <a href="/vs/replay/{r['slug']}.html" class="vs-card">
      <div class="vs-cat">🎬 매매 복기 · {r['date']}</div>
      <div class="vs-title">{esc(r['title'])}</div>
      <div class="vs-desc">{esc(r['name'])}({r['code']}) 매도 복기. 상황 보고 먼저 선택 → AI 실제 판단·결과 확인. <b>{verdict}</b></div>
      <div class="vs-tags"><span class="vs-tag">#매매복기</span><span class="vs-tag">#선택형</span></div>
    </a>
"""
    idx = (SITE / "scripts" / "replay_index_template.html").read_text(encoding="utf-8")
    (REPLAY_DIR / "index.html").write_text(idx.replace("%%CARDS%%", cards), encoding="utf-8")


def update_sitemap(slugs):
    sm = SITEMAP.read_text(encoding="utf-8")
    today = date.today().isoformat()
    added = 0
    for slug in slugs:
        url = f"https://manddo.kr/vs/replay/{slug}.html"
        if url in sm:
            continue
        entry = (f"  <url>\n    <loc>{url}</loc>\n    <lastmod>{today}</lastmod>\n"
                 f"    <changefreq>monthly</changefreq>\n    <priority>0.7</priority>\n  </url>\n")
        sm = sm.replace("</urlset>", entry + "</urlset>")
        added += 1
    # 목록 페이지도 등록
    lu = "https://manddo.kr/vs/replay/"
    if lu not in sm:
        entry = (f"  <url>\n    <loc>{lu}</loc>\n    <lastmod>{today}</lastmod>\n"
                 f"    <changefreq>weekly</changefreq>\n    <priority>0.7</priority>\n  </url>\n")
        sm = sm.replace("</urlset>", entry + "</urlset>")
    SITEMAP.write_text(sm, encoding="utf-8")
    return added


def make_one(trade):
    date_iso = trade["timestamp"][:10]
    slug = f"{trade['code']}-{date_iso}"
    seg, after = price_path(trade["code"], date_iso)
    if len(after) < MIN_DAYS:
        log(f"스킵(경과<{MIN_DAYS}일): {trade['name']} {date_iso}")
        return None
    outcome = compute_outcome(trade["price"], after)
    fact = {
        "종목": trade["name"], "코드": trade["code"], "매도일": date_iso,
        "매도가": trade["price"], "수량": trade["qty"],
        "매도사유_raw": trade.get("reason", ""),
        "전략": trade.get("strategy", ""),
        "전략_해설": STRATEGY_HINT.get(trade.get("strategy", ""), ""),
        "매도후_현재수익률%": outcome["now_pct"],
        "매도후_최저%": outcome["lo_pct"], "매도후_최고%": outcome["hi_pct"],
        "경과거래일": outcome["days"],
        "매도가_옳았나(하락=옳음)": outcome["right"],
        "반전있음": outcome["twist"],
    }
    prose = gen_prose(fact)
    # prose 필드를 outcome에 병합 (렌더용 _키)
    for k in ("title", "hero_lead", "sit_para", "sit_para2", "trend_chip",
              "ai_action_word", "ai_reason1", "ai_reason2", "twist_h2", "twist_para",
              "verdict_h2", "verdict_p", "lesson_para"):
        outcome["_" + k] = prose.get(k, "")
    outcome["_fb_buy"] = prose.get("fb_buy", "")
    outcome["_fb_hold"] = prose.get("fb_hold", "")
    outcome["_fb_sell"] = prose.get("fb_sell", "")
    outcome["_hero_lead"] = prose.get("hero_lead", "")
    outcome["hero_lead_plain"] = re.sub(r"<[^>]+>", "", prose.get("hero_lead", ""))[:160]
    path = render(trade, outcome, seg, slug)
    log(f"✓ 생성: {path.name} ({trade['name']}, {'적중' if outcome['right'] else '오판'})")
    return {
        "slug": slug, "code": trade["code"], "name": trade["name"],
        "date": date_iso, "title": prose.get("title", trade["name"]),
        "right": outcome["right"], "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def git_publish(slugs):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "-C", str(SITE), "add", "vs/replay", "sitemap.xml", "vs/index.html"], check=True, env=env)
    msg = "feat(vs): AI 매매 복기 게임 " + ", ".join(slugs)
    subprocess.run(["git", "-C", str(SITE), "commit", "-q", "-m", msg], check=True, env=env)
    subprocess.run(["git", "-C", str(SITE), "push", "-q"], check=True, env=env)
    log("✓ git push 완료")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade", nargs=2, metavar=("CODE", "DATE"))
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()

    manifest = load_manifest()
    done = {(r["code"], r["date"]) for r in manifest}
    sells = load_sells()

    targets = []
    if args.trade:
        code, dt = args.trade
        targets = [t for t in sells if t["code"] == code and t["timestamp"][:10] == dt]
        if not targets:
            log(f"해당 매도 없음: {code} {dt}"); sys.exit(1)
    elif args.auto:
        targets = [t for t in sells if (t["code"], t["timestamp"][:10]) not in done]
        if not targets:
            log("새로 만들 매도 없음 (모두 생성됨)"); return
    else:
        log("모드 지정 필요: --trade CODE DATE | --auto"); sys.exit(1)

    new = []
    for t in targets:
        r = make_one(t)
        if r:
            new.append(r)
            if args.auto:   # 자동은 성공 1건이면 종료 (기간미달은 다음 후보로)
                break

    if not new:
        return
    # 매니페스트(최신순) + 목록 + 사이트맵
    manifest = new + [m for m in manifest if m["slug"] not in {r["slug"] for r in new}]
    manifest.sort(key=lambda x: x["date"], reverse=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    build_index(manifest)
    update_sitemap([r["slug"] for r in new])
    log(f"목록·사이트맵 갱신 (총 {len(manifest)}개 복기)")

    if args.publish:
        git_publish([r["slug"] for r in new])
        for r in new:
            verdict = "✅ AI 적중" if r["right"] else "❌ AI 오판"
            tg(f"🎬 새 매매 복기 게시 ({verdict})\n{r['title']}\n"
               f"https://manddo.kr/vs/replay/{r['slug']}.html")
    else:
        log("검토 대기: 확인 후 --publish 로 게시하거나 수동 git push")


if __name__ == "__main__":
    main()
