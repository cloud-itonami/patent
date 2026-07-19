"""patent `health` graph — liveness probe."""

from __future__ import annotations

import os
import time
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class _State(TypedDict, total=False):
    ok: bool
    ts: int


def _node_health(state: _State) -> dict[str, Any]:
    return {"ok": True, "ts": int(time.time() * 1000)}


def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("health", _node_health)
    g.add_edge(START, "health")
    g.add_edge("health", END)
    return g


GRAPH = _build().compile(name="health")
