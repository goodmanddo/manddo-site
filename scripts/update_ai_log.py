#!/usr/bin/env python3
"""
한투 잔고·체결 → ai-log/data.json 생성 → git commit/push

매일 15:30 LaunchAgent로 실행. 장 마감 후 정리된 기록을 사이트에 반영.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, date, time, timedelta
from pathlib import Path


def last_market_date():
    """마지막 장 마감일 반환. 한국 주식 기준 (주말 제외, 공휴일 미반영).

    오늘이 평일이고 15:30 이후면 오늘, 아니면 직전 평일을 반환.
    """
    now = datetime.now()
    today = now.date()
    if today.weekday() < 5 and now.time() >= time(15, 30):
        return today
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

sys.path.insert(0, str(Path.home() / "stock_auto_trade"))
from kis_api import KISApi  # noqa: E402

ROOT = Path.home() / "manddo-site"
AI_LOG_DIR = ROOT / "ai-log"
DATA_FILE = AI_LOG_DIR / "data.json"
CONFIG_FILE = AI_LOG_DIR / "config.json"
STATE_FILE = AI_LOG_DIR / ".state.json"
LOG_FILE = Path.home() / "manddo-site" / "scripts" / "update_ai_log.log"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def default_reason(kind, name, pct=None):
    if kind == "buy":
        return f"{name} 신규 편입. 포트폴리오 비중 조정."
    if kind == "win":
        return f"수익 구간 진입. 분할 익절로 수익 확정."
    if kind == "loss":
        return f"손절 규칙 적용. 다음 진입 시점까지 관망."
    return ""


def default_commentary(new_buys, exits, cumulative):
    parts = []
    if new_buys:
        names = ", ".join(b["name"] for b in new_buys[:3])
        parts.append(f"오늘은 {names} 등 신규 편입.")
    if exits:
        wins = [e for e in exits if e["type"] == "win"]
        losses = [e for e in exits if e["type"] == "loss"]
        if wins:
            parts.append(f"{len(wins)}건 익절로 수익 확정.")
        if losses:
            parts.append(f"{len(losses)}건은 손절 규칙에 따라 정리.")
    if not parts:
        parts.append("오늘은 신규 매매 없이 포지션 유지 전략.")
    parts.append(f"시작 이후 누적 {'+' if cumulative >= 0 else ''}{cumulative:.1f}%.")
    return " ".join(parts)


def to_slug(name, code, slug_map):
    return slug_map.get(name) or slug_map.get(code) or ""


def market_label(code):
    # 코스피 종목코드는 보통 0으로 시작하되 엄밀 분류는 어려움. 표시용 간이 판정.
    try:
        n = int(code)
        # 코스닥 주요 대역
        if code.startswith("0") and (15000 <= n <= 29999 or 100000 <= n <= 499999):
            return "코스닥"
    except Exception:
        pass
    return "코스피"


def run_git(*args, cwd=ROOT):
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def git_commit_and_push():
    code, out, err = run_git("status", "--porcelain", "ai-log/")
    if not out.strip():
        log("변경사항 없음 — 커밋 스킵")
        return False
    run_git("add", "ai-log/data.json", "ai-log/.state.json")
    msg = f"AI 매매 일지 자동 업데이트 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    code, out, err = run_git("commit", "-m", msg)
    if code != 0:
        log(f"git commit 실패: {err or out}")
        return False
    code, out, err = run_git("push", "origin", "main")
    if code != 0:
        log(f"git push 실패: {err or out}")
        return False
    log("git push 완료")
    return True


def main():
    market_d = last_market_date()
    today = market_d.isoformat()  # 표시·기록용 기준일 = 마지막 장 마감일
    log(f"업데이트 시작 (기준일 {today}, 실행일 {date.today().isoformat()})")

    config = load_json(CONFIG_FILE, {})
    slug_map = config.get("slug_map", {})
    start_date = config.get("start_date") or today
    initial_capital = int(config.get("initial_capital") or 0)
    overrides = config.get("overrides", {}).get(today, {})
    override_reasons = overrides.get("reasons", {})
    override_commentary = overrides.get("commentary")
    override_weekly_losses = overrides.get("weekly_losses")

    state = load_json(STATE_FILE, {"last_holdings": {}, "history": [], "completed_trades": []})
    prev_holdings = state.get("last_holdings", {})

    api = KISApi()
    bal = api.get_balance()
    holdings = {h["code"]: h for h in bal["holdings"]}
    # KIS tot_evlu_amt는 이미 주식+현금 합계(총자산)
    total_eval = bal["total_eval"] or (sum(h["current_price"] * h["qty"] for h in bal["holdings"]) + bal["cash"])

    # 초기 자본: 최초 실행 시 세팅
    if initial_capital <= 0:
        initial_capital = total_eval
        config["initial_capital"] = initial_capital
        save_json(CONFIG_FILE, config)
        log(f"초기 자본 설정: {initial_capital:,}")

    # 누적 수익률
    cumulative = 0.0
    if initial_capital > 0:
        cumulative = (total_eval - initial_capital) / initial_capital * 100

    # 주간 수익률 (5영업일 전 대비)
    history = state.get("history", [])
    week_return = 0.0
    if history:
        ref = history[-min(5, len(history))]
        if ref.get("total_eval", 0) > 0:
            week_return = (total_eval - ref["total_eval"]) / ref["total_eval"] * 100

    # 오늘 포트폴리오 변화율 (직전 기록 대비)
    day_return = 0.0
    # 같은 날 기록이 이미 있을 수 있으므로 오늘이 아닌 가장 최근 항목과 비교
    prev_entry = None
    for h in reversed(history):
        if h.get("date") != today:
            prev_entry = h
            break
    if prev_entry and prev_entry.get("total_eval", 0) > 0:
        day_return = (total_eval - prev_entry["total_eval"]) / prev_entry["total_eval"] * 100

    # 신규 매수: 최근 7일 내 편입된 보유 종목 (당일 신규 + 최근 며칠 내 편입)
    new_buys = []
    for code, h in holdings.items():
        prev = prev_holdings.get(code, {})
        opened_at_str = prev.get("opened_at") or today
        try:
            opened_at = datetime.strptime(opened_at_str, "%Y-%m-%d").date()
        except Exception:
            opened_at = market_d
        days_held = (market_d - opened_at).days
        if days_held > 7:
            continue
        name = h["name"]
        slug = to_slug(name, code, slug_map)
        if not slug:
            log(f"신규 매수 (slug 미등록, 카드만 표시): {name}/{code}")
        entry = h.get("avg_price") or h.get("current_price") or 0
        cur = h.get("current_price") or entry
        pct = ((cur - entry) / entry * 100) if entry else 0.0
        weight = 0
        if total_eval > 0 and h.get("current_price") and h.get("qty"):
            weight = round(h["current_price"] * h["qty"] / total_eval * 100)
        new_buys.append({
            "name": name,
            "slug": slug,
            "market": market_label(code),
            "entry_to_current_pct": round(pct, 2),
            "weight_pct": weight,
            "hold_days": days_held,
            "reason": override_reasons.get(name) or default_reason("buy", name),
        })
    new_buys.sort(key=lambda x: x["hold_days"])  # 최근 편입부터

    # 청산: 어제 있던 종목이 오늘 없음 → completed에 기록 (동일일+코드 중복 방지)
    completed = state.get("completed_trades", [])
    completed_keys = {(c.get("code"), c.get("date")) for c in completed}
    for code, prev in prev_holdings.items():
        if code in holdings:
            continue
        if (code, today) in completed_keys:
            continue
        name = prev.get("name") or code
        if not to_slug(name, code, slug_map):
            log(f"청산 (slug 미등록, 카드만 표시): {name}/{code}")
        try:
            cur = api.get_current_price(code).get("price") or 0
        except Exception:
            cur = 0
        avg = prev.get("avg_price") or 0
        pct = ((cur - avg) / avg * 100) if (avg and cur) else (prev.get("profit_rate") or 0.0)
        pct = round(pct, 2)
        hold_days = 0
        if prev.get("opened_at"):
            try:
                opened = datetime.strptime(prev["opened_at"], "%Y-%m-%d").date()
                hold_days = (market_d - opened).days
            except Exception:
                pass
        completed.append({
            "name": name, "code": code, "return_pct": pct, "date": today,
            "is_win": pct >= 0, "hold_days": hold_days,
        })
        completed_keys.add((code, today))

    # 오늘의 매도: completed 중 오늘 날짜 거래, 1% 이상만 노출
    exits = []
    for c in completed:
        if c.get("date") != today:
            continue
        pct = c.get("return_pct", 0)
        if abs(pct) < 1.0:
            continue
        etype = "win" if c.get("is_win") else "loss"
        name = c["name"]
        code = c.get("code", "")
        exits.append({
            "type": etype,
            "name": name,
            "slug": to_slug(name, code, slug_map),
            "market": market_label(code),
            "return_pct": pct,
            "hold_days": c.get("hold_days", 0),
            "reason": override_reasons.get(name) or default_reason(etype, name, pct),
        })

    # 오늘 실현 손익 집계 (완료 거래 중 오늘 날짜 전체, 필터 적용 전)
    today_trades = [c for c in completed if c.get("date") == today]
    today_trade_count = len(today_trades)
    today_win_count = sum(1 for c in today_trades if c.get("is_win"))
    today_loss_count = today_trade_count - today_win_count
    today_realized_avg = 0.0
    if today_trade_count:
        today_realized_avg = sum(c.get("return_pct", 0) for c in today_trades) / today_trade_count

    # 승률 (완료 거래 기준)
    if completed:
        wins = sum(1 for c in completed if c.get("is_win"))
        win_rate = round(wins / len(completed) * 100)
    else:
        win_rate = 0

    # 주간 손실 공개: 기준일로부터 최근 7일 내 손절 거래
    today_d = market_d
    weekly_losses = []
    for c in reversed(completed):
        try:
            d = datetime.strptime(c["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if (today_d - d).days > 7:
            break
        if c.get("is_win"):
            continue
        if abs(c.get("return_pct", 0)) < 3.0:
            continue
        slug = to_slug(c["name"], c.get("code", ""), slug_map)
        weekly_losses.append({
            "name": c["name"],
            "slug": slug,
            "return_pct": c["return_pct"],
            "lesson": override_reasons.get(c["name"] + "_lesson") or "손절 규칙을 지킨 건 맞지만, 진입 시점 재검토 필요.",
        })
        if len(weekly_losses) >= 5:
            break

    commentary = override_commentary or default_commentary(new_buys, exits, cumulative)

    # 현재 보유 종목 (비중%만 공개)
    current_holdings = []
    for code, h in holdings.items():
        weight = 0
        if total_eval > 0 and h.get("current_price") and h.get("qty"):
            weight = round(h["current_price"] * h["qty"] / total_eval * 100)
        current_holdings.append({
            "name": h["name"],
            "slug": to_slug(h["name"], code, slug_map),
            "market": market_label(code),
            "weight_pct": weight,
        })
    current_holdings.sort(key=lambda x: x["weight_pct"], reverse=True)

    # weekly_losses: override 우선
    if override_weekly_losses:
        weekly_losses = override_weekly_losses

    data = {
        "date": today,
        "start_date": start_date,
        "cumulative_return": round(cumulative, 2),
        "week_return": round(week_return, 2),
        "day_return": round(day_return, 2),
        "today_realized_avg": round(today_realized_avg, 2),
        "today_trade_count": today_trade_count,
        "today_win_count": today_win_count,
        "today_loss_count": today_loss_count,
        "win_rate": win_rate,
        "total_trades": len(completed),
        "ai_commentary": commentary,
        "new_buys": new_buys,
        "exits": exits,
        "weekly_losses": weekly_losses,
        "holdings": current_holdings,
    }
    save_json(DATA_FILE, data)
    log(f"data.json 저장: 매수 {len(new_buys)}, 청산 {len(exits)}, 누적 {cumulative:.2f}%")

    # 상태 저장
    new_last_holdings = {}
    for code, h in holdings.items():
        prev = prev_holdings.get(code, {})
        opened_at = prev.get("opened_at") or today  # today = 기준일(마지막 장 마감일)
        new_last_holdings[code] = {
            "name": h["name"],
            "qty": h["qty"],
            "avg_price": h["avg_price"],
            "current_price": h["current_price"],
            "opened_at": opened_at,
        }
    new_history = history + [{"date": today, "total_eval": total_eval}]
    # 최근 60일치만 보관
    new_history = new_history[-60:]
    state = {
        "last_holdings": new_last_holdings,
        "history": new_history,
        "completed_trades": completed[-200:],
    }
    save_json(STATE_FILE, state)

    # git commit/push
    try:
        git_commit_and_push()
    except Exception as e:
        log(f"git 오류: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적 오류: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
