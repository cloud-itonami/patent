"""APScheduler in-process cron for lg-patent.

Reads cron schedules from langgraph.json and fires graph.ainvoke() per schedule.
Disable on N-1 replicas with LG_CRON_ENABLED=false.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_log = logging.getLogger(__name__)

_LANGGRAPH_JSON = Path(os.environ.get("LANGGRAPH_JSON", "/app/langgraph.json"))


def _load_cron_specs() -> list[dict[str, Any]]:
    if not _LANGGRAPH_JSON.exists():
        _log.warning("langgraph.json not found at %s — no crons registered", _LANGGRAPH_JSON)
        return []
    try:
        cfg = json.loads(_LANGGRAPH_JSON.read_text())
    except Exception as exc:
        _log.error("langgraph.json parse failed: %s", exc)
        return []
    crons = cfg.get("crons") or []
    return [c for c in crons if isinstance(c, dict) and c.get("schedule") and c.get("graph")]


def _make_fire(graph: Any, name: str, base_input: dict[str, Any]) -> Any:
    async def _fire() -> None:
        thread_id = f"cron:{name}:{int(time.time())}"
        try:
            await graph.ainvoke(
                dict(base_input),
                config={"configurable": {"thread_id": thread_id}},
            )
            _log.info("cron[%s] fired ok thread_id=%s", name, thread_id)
        except Exception as exc:
            _log.exception("cron[%s] error: %s", name, exc)

    return _fire


def start_cron(graphs: dict[str, Any]) -> AsyncIOScheduler | None:
    if os.environ.get("LG_CRON_ENABLED", "true").lower() not in ("1", "true", "yes"):
        _log.info("cron disabled via LG_CRON_ENABLED env")
        return None

    sched = AsyncIOScheduler(timezone="UTC")
    specs = _load_cron_specs()
    registered = 0

    for spec in specs:
        graph_name = str(spec.get("graph"))
        schedule = str(spec.get("schedule"))
        if graph_name not in graphs:
            _log.warning("cron skipped — unknown graph %r", graph_name)
            continue
        try:
            trigger = CronTrigger.from_crontab(schedule, timezone="UTC")
        except Exception as exc:
            _log.error("cron skipped — invalid schedule %r: %s", schedule, exc)
            continue
        sched.add_job(
            _make_fire(graphs[graph_name], graph_name, dict(spec.get("input") or {})),
            trigger=trigger,
            id=f"{graph_name}-cron",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        registered += 1

    sched.start()
    _log.info("cron scheduler started: %d job(s)", registered)
    return sched


async def stop_cron(scheduler: AsyncIOScheduler | None) -> None:
    if scheduler is None:
        return
    try:
        scheduler.shutdown(wait=False)
    except Exception as exc:
        _log.warning("cron shutdown raised: %s", exc)
