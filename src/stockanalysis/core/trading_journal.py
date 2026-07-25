"""
trading_journal.py
===================
AI-coached trade journal: capture what happened before, during, and after a
trade (plan, execution, psychology, rule adherence), compute the R-multiple/
return/expectancy math, and — if ANTHROPIC_API_KEY is set — send the trade to
Claude for an objective post-mortem (grades, top mistakes/strengths, one
sentence of coaching advice). Storage is a single JSON array at
data/journal_trades.json, matching the flat-file convention already used by
data/watchlists.json and data/portfolio.csv: this is a local, single-user
tool, so a real DB buys nothing but a new failure mode (see webapp/app.py's
docstring for the same argument applied to the server itself).

Trade record shape (one dict per trade, see new_trade_from_form()):
    id, created_at, ticker, direction, date, time, market, sector,
    trade_plan, market_context, execution, trade_result, psychology,
    rule_checklist, post_trade_review, ai_feedback (None until reviewed)

Aggregate analytics (win rate, expectancy, setup/time-of-day/emotion
breakdowns, rule-violation cost) operate over a list of these records and are
pure functions of already-computed trade_result — no network, no I/O.
"""

from __future__ import annotations

import json
import os
import statistics
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
JOURNAL_PATH = DATA_DIR / "journal_trades.json"

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed — fall back to real env vars only

CHECKLIST_LABELS = {
    "deviated_from_plan": "Deviated from plan",
    "hesitation":       "Hesitated on entry",
    "late_entry":       "Late entry",
    "early_exit":       "Early exit",
    "fomo":             "FOMO",
    "revenge_trade":    "Revenge trade",
    "overtrading":      "Overtrading",
    "impulsive":        "Impulsive decision",
    "stop_moved":       "Moved stop",
    "target_changed":   "Changed target",
    "averaged_down":    "Averaged down",
    "emotional":        "Emotional decision",
}


# ─────────────────────────────────────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────────────────────────────────────

def load_trades() -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        return json.loads(JOURNAL_PATH.read_text())
    except Exception:
        return []


