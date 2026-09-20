"""
scheduler.py
=============
Signal daemon: no Claude/MCP dependency, no broker session. Every 30 seconds,
during market hours on a weekday, checks whether a new SPY bar has closed; if
so, recomputes the signal engine and — on a genuinely new UT Bot signal —
writes a pending_review proposal. Also watches `placed` entries for the
underlying-flip half of the exit rule.

Pattern (30s `schedule` loop, market-hours guard inside the job function)
adapted from stockanalysis/scheduling/scheduler.py.

Normally runs on a background thread inside the webapp (see webapp/app.py's
_start_spy_daemon_thread) — starting the workstation is enough to have SPY
signals firing. Can still be run standalone:

    python3 src/spydaytrader/daemon/scheduler.py

Known constraint (see README): this process cannot see live option premium,
so it cannot evaluate the premium-based half of the "both" exit rule. That
check needs a Robinhood-authenticated session — run it periodically from a
Claude Code session instead.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Daytrades.core import position_sizing, signal_engine, signal_state, trade_proposals, trading_journal
from Daytrades.daemon import market_data

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("spydaytrader.daemon")

ET = ZoneInfo("America/New_York")

_last_bar_time = None


def _config() -> dict:
    return {
        "bar_interval": os.environ.get("BAR_INTERVAL", "5m"),
        "key_value": float(os.environ.get("UT_KEY_VALUE", 2)),
        "atr_period": int(os.environ.get("UT_ATR_PERIOD", 1)),
        "hma_period": int(os.environ.get("HMA_PERIOD", 31)),
        "ma200_period": int(os.environ.get("MA200_PERIOD", 200)),
        "orb_session_start": os.environ.get("ORB_SESSION_START", "09:30"),
        "orb_minutes": int(os.environ.get("ORB_MINUTES", 5)),
        "account_size": float(os.environ.get("ACCOUNT_SIZE", 100_000)),
        "daily_loss_limit_pct": float(os.environ.get("DAILY_LOSS_LIMIT_PCT", 2)),
    }


def _within_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return (9, 30) <= (now.hour, now.minute) <= (16, 0)


def job_scan_spy(force: bool = False) -> dict:
    """force=True skips the market-hours guard and the dedup-on-same-bar
    check, for manual testing (the dashboard's "Run scan now" button)."""
    global _last_bar_time
    now = datetime.now(ET)
    if not force and not _within_market_hours(now):
        return {"skipped": "outside market hours"}

    cfg = _config()
    df = market_data.fetch_spy_bars(interval=cfg["bar_interval"])
    if df.empty:
        log.warning("no SPY bars returned")
        return {"skipped": "no bars returned"}

    last_bar_ts = df.index[-1]
    if not force and _last_bar_time is not None and last_bar_ts <= _last_bar_time:
        return {"skipped": "no new closed bar"}
    _last_bar_time = last_bar_ts

    vix = market_data.fetch_vix()
    frame = signal_engine.compute_signal_frame(
        df,
        key_value=cfg["key_value"],
        atr_period=cfg["atr_period"],
        hma_period=cfg["hma_period"],
        ma200_period=cfg["ma200_period"],
        orb_session_start=cfg["orb_session_start"],
        orb_minutes=cfg["orb_minutes"],
    )
    signal = signal_engine.latest_signal(frame, ticker="SPY", vix=vix)
    new_signal = signal_state.record_signal(signal)

    if new_signal:
        blocked = position_sizing.daily_loss_limit_hit(
            trading_journal.realized_pnl_today(), cfg["account_size"], cfg["daily_loss_limit_pct"]
        )
        proposal = trade_proposals.sync_from_signal(new_signal, blocked_by_daily_limit=blocked)
        if proposal:
            log.info("new %s signal on SPY @ %.2f -> proposal %s (blocked=%s)",
                      new_signal["side"], new_signal["spy_price"], proposal["id"], blocked)

    current_side = signal_state.current_side("SPY")
    exit_proposals_raised = []
    if current_side:
        for entry in trade_proposals.list_proposals(status="placed", kind="entry"):
            if entry["side"] != current_side:
                exit_proposal = trade_proposals.create_exit_proposal(entry["id"], "signal_flip")
                if exit_proposal:
                    log.info("signal flip -> exit proposal %s for entry %s", exit_proposal["id"], entry["id"])
                    exit_proposals_raised.append(exit_proposal["id"])

    return {
        "bar_time": last_bar_ts.isoformat(),
        "new_signal": new_signal,
        "exit_proposals_raised": exit_proposals_raised,
    }


def run_forever() -> None:
    # Imported here, not at module scope: the webapp imports this module at
    # boot to reach job_scan_spy() for its "Run scan now" button, and shouldn't
    # fail to start over a scheduling library that only run_forever() calls.
    import schedule

    # A private Scheduler instance, NOT the module-level `schedule.every(...)`
    # default. stockanalysis.scheduling.scheduler registers its scan jobs on
    # the default scheduler and calls schedule.clear() whenever the schedule
    # config is reloaded — sharing it would silently delete this job, and the
    # symptom (signals quietly stop firing sometime after a config save) is
    # exactly the kind of thing you'd never notice until you needed a signal.
    scheduler = schedule.Scheduler()
    scheduler.every(30).seconds.do(job_scan_spy)
    log.info("SPY signal daemon started — polling every 30s, acting only during "
             "9:30-16:00 ET on weekdays")
    while True:
        scheduler.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_forever()
