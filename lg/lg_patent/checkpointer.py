"""RisingWave-compatible LangGraph checkpoint saver for lg-patent.

RisingWave PG :4566 does NOT support ON CONFLICT DO UPDATE.
Strategy: PK-implicit overwrite for checkpoints/blobs; delete-then-insert
for checkpoint_writes.
Tables created by migration 20260508220000_lg_checkpoints.ts.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

_log = logging.getLogger(__name__)

CHECKPOINTER_URL = os.environ.get("LG_CHECKPOINTER_URL") or os.environ.get("RW_URL", "")

_RW_INSERT_CHECKPOINT = """
    INSERT INTO lg_checkpoints
        (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
         type, checkpoint, metadata, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
"""

_RW_INSERT_BLOB = """
    INSERT INTO lg_checkpoint_blobs
        (thread_id, checkpoint_ns, channel, version, type, blob, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, NOW())
"""

_RW_DELETE_WRITES = """
    DELETE FROM lg_checkpoint_writes
    WHERE thread_id = %s AND checkpoint_ns = %s
      AND checkpoint_id = %s AND task_id = %s AND idx = %s
"""

_RW_INSERT_WRITES = """
    INSERT INTO lg_checkpoint_writes
        (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
         channel, type, blob, task_path, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
"""


@asynccontextmanager
async def build_checkpointer() -> AsyncIterator[Any]:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.checkpoint.base import copy_checkpoint

    if not CHECKPOINTER_URL:
        raise RuntimeError("LG_CHECKPOINTER_URL or RW_URL must be set")

    class _RwAsyncPostgresSaver(AsyncPostgresSaver):
        async def setup(self) -> None:
            return

        async def aput(self, config: dict, checkpoint: Any, metadata: Any, new_versions: dict) -> dict:
            cfg = config["configurable"]
            thread_id = cfg["thread_id"]
            checkpoint_ns = cfg.get("checkpoint_ns", "")
            checkpoint_id = checkpoint["id"]
            parent_id = cfg.get("checkpoint_id")
            ckpt_blob = self.serde.dumps(copy_checkpoint(checkpoint))
            meta_blob = self.serde.dumps(metadata)

            async with self._cursor() as cur:  # type: ignore[attr-defined]
                for channel, version in new_versions.items():
                    val = checkpoint["channel_values"].get(channel)
                    val_type, val_blob = (
                        ("empty", b"") if val is None else self.serde.dumps_typed(val)
                    )
                    await cur.execute(
                        _RW_INSERT_BLOB,
                        (thread_id, checkpoint_ns, channel, str(version), val_type, val_blob),
                    )
                await cur.execute(
                    _RW_INSERT_CHECKPOINT,
                    (thread_id, checkpoint_ns, checkpoint_id, parent_id, "msgpack", ckpt_blob, meta_blob),
                )

            return {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            }

        async def aput_writes(self, config: dict, writes: list, task_id: str, task_path: str = "") -> None:
            cfg = config["configurable"]
            thread_id = cfg["thread_id"]
            checkpoint_ns = cfg.get("checkpoint_ns", "")
            checkpoint_id = cfg["checkpoint_id"]

            async with self._cursor() as cur:  # type: ignore[attr-defined]
                for idx, (channel, value) in enumerate(writes):
                    val_type, val_blob = self.serde.dumps_typed(value)
                    await cur.execute(
                        _RW_DELETE_WRITES,
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, idx),
                    )
                    await cur.execute(
                        _RW_INSERT_WRITES,
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
                         channel, val_type, val_blob, task_path),
                    )

    async with _RwAsyncPostgresSaver.from_conn_string(CHECKPOINTER_URL) as cp:
        yield cp