def save_trades(trades: list[dict]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.write_text(json.dumps(trades, indent=2))


def get_trade(trade_id: str) -> dict | None:
    return next((t for t in load_trades() if t.get("id") == trade_id), None)


def add_trade(form: dict) -> dict:
    trade = new_trade_from_form(form)
    trades = load_trades()
    trades.append(trade)
    save_trades(trades)
    return trade


def update_trade(trade_id: str, patch: dict) -> dict | None:
    trades = load_trades()
    for t in trades:
        if t.get("id") == trade_id:
            t.update(patch)
            save_trades(trades)
            return t
    return None


def update_trade_from_form(trade_id: str, form: dict) -> dict | None:
    """Edit an existing trade in place from a resubmitted journal form —
    see apply_form_update() for why the two are split (identity/AI feedback
    preserved, everything else re-parsed and trade_result recomputed)."""
    trades = load_trades()
    for t in trades:
        if t.get("id") == trade_id:
            apply_form_update(t, form)
            save_trades(trades)
            return t
    return None


def delete_trade(trade_id: str) -> bool:
    trades = load_trades()
    kept = [t for t in trades if t.get("id") != trade_id]
    if len(kept) == len(trades):
        return False
    save_trades(kept)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# FORM → TRADE RECORD
# ─────────────────────────────────────────────────────────────────────────────

def _first(form: dict, key: str, default: str = "") -> str:
    return (form.get(key) or [default])[0]


def _num(form: dict, key: str) -> float | None:
    v = _first(form, key).strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _bool(form: dict, key: str) -> bool:
    return key in form  # unchecked checkboxes are omitted by the browser entirely


def _sections_from_form(form: dict) -> dict:
    """Parse every editable section out of a submitted journal form
    (parse_qs-style dict of lists) — everything except the record identity
    (id/created_at) and the AI coach's own output, so this can back both a
    brand-new trade and an in-place edit of an existing one."""
    plan = {
        "why":                _first(form, "plan_why"),
        "setup_name":         _first(form, "plan_setup_name"),
        "setup_grade":        _first(form, "plan_setup_grade"),
        "higher_tf_trend":    _first(form, "plan_htf_trend"),
        "lower_tf_trigger":   _first(form, "plan_ltf_trigger"),
        "entry":              _num(form, "plan_entry"),
        "stop":               _num(form, "plan_stop"),
        "target1":            _num(form, "plan_target1"),
        "target2":            _num(form, "plan_target2"),
        "risk_pct":           _num(form, "plan_risk_pct"),
        "expected_reward":    _num(form, "plan_expected_reward"),
        "max_loss_accepted":  _num(form, "plan_max_loss"),
        "max_gain_expected":  _num(form, "plan_max_gain"),
    }
    plan["risk_reward"] = _risk_reward(plan)

    market_context = {
        "earnings_considered": _bool(form, "ctx_earnings"),
        "fed_considered":      _bool(form, "ctx_fed"),
        "vix_considered":      _bool(form, "ctx_vix"),
        "yields_considered":   _bool(form, "ctx_yields"),
        "news_considered":     _bool(form, "ctx_news"),
        "notes":               _first(form, "ctx_notes"),
    }

    execution = {
        "actual_entry":    _num(form, "exec_actual_entry"),
        "actual_exit":     _num(form, "exec_actual_exit"),
        "partial_exits":   _first(form, "exec_partial_exits"),
        "position_size":   _first(form, "exec_position_size"),
        "scaling":         _first(form, "exec_scaling"),
        "duration_minutes": _num(form, "exec_duration_minutes"),
        "mfe":             _num(form, "exec_mfe"),
        "mae":             _num(form, "exec_mae"),
        "deviated_from_plan": _bool(form, "exec_deviated_from_plan"),
        "hesitation":      _bool(form, "exec_hesitation"),
        "late_entry":      _bool(form, "exec_late_entry"),
        "early_exit":      _bool(form, "exec_early_exit"),
        "fomo":            _bool(form, "exec_fomo"),
        "revenge_trade":   _bool(form, "exec_revenge"),
        "overtrading":     _bool(form, "exec_overtrading"),
    }

    psychology = {
        "before":     _first(form, "psych_before"),
        "during":     _first(form, "psych_during"),
        "after":      _first(form, "psych_after"),
        "stress":     _num(form, "psych_stress"),
        "confidence": _num(form, "psych_confidence"),
        "discipline": _num(form, "psych_discipline"),
        "patience":   _num(form, "psych_patience"),
        "focus":      _num(form, "psych_focus"),
        "fear":       _first(form, "psych_fear"),
        "greed":      _first(form, "psych_greed"),
        "hope":       _first(form, "psych_hope"),
        "regret":     _first(form, "psych_regret"),
        "fomo_note":  _first(form, "psych_fomo_note"),
    }

    rule_checklist = {
        "rules_broken":     _first(form, "chk_rules_broken"),
        "impulsive":        _bool(form, "chk_impulsive"),
        "stop_moved":       _bool(form, "chk_stop_moved"),
        "target_changed":   _bool(form, "chk_target_changed"),
        "averaged_down":    _bool(form, "chk_averaged_down"),
        "emotional":        _bool(form, "chk_emotional"),
    }

    post_trade_review = {
        "what_worked":   _first(form, "rev_worked"),
        "what_failed":   _first(form, "rev_failed"),
        "repeat":        _first(form, "rev_repeat"),
        "never_again":   _first(form, "rev_never_again"),
    }

    return {
        "ticker":     _first(form, "ticker").strip().upper(),
        "direction":  _first(form, "direction", "Long"),
        "date":       _first(form, "date"),
        "time":       _first(form, "time"),
        "market":     _first(form, "market"),
        "sector":     _first(form, "sector"),
        "trade_plan": plan,
        "market_context": market_context,
        "execution":  execution,
        "psychology": psychology,
        "rule_checklist": rule_checklist,
        "post_trade_review": post_trade_review,
    }


def trade_to_form_dict(t: dict) -> dict:
    """Inverse of _sections_from_form: flatten a trade record back into the
    same flat field names the journal form uses, so the webapp's Edit
    button can prefill every field with one JS loop over form.elements
    instead of hand-wiring each one."""
    plan, ctx = t.get("trade_plan", {}), t.get("market_context", {})
    execu = t.get("execution", {})
    psych, chk = t.get("psychology", {}), t.get("rule_checklist", {})
    review = t.get("post_trade_review", {})
    return {
        "trade_id": t.get("id"),
        "ticker": t.get("ticker"), "direction": t.get("direction"),
        "date": t.get("date"), "time": t.get("time"),
        "market": t.get("market"), "sector": t.get("sector"),
        "plan_why": plan.get("why"), "plan_setup_name": plan.get("setup_name"),
        "plan_setup_grade": plan.get("setup_grade"),
        "plan_htf_trend": plan.get("higher_tf_trend"), "plan_ltf_trigger": plan.get("lower_tf_trigger"),
        "plan_entry": plan.get("entry"), "plan_stop": plan.get("stop"),
        "plan_target1": plan.get("target1"), "plan_target2": plan.get("target2"),
        "plan_risk_pct": plan.get("risk_pct"), "plan_expected_reward": plan.get("expected_reward"),
        "plan_max_loss": plan.get("max_loss_accepted"), "plan_max_gain": plan.get("max_gain_expected"),
        "ctx_earnings": ctx.get("earnings_considered"), "ctx_fed": ctx.get("fed_considered"),
        "ctx_vix": ctx.get("vix_considered"), "ctx_yields": ctx.get("yields_considered"),
        "ctx_news": ctx.get("news_considered"), "ctx_notes": ctx.get("notes"),
        "exec_actual_entry": execu.get("actual_entry"), "exec_actual_exit": execu.get("actual_exit"),
        "exec_partial_exits": execu.get("partial_exits"), "exec_position_size": execu.get("position_size"),
        "exec_scaling": execu.get("scaling"), "exec_duration_minutes": execu.get("duration_minutes"),
        "exec_mfe": execu.get("mfe"), "exec_mae": execu.get("mae"),
        "exec_deviated_from_plan": execu.get("deviated_from_plan"), "exec_hesitation": execu.get("hesitation"),
        "exec_late_entry": execu.get("late_entry"), "exec_early_exit": execu.get("early_exit"),
        "exec_fomo": execu.get("fomo"), "exec_revenge": execu.get("revenge_trade"),
        "exec_overtrading": execu.get("overtrading"),
        "psych_before": psych.get("before"), "psych_during": psych.get("during"), "psych_after": psych.get("after"),
        "psych_stress": psych.get("stress"), "psych_confidence": psych.get("confidence"),
        "psych_discipline": psych.get("discipline"), "psych_patience": psych.get("patience"),
        "psych_focus": psych.get("focus"),
        "psych_fear": psych.get("fear"), "psych_greed": psych.get("greed"),
        "psych_hope": psych.get("hope"), "psych_regret": psych.get("regret"),
        "psych_fomo_note": psych.get("fomo_note"),
        "chk_rules_broken": chk.get("rules_broken"), "chk_impulsive": chk.get("impulsive"),
        "chk_stop_moved": chk.get("stop_moved"), "chk_target_changed": chk.get("target_changed"),
        "chk_averaged_down": chk.get("averaged_down"), "chk_emotional": chk.get("emotional"),
        "rev_worked": review.get("what_worked"), "rev_failed": review.get("what_failed"),
        "rev_repeat": review.get("repeat"), "rev_never_again": review.get("never_again"),
    }


def new_trade_from_form(form: dict) -> dict:
    trade = {
        "id":         uuid.uuid4().hex[:12],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ai_feedback": None,
        **_sections_from_form(form),
    }
    trade["trade_result"] = compute_trade_result(trade)
    return trade


def apply_form_update(trade: dict, form: dict) -> dict:
    """Re-parse an edited form into an existing trade record in place —
    used when a trade was logged with only partial detail (e.g. the plan,
    before the trade closed) and the trader comes back later to fill in
    execution/psychology/lessons. Identity (id/created_at) and any AI coach
    feedback already on the record are left untouched; trade_result is
    recomputed since entry/exit/stop may have changed."""
    trade.update(_sections_from_form(form))
    trade["trade_result"] = compute_trade_result(trade)
    return trade


def _risk_reward(plan: dict) -> float | None:
    entry, stop, target1 = plan.get("entry"), plan.get("stop"), plan.get("target1")
    if entry is None or stop is None or target1 is None or entry == stop:
        return None
    risk = abs(entry - stop)
    reward = abs(target1 - entry)
    return round(reward / risk, 2) if risk else None


def compute_trade_result(trade: dict) -> dict:
    """R multiple / return % from actual fills vs. the planned stop — the one
    piece of the record that's arithmetic rather than free-form input."""
    plan, execu = trade.get("trade_plan", {}), trade.get("execution", {})
    entry, exit_ = execu.get("actual_entry"), execu.get("actual_exit")
    stop = plan.get("stop")
    is_short = (trade.get("direction") or "Long").lower() == "short"

    pnl_per_share = r_multiple = return_pct = None
    if entry is not None and exit_ is not None:
        pnl_per_share = (entry - exit_) if is_short else (exit_ - entry)
        return_pct = round(pnl_per_share / entry * 100, 2) if entry else None
        if stop is not None and stop != entry:
            risk_per_share = abs(entry - stop)
            r_multiple = round(pnl_per_share / risk_per_share, 2) if risk_per_share else None

    return {
        "pnl_per_share":    round(pnl_per_share, 4) if pnl_per_share is not None else None,
        "r_multiple":       r_multiple,
        "return_pct":       return_pct,
        "duration_minutes": execu.get("duration_minutes"),
        "mfe":              execu.get("mfe"),
        "mae":              execu.get("mae"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# AI COACH — optional, degrades to None with no key configured
# ─────────────────────────────────────────────────────────────────────────────

def build_coach_prompt(trade: dict) -> str:
    plan, ctx = trade.get("trade_plan", {}), trade.get("market_context", {})
    execu, result = trade.get("execution", {}), trade.get("trade_result", {})
    psych, chk = trade.get("psychology", {}), trade.get("rule_checklist", {})
    review = trade.get("post_trade_review", {})

    def y(v):
        return "yes" if v else "no"

    return f"""You are an experienced trading psychologist and professional portfolio manager.
Your role is to review this completed trade objectively. Do NOT judge the
trader. Your goal is to identify strengths, weaknesses, recurring mistakes,
and areas for improvement.

TRADE INFORMATION
Ticker: {trade.get('ticker')}   Direction: {trade.get('direction')}
Date: {trade.get('date')}   Time: {trade.get('time')}
Market: {trade.get('market')}   Sector: {trade.get('sector')}

TRADE PLAN
Why taken: {plan.get('why')}
Setup: {plan.get('setup_name')} (rated {plan.get('setup_grade')})
Higher timeframe trend: {plan.get('higher_tf_trend')}
Lower timeframe trigger: {plan.get('lower_tf_trigger')}
Entry {plan.get('entry')} / Stop {plan.get('stop')} / Target1 {plan.get('target1')} / Target2 {plan.get('target2')}
Risk % {plan.get('risk_pct')} / Expected reward {plan.get('expected_reward')} / Risk:Reward {plan.get('risk_reward')}
Max loss accepted {plan.get('max_loss_accepted')} / Max gain expected {plan.get('max_gain_expected')}
Considered: earnings={y(ctx.get('earnings_considered'))}, Fed={y(ctx.get('fed_considered'))}, VIX={y(ctx.get('vix_considered'))}, yields={y(ctx.get('yields_considered'))}, news={y(ctx.get('news_considered'))}
Market context notes: {ctx.get('notes')}

EXECUTION
Actual entry {execu.get('actual_entry')} / Actual exit {execu.get('actual_exit')}
Partial exits: {execu.get('partial_exits')}
Position sizing: {execu.get('position_size')}   Scaling: {execu.get('scaling')}
Deviated from plan: {y(execu.get('deviated_from_plan'))}   Hesitation: {y(execu.get('hesitation'))}
Late entry: {y(execu.get('late_entry'))}   Early exit: {y(execu.get('early_exit'))}
FOMO: {y(execu.get('fomo'))}   Revenge trade: {y(execu.get('revenge_trade'))}   Overtrading: {y(execu.get('overtrading'))}

RESULT
R multiple: {result.get('r_multiple')}   Return %: {result.get('return_pct')}
Duration (min): {result.get('duration_minutes')}
MFE: {result.get('mfe')}   MAE: {result.get('mae')}

EMOTIONS (1-10 unless noted)
Before: {psych.get('before')}
During: {psych.get('during')}
After: {psych.get('after')}
Stress {psych.get('stress')} / Confidence {psych.get('confidence')} / Discipline {psych.get('discipline')} / Patience {psych.get('patience')} / Focus {psych.get('focus')}
Fear: {psych.get('fear')}   Greed: {psych.get('greed')}   Hope: {psych.get('hope')}   Regret: {psych.get('regret')}   FOMO note: {psych.get('fomo_note')}

CHECKLIST
Rules broken: {chk.get('rules_broken') or 'none noted'}
Impulsive decision: {y(chk.get('impulsive'))}   Stop moved: {y(chk.get('stop_moved'))}
Target changed: {y(chk.get('target_changed'))}   Averaged down: {y(chk.get('averaged_down'))}
Emotional decision: {y(chk.get('emotional'))}

LESSONS (trader's own notes)
What worked: {review.get('what_worked')}
What failed: {review.get('what_failed')}
Should repeat: {review.get('repeat')}
Should never happen again: {review.get('never_again')}

OUTPUT
Return JSON only, no prose outside the JSON, with exactly these keys:
{{
  "overall_grade": "A-F letter grade",
  "execution_score": 0-100,
  "planning_score": 0-100,
  "psychology_score": 0-100,
  "risk_management_score": 0-100,
  "setup_quality_score": 0-100,
  "edge_score": 0-100,
  "top_mistakes": ["...", "...", "..."],
  "top_strengths": ["...", "...", "..."],
  "improvement_suggestions": ["...", "..."],
  "confidence_in_future_similar_setup": "one short phrase",
  "coaching_advice": "one sentence"
}}"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        text = text[4:] if text.startswith("json") else text
    return json.loads(text.strip())


def run_ai_coach_review(trade: dict) -> dict:
    """Calls Claude with the trade's full context and returns the parsed
    coaching JSON. Raises (rather than degrading to None) when called
    directly — the webapp's journal_review job is the "optional" boundary
    (it surfaces the exception as a failed job so the button gives clear
    feedback instead of silently doing nothing)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — add it to .env to enable AI coach reviews")
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": build_coach_prompt(trade)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    try:
        return _parse_json_response(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"AI coach returned non-JSON output: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATE ANALYTICS — pure functions over a list of trade records
# ─────────────────────────────────────────────────────────────────────────────

def _r_values(trades: list[dict]) -> list[float]:
    return [t["trade_result"]["r_multiple"] for t in trades
            if t.get("trade_result", {}).get("r_multiple") is not None]


def aggregate_metrics(trades: list[dict]) -> dict:
    rs = _r_values(trades)
    if not rs:
        return {"count": 0}

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    win_rate = round(len(wins) / len(rs) * 100, 1)
    avg_r = round(statistics.mean(rs), 2)
    avg_winner = round(statistics.mean(wins), 2) if wins else None
    avg_loser = round(statistics.mean(losses), 2) if losses else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss else None
    expectancy = round(avg_r, 2)

    durations = [t["trade_result"]["duration_minutes"] for t in trades
                 if t.get("trade_result", {}).get("duration_minutes") is not None]

    # equity curve in R, for max drawdown
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    def _streaks(vals: list[bool]) -> int:
        best = cur = 0
        for v in vals:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return best

    is_win = [r > 0 for r in rs]

    return {
        "count":            len(rs),
        "win_rate":         win_rate,
        "avg_r":            avg_r,
        "expectancy":       expectancy,
        "profit_factor":    profit_factor,
        "avg_winner_r":     avg_winner,
        "avg_loser_r":      avg_loser,
        "avg_holding_minutes": round(statistics.mean(durations), 1) if durations else None,
        "max_drawdown_r":  round(max_dd, 2),
        "max_consecutive_wins":   _streaks(is_win),
        "max_consecutive_losses": _streaks([not w for w in is_win]),
    }


def _bucket_stats(groups: dict[str, list[float]]) -> list[dict]:
    out = []
    for key, rs in groups.items():
        if not rs:
            continue
        wins = sum(1 for r in rs if r > 0)
        out.append({
            "key": key, "count": len(rs),
            "win_rate": round(wins / len(rs) * 100, 1),
            "avg_r": round(statistics.mean(rs), 2),
        })
    return sorted(out, key=lambda d: d["avg_r"], reverse=True)


def setup_performance(trades: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        r = t.get("trade_result", {}).get("r_multiple")
        setup = t.get("trade_plan", {}).get("setup_name") or "(unnamed)"
        if r is not None:
            groups.setdefault(setup, []).append(r)
    return _bucket_stats(groups)


def time_of_day_performance(trades: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        r = t.get("trade_result", {}).get("r_multiple")
        hhmm = t.get("time") or ""
        if r is None or ":" not in hhmm:
            continue
        hour = hhmm.split(":")[0].zfill(2)
        groups.setdefault(f"{hour}:00", []).append(r)
    return _bucket_stats(groups)


def day_of_week_performance(trades: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        r = t.get("trade_result", {}).get("r_multiple")
        date_s = t.get("date") or ""
        if r is None or not date_s:
            continue
        try:
            day = datetime.strptime(date_s, "%Y-%m-%d").strftime("%A")
        except ValueError:
            continue
        groups.setdefault(day, []).append(r)
    return _bucket_stats(groups)


def trend_performance(trades: list[dict]) -> list[dict]:
    """Performance grouped by the higher-timeframe trend noted at plan time —
    the closest proxy this data model has to "market regime" without a
    separate regime classifier."""
    groups: dict[str, list[float]] = {}
    for t in trades:
        r = t.get("trade_result", {}).get("r_multiple")
        trend = t.get("trade_plan", {}).get("higher_tf_trend") or "(not noted)"
        if r is not None:
            groups.setdefault(trend, []).append(r)
    return _bucket_stats(groups)


def emotion_correlation(trades: list[dict]) -> list[dict]:
    """For each psychology dimension, the average R multiple when the trader
    scored themselves high (>=7) vs low (<=4) on that dimension, plus a
    simple Pearson correlation coefficient against R — lets a pattern like
    "low patience -> worse trades" surface without any external stats lib."""
    dims = ("stress", "confidence", "discipline", "patience", "focus")
    out = []
    for dim in dims:
        pairs = [(t["psychology"].get(dim), t["trade_result"]["r_multiple"])
                 for t in trades
                 if t.get("psychology", {}).get(dim) is not None
                 and t.get("trade_result", {}).get("r_multiple") is not None]
        if len(pairs) < 2:
            continue
        scores = [p[0] for p in pairs]
        rs = [p[1] for p in pairs]
        high_r = [r for s, r in pairs if s >= 7]
        low_r = [r for s, r in pairs if s <= 4]
        corr = _pearson(scores, rs)
        out.append({
            "dimension": dim,
            "avg_r_high": round(statistics.mean(high_r), 2) if high_r else None,
            "avg_r_low": round(statistics.mean(low_r), 2) if low_r else None,
            "correlation_with_r": round(corr, 2) if corr is not None else None,
        })
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    return cov / (sx * sy) if sx and sy else None


def rule_violation_stats(trades: list[dict]) -> list[dict]:
    """How often each rule violation occurs, and its cost — average R on
    trades where it happened vs. where it didn't."""
    flags = list(CHECKLIST_LABELS.keys())
    out = []
    for flag in flags:
        with_r, without_r = [], []
        for t in trades:
            r = t.get("trade_result", {}).get("r_multiple")
            if r is None:
                continue
            present = t.get("execution", {}).get(flag) if flag in t.get("execution", {}) \
                else t.get("rule_checklist", {}).get(flag)
            (with_r if present else without_r).append(r)
        if not with_r:
            continue
        out.append({
            "violation": CHECKLIST_LABELS[flag],
            "count": len(with_r),
            "avg_r_when_present": round(statistics.mean(with_r), 2),
            "avg_r_when_absent": round(statistics.mean(without_r), 2) if without_r else None,
        })
    return sorted(out, key=lambda d: d["count"], reverse=True)


def risk_consistency(trades: list[dict]) -> dict:
    risk_pcts = [t["trade_plan"]["risk_pct"] for t in trades
                 if t.get("trade_plan", {}).get("risk_pct") is not None]
    if len(risk_pcts) < 2:
        return {"count": len(risk_pcts)}
    return {
        "count": len(risk_pcts),
        "avg_risk_pct": round(statistics.mean(risk_pcts), 2),
        "stdev_risk_pct": round(statistics.stdev(risk_pcts), 2),
        "max_risk_pct": max(risk_pcts),
    }


def monthly_review(trades: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for t in trades:
        date_s = t.get("date") or ""
        if len(date_s) < 7:
            continue
        groups.setdefault(date_s[:7], []).append(t)

    out = []
    for month, month_trades in sorted(groups.items(), reverse=True):
        rs = _r_values(month_trades)
        setups = setup_performance(month_trades)
        mistakes: dict[str, int] = {}
        for t in month_trades:
            for m in (t.get("ai_feedback") or {}).get("top_mistakes") or []:
                mistakes[m] = mistakes.get(m, 0) + 1
        top_mistake = max(mistakes, key=mistakes.get) if mistakes else None
        out.append({
            "month": month,
            "count": len(month_trades),
            "win_rate": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1) if rs else None,
            "avg_r": round(statistics.mean(rs), 2) if rs else None,
            "best_setup": setups[0]["key"] if setups else None,
            "top_recurring_mistake": top_mistake,
        })
    return out
