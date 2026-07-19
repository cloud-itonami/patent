# lg-patent — clj port (langgraph-python → langgraph-clj)

clj/cljc twin of the deployed Python LangGraph server (`../lg_patent/**.py`),
per **ADR-2606280030** (WAVE 2). The Python remains the deployed runtime
(`langgraph.json` / `Dockerfile` / Helm); this twin is **additive and COEXISTS**
until a human cuts the runtime over. Nothing here removes or edits the Python.

## Layout

| clj ns | ports python |
|---|---|
| `lg-patent.graphs.health` | `lg_patent/graphs/health.py` (START → health → END) |
| `lg-patent.graphs.blob-convert` | `lg_patent/graphs/blob_convert.py` (every-5-min PDF→webp+OCR) |
| `lg-patent.graphs.ingest-uspto-weekly` | `lg_patent/graphs/ingest_uspto_weekly.py` (weekly USPTO+EPO) |
| `lg-patent.server` | `lg_patent/server.py` (FastAPI → org.httpkit.server) |
| `lg-patent.cron` | `lg_patent/cron.py` (APScheduler spec loader) |
| `lg-patent.audit` | OCEL `generic.audit.emit` shim (sibling-twin pattern) |

## Run

```bash
bb test              # clojure.test suite (run_tests.clj; NOT .sh — repo rule)
bb server 8000       # boot the httpkit server (port parity w/ uvicorn :8000)
```

## Faithfulness + deviations (per ADR-2606280030)

- **Topology preserved**: graph registry (`GRAPHS`), NSID map (`NSID-MAP`), cron
  graphs (blob_convert `*/5 * * * *`, ingest_uspto_weekly `0 2 * * 0`), and the
  HTTP surface (`/runs` `{ok result thread_id}`, `/runs/stream` SSE, `/xrpc/{nsid}`
  `{ok result}`, `/threads/{tid}/state`, `/graphs`, `/ok` `/health`) all match.
- **httpx → `babashka.http-client`; JSON → `cheshire`; FastAPI/uvicorn →
  `org.httpkit.server`** (all bb built-ins).
- **RisingWave/psycopg → injectable store seam.** The substrate boundary forbids
  RisingWave; the `_RwAsyncPostgresSaver` checkpointer (`checkpointer.py`) is NOT
  ported — graphs compile without a checkpointer, so `/threads/{tid}/state`
  returns an empty snapshot until a kotoba-Datom-log checkpointer is wired
  (ADR-2605312345).
- **kotodama graphs not in this checkout.** `blob_convert` / `ingest_uspto_weekly`
  re-exported `kotodama.langgraph_graphs.patent_*` in the python; that module is
  not vendored here, so the ports reconstruct the pipeline TOPOLOGY + contract
  from `langgraph.json` + the actor CLAUDE.md, with the native (PDF→webp/OCR) and
  network (USPTO PatentsView / EPO OPS) boundaries left INJECTABLE (`*list-pending*`,
  `*convert-blob*`, `*write-record*`, `*http-get*`, `*write-records*`) — the actor
  swap pattern, defaulting to a "store not configured" / network-disabled no-op so
  the pipelines verify offline under bb.
- **No `RetryPolicy`** — langgraph-clj `add-node` has no per-node retry equivalent;
  the python per-node retries are dropped (nodes are best-effort + fail-soft).
- **LLM (if later wired) → Murakumo loopback** (127.0.0.1:4000, no-server-key /
  read-only, ADR-2605215000) — same as the wave-1 twins.
