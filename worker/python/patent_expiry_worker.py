#!/usr/bin/env python3
"""Zeebe worker for expired pharmaceutical patent to manufacturing candidates.

The worker does not decide legal freedom-to-operate by itself. It turns
already-expired patent/exclusivity facts into auditable graph candidates that
QA/regulatory workflows can review before any manufacturing action.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json
except Exception:  # noqa: BLE001
    psycopg = None
    dict_row = None
    Json = None


AGENTGATEWAY_MCP_URL = os.environ.get(
    "AGENTGATEWAY_MCP_URL",
    "http://agentgateway-mcp.mitama-udf.svc.cluster.local:8080",
)
RW_URL = os.environ.get("RW_URL") or os.environ.get("DATABASE_URL")
BLOCKER_TYPES = {
    "regulatory_exclusivity",
    "pediatric_exclusivity",
    "orphan_exclusivity",
    "data_exclusivity",
    "secondary_patent",
    "litigation_stay",
    "market_exclusivity",
    "other",
}


async def serve_langserver(tasks: dict[str, Callable[..., Any]]) -> None:
    port = int(os.environ.get("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._json(200, {"ok": True, "runtimeKind": "k8s-langserver", "agentGatewayMcpUrl": AGENTGATEWAY_MCP_URL})
            elif self.path == "/tools":
                self._json(200, {"tools": [{"name": name, "runtime": "langserver"} for name in sorted(tasks)]})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            name = str(payload.get("name") or payload.get("tool") or payload.get("assistant_id") or "")
            arguments = payload.get("arguments") or payload.get("input") or {}
            handler = tasks.get(name)
            if handler is None:
                self._json(404, {"error": f"unknown tool: {name}"})
                return
            result = asyncio.run(handler(**arguments))
            self._json(200, {"ok": True, "name": name, "result": result})

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    await asyncio.to_thread(server.serve_forever)


def today_iso() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_id(prefix: str, *parts: object) -> str:
    if parts:
        digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}_{digest}"
    return f"{prefix}_{secrets.token_urlsafe(16).replace('-', '').replace('_', '')[:20]}"


def parse_date(value: object, field: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return date.fromisoformat(value[:10])


def require(payload: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if not str(payload.get(field, "")).strip()]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


class GraphConnection:
    def __init__(self, url: str):
        if psycopg is None or dict_row is None:
            raise RuntimeError("RW_URL is set but psycopg is not installed")
        self._con = psycopg.connect(url, row_factory=dict_row)

    def __enter__(self) -> "GraphConnection":
        self._con.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._con.__exit__(exc_type, exc, tb)

    def execute(self, query: str, params: Iterable[Any] | dict[str, Any] | None = None) -> Any:
        if isinstance(params, dict):
            query = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", query)
        elif params is not None:
            query = query.replace("?", "%s")
        return self._con.execute(query, params)


def maybe_insert(table: str, row: dict[str, Any], *, ignore_conflict: bool = False) -> bool:
    if not RW_URL:
        return False
    columns = list(row)
    placeholders = ", ".join(f":{column}" for column in columns)
    names = ", ".join(columns)
    where_clause = f" WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE vertex_id = :vertex_id)" if ignore_conflict else ""  # noqa: S608
    with GraphConnection(str(RW_URL)) as con:
        con.execute(
            f"INSERT INTO {table} ({names}) SELECT {placeholders}{where_clause}",  # noqa: S608 - table is constant-controlled by caller
            row,
        )
    return True


def add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def compact_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return str(value)


def is_pharma_patent(row: dict[str, Any]) -> bool:
    code_text = f"{compact_text(row.get('ipc_codes'))} {compact_text(row.get('cpc_codes'))}".upper()
    pharma_codes = ("A61K", "A61P", "C07D", "C07H", "C12N", "C12P", "C12Q")
    if any(code in code_text for code in pharma_codes):
        return True
    text = f"{compact_text(row.get('title'))} {compact_text(row.get('abstract'))}".lower()
    pharma_terms = ("drug", "pharma", "pharmaceutical", "therapeutic", "antibody", "vaccine", "active ingredient")
    return any(term in text for term in pharma_terms)


def first_present(row: dict[str, Any], names: tuple[str, ...]) -> object | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def estimate_expiry_date(row: dict[str, Any]) -> date | None:
    explicit = first_present(row, ("expiryDate", "expiry_date", "expires_at"))
    if explicit:
        return parse_date(explicit, "expiryDate")
    filed = first_present(row, ("filed_at", "filedAt", "filing_date", "filingDate", "priority_date", "priorityDate"))
    if not filed:
        return None
    return add_years(parse_date(filed, "filed_at"), 20)


def active_blocking_until(payload: dict[str, Any], as_of: date) -> date | None:
    values: list[date] = []
    direct = payload.get("blockingExclusivityUntil") or payload.get("blocking_until") or payload.get("blockingUntil")
    if direct:
        values.append(parse_date(direct, "blockingExclusivityUntil"))
    for blocker in payload.get("blockers") or []:
        if not isinstance(blocker, dict):
            continue
        value = blocker.get("blockingUntil") or blocker.get("blocking_until")
        if not value:
            continue
        blocking_until = parse_date(value, "blockingUntil")
        if blocking_until >= as_of:
            values.append(blocking_until)
    if not values:
        return None
    return max(values)


def fetch_patent_rows(limit: int, jurisdiction: str | None) -> list[dict[str, Any]]:
    if not RW_URL:
        return []
    where = "WHERE filed_at IS NOT NULL"
    params: dict[str, Any] = {"limit": limit * 5}
    if jurisdiction:
        where += " AND jurisdiction = :jurisdiction"
        params["jurisdiction"] = jurisdiction
    query = f"""
        SELECT
          vertex_id,
          jurisdiction,
          COALESCE(grant_number, pub_number, app_number, vertex_id) AS patent_number,
          title,
          abstract,
          ipc_codes,
          cpc_codes,
          filed_at,
          granted_at
        FROM vertex_patent
        {where}
        ORDER BY filed_at ASC
        LIMIT :limit
    """
    with GraphConnection(str(RW_URL)) as con:
        return list(con.execute(query, params).fetchall())


def collect_payload(payload: dict[str, Any]) -> dict[str, Any]:
    as_of = parse_date(payload.get("asOf") or today_iso(), "asOf")
    limit = int(payload.get("limit") or 100)
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    jurisdiction = str(payload["jurisdiction"]) if payload.get("jurisdiction") else None
    dry_run = bool(payload.get("dryRun")) or not RW_URL
    rows = list(payload.get("rows") or fetch_patent_rows(limit, jurisdiction))

    candidates: list[dict[str, Any]] = []
    inserted_count = 0
    for row in rows:
        normalized = dict(row)
        if jurisdiction and str(normalized.get("jurisdiction")) != jurisdiction:
            continue
        if not is_pharma_patent(normalized):
            continue
        expiry_date = estimate_expiry_date(normalized)
        if expiry_date is None or expiry_date > as_of:
            continue
        if len(candidates) >= limit:
            break
        patent_vertex_id = str(normalized.get("vertex_id") or normalized.get("patentVertexId") or normalized.get("patent_vertex_id"))
        patent_number = str(normalized.get("patent_number") or normalized.get("patentNumber") or patent_vertex_id)
        candidate = {
            "patentVertexId": patent_vertex_id,
            "patentNumber": patent_number,
            "jurisdiction": str(normalized.get("jurisdiction") or "unknown"),
            "expiryDate": expiry_date.isoformat(),
            "asOf": as_of.isoformat(),
            "status": "eligible_expired",
            "basis": "estimated_filed_date_plus_20_years",
        }
        candidates.append(candidate)
        if not dry_run:
            vertex_id = new_id("open_patent_expiry", patent_number, candidate["jurisdiction"], as_of)
            inserted = maybe_insert(
                "vertex_open_patent_drug_expiry",
                {
                    "vertex_id": vertex_id,
                    "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
                    "patent_vertex_id": patent_vertex_id,
                    "patent_number": patent_number,
                    "jurisdiction": candidate["jurisdiction"],
                    "product_id": normalized.get("product_id") or normalized.get("productId"),
                    "atc_code": normalized.get("atc_code") or normalized.get("atcCode"),
                    "ndc_code": normalized.get("ndc_code") or normalized.get("ndcCode"),
                    "expiry_date": candidate["expiryDate"],
                    "blocking_exclusivity_until": None,
                    "as_of": candidate["asOf"],
                    "eligible": True,
                    "status": "eligible_expired",
                    "created_at": now_iso(),
                    "sensitivity_ord": 2,
                    "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
                    "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
                    "actor_id": "sys.worker.open-patent-expiry",
                },
                ignore_conflict=True,
            )
            inserted_count += 1 if inserted else 0

    run_vertex_id = new_id("open_patent_expiry_backlog", as_of, jurisdiction or "all")
    if not dry_run:
        maybe_insert(
            "vertex_open_patent_expiry_backlog_run",
            {
                "vertex_id": run_vertex_id,
                "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
                "as_of": as_of.isoformat(),
                "jurisdiction": jurisdiction,
                "limit_count": limit,
                "scanned_count": len(rows),
                "candidate_count": len(candidates),
                "inserted_count": inserted_count,
                "status": "completed",
                "created_at": now_iso(),
                "sensitivity_ord": 2,
                "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
                "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
                "actor_id": "sys.worker.open-patent-expiry",
            },
            ignore_conflict=True,
        )
    return {
        "ok": True,
        "runVertexId": run_vertex_id,
        "asOf": as_of.isoformat(),
        "scannedCount": len(rows),
        "candidateCount": len(candidates),
        "insertedCount": inserted_count,
        "candidates": candidates[:limit],
    }


async def collect_expired_drug_patent_backlog(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return collect_payload(dict(payload or kwargs))


def pipeline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    collect = collect_payload(payload)
    candidate_kind = str(payload.get("candidateKind") or "generic")
    if candidate_kind not in {"generic", "biosimilar", "api_source"}:
        raise ValueError("candidateKind must be generic, biosimilar, or api_source")

    screens: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    start_requests: list[dict[str, Any]] = []
    skipped_plan_count = 0
    skipped_draft_count = 0
    skipped_queue_count = 0
    auto_handoff = payload.get("autoHandoffToSeiyaku", True) is not False
    auto_batch_draft = payload.get("autoPrepareSeiyakuBatchDraft", True) is not False
    auto_validate_draft = payload.get("autoValidateSeiyakuBatchDraft", True) is not False
    auto_queue_start = payload.get("autoQueueSeiyakuBatchStart", True) is not False
    for candidate in collect["candidates"]:
        source = dict(candidate)
        matching_row = next(
            (
                dict(row)
                for row in payload.get("rows", [])
                if str(row.get("vertex_id") or row.get("patentVertexId") or row.get("patent_vertex_id")) == source["patentVertexId"]
            ),
            {},
        )
        product_id = matching_row.get("product_id") or matching_row.get("productId") or payload.get("productId")
        screen = screen_payload(
            {
                "patentVertexId": source["patentVertexId"],
                "patentNumber": source["patentNumber"],
                "jurisdiction": source["jurisdiction"],
                "productId": product_id,
                "atcCode": matching_row.get("atc_code") or matching_row.get("atcCode") or payload.get("atcCode"),
                "ndcCode": matching_row.get("ndc_code") or matching_row.get("ndcCode") or payload.get("ndcCode"),
                "expiryDate": source["expiryDate"],
                "blockingExclusivityUntil": matching_row.get("blocking_exclusivity_until")
                or matching_row.get("blockingExclusivityUntil")
                or payload.get("blockingExclusivityUntil"),
                "blockers": matching_row.get("blockers") or payload.get("blockers"),
                "asOf": collect["asOf"],
                "callerDid": payload.get("callerDid"),
                "dryRun": payload.get("dryRun"),
            }
        )
        screens.append(screen)
        if not screen["eligible"] or not product_id:
            skipped_plan_count += 1
            continue
        plan = plan_payload(
            {
                "expiryScreenVid": screen["vertexId"],
                "productId": product_id,
                "candidateKind": candidate_kind,
                "manufacturerOrgId": payload.get("manufacturerOrgId"),
                "plantOrgId": payload.get("plantOrgId"),
                "dosageForm": matching_row.get("dosage_form") or matching_row.get("dosageForm") or payload.get("dosageForm"),
                "targetMarket": payload.get("targetMarket") or source["jurisdiction"],
                "callerDid": payload.get("callerDid"),
                "dryRun": payload.get("dryRun"),
            }
        )
        plans.append(plan)
        if auto_handoff:
            handoff = handoff_payload(
                {
                    "genericCandidateVid": plan["vertexId"],
                    "productId": product_id,
                    "seiyakuProcessId": plan["seiyakuProcessId"],
                    "targetMarket": payload.get("targetMarket") or source["jurisdiction"],
                    "manufacturerOrgId": payload.get("manufacturerOrgId"),
                    "plantOrgId": payload.get("plantOrgId"),
                    "dosageForm": matching_row.get("dosage_form") or matching_row.get("dosageForm") or payload.get("dosageForm"),
                    "batchIntent": payload.get("batchIntent") or "expired_patent_generic_candidate",
                    "callerDid": payload.get("callerDid"),
                    "dryRun": payload.get("dryRun"),
                }
            )
            handoffs.append(handoff)
            if auto_batch_draft and payload.get("manufacturerOrgId") and payload.get("plantOrgId"):
                draft = prepare_batch_draft_payload(
                    {
                        "handoffVid": handoff["vertexId"],
                        "productId": product_id,
                        "manufacturerOrgId": payload.get("manufacturerOrgId"),
                        "plantOrgId": payload.get("plantOrgId"),
                        "productCode": payload.get("productCode") or product_id,
                        "batchNumber": payload.get("batchNumber"),
                        "dosageForm": matching_row.get("dosage_form") or matching_row.get("dosageForm") or payload.get("dosageForm"),
                        "targetMarket": payload.get("targetMarket") or source["jurisdiction"],
                        "callerDid": payload.get("callerDid"),
                        "dryRun": payload.get("dryRun"),
                    }
                )
                drafts.append(draft)
                if auto_validate_draft:
                    validation = validate_batch_draft_payload(
                        {
                            "batchDraftVid": draft["vertexId"],
                            "batchPayload": draft["batchPayload"],
                            "dryRun": payload.get("dryRun"),
                            "callerDid": payload.get("callerDid"),
                        }
                    )
                    validations.append(validation)
                    if auto_queue_start and validation["passed"]:
                        start_request = queue_seiyaku_batch_start_payload(
                            {
                                "batchDraftVid": draft["vertexId"],
                                "validationVid": validation["vertexId"],
                                "validationPassed": validation["passed"],
                                "batchPayload": draft["batchPayload"],
                                "callerDid": payload.get("callerDid"),
                                "dryRun": payload.get("dryRun"),
                            }
                        )
                        start_requests.append(start_request)
                    elif auto_queue_start:
                        skipped_queue_count += 1
            elif auto_batch_draft:
                skipped_draft_count += 1

    return {
        "ok": True,
        "runVertexId": collect["runVertexId"],
        "asOf": collect["asOf"],
        "collectedCount": collect["candidateCount"],
        "screenedCount": len(screens),
        "plannedCount": len(plans),
        "handoffCount": len(handoffs),
        "draftCount": len(drafts),
        "validationCount": len(validations),
        "startRequestCount": len(start_requests),
        "skippedPlanCount": skipped_plan_count,
        "skippedDraftCount": skipped_draft_count,
        "skippedQueueCount": skipped_queue_count,
        "collect": collect,
        "screens": screens,
        "plans": plans,
        "handoffs": handoffs,
        "drafts": drafts,
        "validations": validations,
        "startRequests": start_requests,
    }


async def run_expired_drug_patent_pipeline(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return pipeline_payload(dict(payload or kwargs))


def record_blocker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["patentVertexId", "patentNumber", "jurisdiction", "blockerType", "blockingUntil"])
    blocker_type = str(payload["blockerType"])
    if blocker_type not in BLOCKER_TYPES:
        raise ValueError(f"blockerType must be one of: {', '.join(sorted(BLOCKER_TYPES))}")
    as_of = parse_date(payload.get("asOf") or today_iso(), "asOf")
    blocking_until = parse_date(payload["blockingUntil"], "blockingUntil")
    active = blocking_until >= as_of
    status = "active_blocker" if active else "expired_blocker"
    vertex_id = str(
        payload.get("vertexId")
        or new_id("open_patent_blocker", payload["patentNumber"], payload["jurisdiction"], blocker_type, blocking_until)
    )
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "patent_vertex_id": payload["patentVertexId"],
        "patent_number": payload["patentNumber"],
        "jurisdiction": payload["jurisdiction"],
        "product_id": payload.get("productId"),
        "blocker_type": blocker_type,
        "blocking_until": blocking_until.isoformat(),
        "source": payload.get("source"),
        "evidence_uri": payload.get("evidenceUri"),
        "as_of": as_of.isoformat(),
        "active": active,
        "status": status,
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_regulatory_blocker", row, ignore_conflict=True)
    return {
        "ok": True,
        "vertexId": vertex_id,
        "active": active,
        "status": status,
        "blockingUntil": blocking_until.isoformat(),
    }


async def record_drug_regulatory_blocker(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return record_blocker_payload(dict(payload or kwargs))


def screen_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["patentVertexId", "patentNumber", "jurisdiction", "expiryDate"])
    as_of = parse_date(payload.get("asOf") or today_iso(), "asOf")
    expiry_date = parse_date(payload["expiryDate"], "expiryDate")
    blocking_until = active_blocking_until(payload, as_of)

    patent_expired = expiry_date <= as_of
    exclusivity_clear = blocking_until is None or blocking_until < as_of
    eligible = patent_expired and exclusivity_clear
    status = "eligible_expired" if eligible else "blocked_by_exclusivity" if patent_expired else "patent_active"
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_expiry", payload["patentNumber"], payload["jurisdiction"], as_of))

    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "patent_vertex_id": payload["patentVertexId"],
        "patent_number": payload["patentNumber"],
        "jurisdiction": payload["jurisdiction"],
        "product_id": payload.get("productId"),
        "atc_code": payload.get("atcCode"),
        "ndc_code": payload.get("ndcCode"),
        "expiry_date": expiry_date.isoformat(),
        "blocking_exclusivity_until": blocking_until.isoformat() if blocking_until else None,
        "as_of": as_of.isoformat(),
        "eligible": eligible,
        "status": status,
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_drug_expiry", row, ignore_conflict=True)
    return {"ok": True, "vertexId": vertex_id, "eligible": eligible, "status": status, "asOf": as_of.isoformat()}


async def screen_expired_drug_patent(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return screen_payload(dict(payload or kwargs))


def plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["expiryScreenVid", "productId", "candidateKind"])
    candidate_kind = str(payload["candidateKind"])
    if candidate_kind not in {"generic", "biosimilar", "api_source"}:
        raise ValueError("candidateKind must be generic, biosimilar, or api_source")

    vertex_id = str(payload.get("vertexId") or new_id("open_patent_generic", payload["expiryScreenVid"], payload["productId"], candidate_kind))
    process_type = str(payload.get("processType") or ("biosimilar_comparability" if candidate_kind == "biosimilar" else "generic_formulation"))
    seiyaku_process_id = "seiyaku_register_batch"
    status = "candidate_ready"
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "expiry_screen_vid": payload["expiryScreenVid"],
        "product_id": payload["productId"],
        "candidate_kind": candidate_kind,
        "manufacturer_org_id": payload.get("manufacturerOrgId"),
        "plant_org_id": payload.get("plantOrgId"),
        "dosage_form": payload.get("dosageForm"),
        "process_type": process_type,
        "target_market": payload.get("targetMarket"),
        "seiyaku_process_id": seiyaku_process_id,
        "status": status,
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_generic_candidate", row, ignore_conflict=True)
    return {"ok": True, "vertexId": vertex_id, "seiyakuProcessId": seiyaku_process_id, "status": status}


async def plan_generic_manufacturing(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return plan_payload(dict(payload or kwargs))


def handoff_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["genericCandidateVid", "productId"])
    seiyaku_process_id = str(payload.get("seiyakuProcessId") or "seiyaku_register_batch")
    if seiyaku_process_id != "seiyaku_register_batch":
        raise ValueError("seiyakuProcessId must be seiyaku_register_batch")
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_seiyaku_handoff", payload["genericCandidateVid"], payload["productId"]))
    status = "handoff_ready"
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "generic_candidate_vid": payload["genericCandidateVid"],
        "product_id": payload["productId"],
        "seiyaku_process_id": seiyaku_process_id,
        "target_market": payload.get("targetMarket"),
        "manufacturer_org_id": payload.get("manufacturerOrgId"),
        "plant_org_id": payload.get("plantOrgId"),
        "dosage_form": payload.get("dosageForm"),
        "batch_intent": payload.get("batchIntent") or "expired_patent_generic_candidate",
        "status": status,
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_seiyaku_handoff", row, ignore_conflict=True)
    return {"ok": True, "vertexId": vertex_id, "seiyakuProcessId": seiyaku_process_id, "status": status}


async def handoff_generic_candidate_to_seiyaku(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return handoff_payload(dict(payload or kwargs))


def prepare_batch_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["handoffVid", "productId", "manufacturerOrgId", "plantOrgId"])
    product_code = str(payload.get("productCode") or payload["productId"])
    batch_number = str(payload.get("batchNumber") or new_id("batch", payload["handoffVid"], product_code))
    seiyaku_process_id = "seiyaku_register_batch"
    batch_payload = {
        "manufacturerOrgId": payload["manufacturerOrgId"],
        "plantOrgId": payload["plantOrgId"],
        "productCode": product_code,
        "batchNumber": batch_number,
        "dosageForm": payload.get("dosageForm"),
        "targetMarket": payload.get("targetMarket"),
        "source": "open-patent.expired-pharma",
        "handoffVid": payload["handoffVid"],
    }
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_seiyaku_batch_draft", payload["handoffVid"], batch_number))
    status = "batch_draft_ready"
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "handoff_vid": payload["handoffVid"],
        "product_id": payload["productId"],
        "manufacturer_org_id": payload["manufacturerOrgId"],
        "plant_org_id": payload["plantOrgId"],
        "product_code": product_code,
        "batch_number": batch_number,
        "dosage_form": payload.get("dosageForm"),
        "target_market": payload.get("targetMarket"),
        "seiyaku_process_id": seiyaku_process_id,
        "batch_payload": Json(batch_payload) if Json is not None else json.dumps(batch_payload, sort_keys=True),
        "status": status,
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_seiyaku_batch_draft", row, ignore_conflict=True)
    return {
        "ok": True,
        "vertexId": vertex_id,
        "seiyakuProcessId": seiyaku_process_id,
        "batchNumber": batch_number,
        "status": status,
        "batchPayload": batch_payload,
    }


async def prepare_seiyaku_batch_draft(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return prepare_batch_draft_payload(dict(payload or kwargs))


def validate_batch_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    batch_payload = dict(payload.get("batchPayload") or {})
    merged = {
        "manufacturerOrgId": payload.get("manufacturerOrgId") or batch_payload.get("manufacturerOrgId"),
        "plantOrgId": payload.get("plantOrgId") or batch_payload.get("plantOrgId"),
        "productCode": payload.get("productCode") or batch_payload.get("productCode"),
        "batchNumber": payload.get("batchNumber") or batch_payload.get("batchNumber"),
        "dosageForm": payload.get("dosageForm") or batch_payload.get("dosageForm"),
        "targetMarket": payload.get("targetMarket") or batch_payload.get("targetMarket"),
    }
    findings: list[str] = []
    for field in ("manufacturerOrgId", "plantOrgId", "productCode", "batchNumber"):
        if not str(merged.get(field) or "").strip():
            findings.append(f"missing_{field}")
    for field in ("dosageForm", "targetMarket"):
        if not str(merged.get(field) or "").strip():
            findings.append(f"recommended_{field}")
    passed = not any(finding.startswith("missing_") for finding in findings)
    status = "validation_passed" if passed else "validation_failed"
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_seiyaku_batch_validation", payload.get("batchDraftVid"), merged.get("batchNumber")))
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "batch_draft_vid": payload.get("batchDraftVid"),
        "passed": passed,
        "status": status,
        "findings": Json(findings) if Json is not None else json.dumps(findings, sort_keys=True),
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_seiyaku_batch_validation", row, ignore_conflict=True)
    return {"ok": True, "vertexId": vertex_id, "passed": passed, "status": status, "findings": findings}


async def validate_seiyaku_batch_draft(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return validate_batch_draft_payload(dict(payload or kwargs))


def queue_seiyaku_batch_start_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["batchDraftVid", "batchPayload"])
    if payload.get("validationPassed") is False:
        raise ValueError("validationPassed must not be false")
    request_payload = dict(payload["batchPayload"])
    for field in ("manufacturerOrgId", "plantOrgId", "productCode", "batchNumber"):
        if not str(request_payload.get(field) or "").strip():
            raise ValueError(f"batchPayload.{field} is required")
    start_nsid = "com.etzhayyim.apps.openSeiyaku.startBatchRecord"
    bpmn_process_id = "seiyaku_register_batch"
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_seiyaku_start", payload["batchDraftVid"], request_payload["batchNumber"]))
    status = "queued"
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "batch_draft_vid": payload["batchDraftVid"],
        "validation_vid": payload.get("validationVid"),
        "start_nsid": start_nsid,
        "bpmn_process_id": bpmn_process_id,
        "request_payload": Json(request_payload) if Json is not None else json.dumps(request_payload, sort_keys=True),
        "status": status,
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_seiyaku_start_request", row, ignore_conflict=True)
    return {
        "ok": True,
        "vertexId": vertex_id,
        "startNsid": start_nsid,
        "bpmnProcessId": bpmn_process_id,
        "status": status,
        "requestPayload": request_payload,
    }


async def queue_seiyaku_batch_start(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return queue_seiyaku_batch_start_payload(dict(payload or kwargs))


def acknowledge_seiyaku_batch_start_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["startRequestVid"])
    status = str(payload.get("status") or "accepted")
    if status not in {"accepted", "rejected", "started"}:
        raise ValueError("status must be accepted, rejected, or started")
    instance_value = payload.get("seiyakuInstanceKey")
    seiyaku_instance_key = int(instance_value) if instance_value not in (None, "") else None
    seiyaku_batch_vertex_id = payload.get("seiyakuBatchVertexId")
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_seiyaku_start_ack", payload["startRequestVid"], status, seiyaku_instance_key or "none"))
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "start_request_vid": payload["startRequestVid"],
        "seiyaku_instance_key": seiyaku_instance_key,
        "seiyaku_batch_vertex_id": seiyaku_batch_vertex_id,
        "status": status,
        "message": payload.get("message"),
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_seiyaku_start_ack", row, ignore_conflict=True)
    return {
        "ok": True,
        "vertexId": vertex_id,
        "status": status,
        "startRequestVid": payload["startRequestVid"],
        "seiyakuInstanceKey": seiyaku_instance_key,
        "seiyakuBatchVertexId": seiyaku_batch_vertex_id,
    }


async def acknowledge_seiyaku_batch_start(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return acknowledge_seiyaku_batch_start_payload(dict(payload or kwargs))


def summarize_seiyaku_start_progress_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, ["startRequestVid"])
    ack_status = payload.get("ackStatus")
    start_status = str(payload.get("startRequestStatus") or "queued")
    if ack_status in {"started", "accepted", "rejected"}:
        progress_status = str(ack_status)
    elif start_status == "queued":
        progress_status = "queued"
    else:
        progress_status = start_status
    if progress_status not in {"queued", "accepted", "started", "rejected"}:
        raise ValueError("progress status must be queued, accepted, started, or rejected")
    instance_value = payload.get("seiyakuInstanceKey")
    seiyaku_instance_key = int(instance_value) if instance_value not in (None, "") else None
    vertex_id = str(payload.get("vertexId") or new_id("open_patent_seiyaku_progress", payload["startRequestVid"], payload.get("ackVid") or "no_ack"))
    row = {
        "vertex_id": vertex_id,
        "owner_did": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "start_request_vid": payload["startRequestVid"],
        "ack_vid": payload.get("ackVid"),
        "progress_status": progress_status,
        "seiyaku_instance_key": seiyaku_instance_key,
        "seiyaku_batch_vertex_id": payload.get("seiyakuBatchVertexId"),
        "message": payload.get("message"),
        "created_at": now_iso(),
        "sensitivity_ord": 2,
        "org_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "user_id": str(payload.get("callerDid") or "did:web:open-patent.etzhayyim.com"),
        "actor_id": "sys.worker.open-patent-expiry",
    }
    if not payload.get("dryRun"):
        maybe_insert("vertex_open_patent_seiyaku_progress", row, ignore_conflict=True)
    return {
        "ok": True,
        "vertexId": vertex_id,
        "progressStatus": progress_status,
        "startRequestVid": payload["startRequestVid"],
        "ackVid": payload.get("ackVid"),
        "seiyakuInstanceKey": seiyaku_instance_key,
        "seiyakuBatchVertexId": payload.get("seiyakuBatchVertexId"),
    }


async def summarize_seiyaku_start_progress(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return summarize_seiyaku_start_progress_payload(dict(payload or kwargs))


async def serve() -> None:
    await serve_langserver({
        "openPatent.expiredDrugPatent.recordBlocker": record_drug_regulatory_blocker,
        "openPatent.expiredDrugPatent.pipeline": run_expired_drug_patent_pipeline,
        "openPatent.expiredDrugPatent.collect": collect_expired_drug_patent_backlog,
        "openPatent.expiredDrugPatent.screen": screen_expired_drug_patent,
        "openPatent.genericManufacturing.plan": plan_generic_manufacturing,
        "openPatent.genericManufacturing.handoffSeiyaku": handoff_generic_candidate_to_seiyaku,
        "openPatent.genericManufacturing.prepareSeiyakuBatchDraft": prepare_seiyaku_batch_draft,
        "openPatent.genericManufacturing.validateSeiyakuBatchDraft": validate_seiyaku_batch_draft,
        "openPatent.genericManufacturing.queueSeiyakuBatchStart": queue_seiyaku_batch_start,
        "openPatent.genericManufacturing.ackSeiyakuBatchStart": acknowledge_seiyaku_batch_start,
        "openPatent.genericManufacturing.summarizeSeiyakuStartProgress": summarize_seiyaku_start_progress,
    })


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["summarize-progress", "ack-start", "queue-start", "validate-draft", "batch-draft", "handoff", "blocker", "pipeline", "collect", "screen", "plan", "serve", "dry-run"])
    parser.add_argument("payload", nargs="?", default="{}")
    args = parser.parse_args(argv)
    if args.command == "serve":
        asyncio.run(serve())
        return 0
    if args.command == "dry-run":
        sample = {
            "patentVertexId": "at://did:web:patent.etzhayyim.com/patent/example",
            "patentNumber": "EX-1999-0001",
            "jurisdiction": "USA",
            "productId": "demo-amoxicillin",
            "atcCode": "J01CA04",
            "expiryDate": "2019-01-01",
            "asOf": today_iso(),
            "dryRun": True,
        }
        screen = screen_payload(sample)
        plan = plan_payload({"expiryScreenVid": screen["vertexId"], "productId": sample["productId"], "candidateKind": "generic"})
        handoff = handoff_payload(
            {
                "genericCandidateVid": plan["vertexId"],
                "productId": sample["productId"],
                "seiyakuProcessId": plan["seiyakuProcessId"],
                "targetMarket": sample["jurisdiction"],
                "manufacturerOrgId": "org-demo-manufacturer",
                "plantOrgId": "plant-demo",
                "dryRun": True,
            }
        )
        draft = prepare_batch_draft_payload(
            {
                "handoffVid": handoff["vertexId"],
                "productId": sample["productId"],
                "manufacturerOrgId": "org-demo-manufacturer",
                "plantOrgId": "plant-demo",
                "targetMarket": sample["jurisdiction"],
                "dryRun": True,
            }
        )
        validation = validate_batch_draft_payload({"batchDraftVid": draft["vertexId"], "batchPayload": draft["batchPayload"], "dryRun": True})
        start_request = queue_seiyaku_batch_start_payload(
            {
                "batchDraftVid": draft["vertexId"],
                "validationVid": validation["vertexId"],
                "validationPassed": validation["passed"],
                "batchPayload": draft["batchPayload"],
                "dryRun": True,
            }
        )
        start_ack = acknowledge_seiyaku_batch_start_payload(
            {
                "startRequestVid": start_request["vertexId"],
                "seiyakuInstanceKey": 123456789,
                "seiyakuBatchVertexId": "at://did:web:open-seiyaku.etzhayyim.com/batch/demo",
                "status": "started",
                "dryRun": True,
            }
        )
        progress = summarize_seiyaku_start_progress_payload(
            {
                "startRequestVid": start_request["vertexId"],
                "ackVid": start_ack["vertexId"],
                "ackStatus": start_ack["status"],
                "seiyakuInstanceKey": start_ack["seiyakuInstanceKey"],
                "seiyakuBatchVertexId": start_ack["seiyakuBatchVertexId"],
                "dryRun": True,
            }
        )
        blocker = record_blocker_payload(
            {
                "patentVertexId": sample["patentVertexId"],
                "patentNumber": sample["patentNumber"],
                "jurisdiction": sample["jurisdiction"],
                "productId": sample["productId"],
                "blockerType": "regulatory_exclusivity",
                "blockingUntil": "2027-01-01",
                "source": "demo",
                "asOf": today_iso(),
                "dryRun": True,
            }
        )
        collect = collect_payload(
            {
                "asOf": today_iso(),
                "limit": 10,
                "dryRun": True,
                "rows": [
                    {
                        "vertex_id": sample["patentVertexId"],
                        "patent_number": sample["patentNumber"],
                        "jurisdiction": sample["jurisdiction"],
                        "title": "Pharmaceutical antibiotic composition",
                        "abstract": "Therapeutic drug formulation",
                        "cpc_codes": ["A61K"],
                        "filed_at": "1999-01-01",
                    }
                ],
            }
        )
        pipeline = pipeline_payload(
            {
                "asOf": today_iso(),
                "limit": 10,
                "candidateKind": "generic",
                "manufacturerOrgId": "org-demo-manufacturer",
                "plantOrgId": "plant-demo",
                "dryRun": True,
                "rows": [
                    {
                        "vertex_id": sample["patentVertexId"],
                        "patent_number": sample["patentNumber"],
                        "jurisdiction": sample["jurisdiction"],
                        "title": "Pharmaceutical antibiotic composition",
                        "abstract": "Therapeutic drug formulation",
                        "cpc_codes": ["A61K"],
                        "filed_at": "1999-01-01",
                        "productId": sample["productId"],
                    }
                ],
            }
        )
        print(
            json.dumps(
                {
                    "blocker": blocker,
                    "collect": collect,
                    "screen": screen,
                    "plan": plan,
                    "handoff": handoff,
                    "draft": draft,
                    "validation": validation,
                    "startRequest": start_request,
                    "startAck": start_ack,
                    "progress": progress,
                    "pipeline": pipeline,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    payload = json.loads(args.payload)
    if args.command == "summarize-progress":
        result = summarize_seiyaku_start_progress_payload(payload)
    elif args.command == "ack-start":
        result = acknowledge_seiyaku_batch_start_payload(payload)
    elif args.command == "queue-start":
        result = queue_seiyaku_batch_start_payload(payload)
    elif args.command == "validate-draft":
        result = validate_batch_draft_payload(payload)
    elif args.command == "batch-draft":
        result = prepare_batch_draft_payload(payload)
    elif args.command == "handoff":
        result = handoff_payload(payload)
    elif args.command == "blocker":
        result = record_blocker_payload(payload)
    elif args.command == "pipeline":
        result = pipeline_payload(payload)
    elif args.command == "collect":
        result = collect_payload(payload)
    elif args.command == "screen":
        result = screen_payload(payload)
    else:
        result = plan_payload(payload)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
