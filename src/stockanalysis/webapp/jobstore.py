"""
jobstore.py
===========
In-process background job registry for the webapp. One job "kind" (scan /
research / news / cleanup) runs at a time each; a job reports its own stage
as it progresses so the UI can show real progress instead of a spinner.

No new dependency, no separate worker process — jobs are daemon threads
inside the same Python process as the HTTP server, which is why job state
lives in a module-level dict rather than a queue/DB: this is a local,
single-user tool, not a multi-worker service (see the app.py docstring for
why that trade-off is intentional here).
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime

MAX_HISTORY = 50

_jobs: dict[str, dict] = {}          # kind -> latest job for that kind
_history: list[dict] = []            # bounded log of all finished jobs
_lock = threading.Lock()


class Progress:
    """Passed into the job function; call .stage(...) to report progress.
    A stage string like "scan:NVDA" lets the UI infer both the pipeline step
    and, combined with done/total, a percentage."""

    def __init__(self, kind: str):
        self._kind = kind

    def stage(self, label: str, done: int | None = None,
              total: int | None = None) -> None:
        with _lock:
            j = _jobs.get(self._kind)
            if j and j["status"] == "running":
                j["stage"] = label
                if done is not None:
                    j["done"] = done
                if total is not None:
                    j["total"] = total


def is_running(kind: str) -> bool:
    with _lock:
        j = _jobs.get(kind)
        return bool(j and j["status"] == "running")


def start(kind: str, label: str, fn) -> str:
    """
    Run fn(progress: Progress) on a daemon thread. fn's return value becomes
    the job's `detail` on success. Refuses to start (returns an error
    string) if a job of the same kind is already running; returns "" on
    successful start.
    """
    if is_running(kind):
        return f"a {kind} job is already running — wait for it to finish"

    progress = Progress(kind)

    def _runner():
        try:
            detail = fn(progress)
            _finish(kind, "done", str(detail or "ok"))
        except Exception as e:
            traceback.print_exc()
            _finish(kind, "failed", str(e)[:300])

    with _lock:
        _jobs[kind] = {
            "kind": kind, "label": label, "status": "running",
            "stage": "starting", "done": 0, "total": 0, "detail": "",
            "started": datetime.now(), "finished": None,
        }
    threading.Thread(target=_runner, daemon=True, name=f"job-{kind}").start()
    return ""


def _finish(kind: str, status: str, detail: str) -> None:
    with _lock:
        j = _jobs.get(kind)
        if not j:
            return
        j.update(status=status, detail=detail, finished=datetime.now())
        _history.insert(0, dict(j))
        del _history[MAX_HISTORY:]


def _serialize(j: dict) -> dict:
    pct = (round(j["done"] / j["total"] * 100) if j.get("total") else None)
    return {
        "kind": j["kind"], "label": j["label"], "status": j["status"],
        "stage": j.get("stage", ""), "done": j.get("done", 0),
        "total": j.get("total", 0), "pct": pct, "detail": j.get("detail", ""),
        "started": j["started"].strftime("%H:%M:%S") if j.get("started") else None,
        "finished": j["finished"].strftime("%H:%M:%S") if j.get("finished") else None,
    }


def current() -> list[dict]:
    """Latest job per kind, running first, then most recently started."""
    with _lock:
        items = list(_jobs.values())
    items.sort(key=lambda j: (j["status"] != "running", -j["started"].timestamp()))
    return [_serialize(j) for j in items]


def history() -> list[dict]:
    with _lock:
        items = list(_history)
    return [_serialize(j) for j in items]


def any_running() -> bool:
    with _lock:
        return any(j["status"] == "running" for j in _jobs.values())


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER STATUS — the always-on cron-style loop (app.py's scheduler-loop
# thread), as opposed to the one-off jobs above. Thread liveness is read
# straight from threading.enumerate() rather than a flag we set ourselves,
# so it reflects reality even if the thread died from an uncaught exception.
# ─────────────────────────────────────────────────────────────────────────────

SCHEDULER_THREAD_NAME = "scheduler-loop"


def scheduler_alive() -> bool:
    return any(t.name == SCHEDULER_THREAD_NAME and t.is_alive()
               for t in threading.enumerate())


def job_name(job) -> str:
    """The registered function's name for a `schedule` Job — shared by the
    status table and the manual "run now" trigger so both identify jobs the
    same way."""
    fn = job.job_func
    return getattr(fn, "func", fn).__name__ if hasattr(fn, "func") else getattr(fn, "__name__", str(fn))


def scheduler_jobs() -> list[dict]:
    """Next-run time for every job registered with the `schedule` library.
    Reads the package's shared default scheduler, so this works regardless
    of which module (scheduler.py) actually called schedule.every()."""
    try:
        import schedule
    except ImportError:
        return []

    jobs = sorted(schedule.jobs, key=lambda j: j.next_run or datetime.max)
    return [{"job": job_name(j),
             "next_run": j.next_run.strftime("%a %H:%M") if j.next_run else None}
            for j in jobs]
