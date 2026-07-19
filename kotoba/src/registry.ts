/**
 * patent kotoba — patent + party + classification + citation registries +
 * coverage. AT PDS records (no RW). Parties / classifications / citations
 * FK-reference an existing patent. Public patent-registry data only.
 */

import type { Etzhayyim } from "@etzhayyim/sdk";
import {
  CITATION_COLLECTION,
  CLASS_COLLECTION,
  KINDS,
  OFFICES,
  PARTY_COLLECTION,
  PATENT_COLLECTION,
  ROLES,
  SCHEMES,
  citationDidFor,
  citationRkey,
  classDidFor,
  classRkey,
  isJurisdiction,
  isLei,
  partyDidFor,
  partyRkey,
  patentDidFor,
  patentRkey,
  type AddCitationInput,
  type AddCitationOutput,
  type AddPartyInput,
  type AddPartyOutput,
  type CitationRecord,
  type CitationView,
  type ClassificationRecord,
  type ClassificationView,
  type ClassifyInput,
  type ClassifyOutput,
  type CoverageInput,
  type CoverageOutput,
  type GetPatentInput,
  type GetPatentOutput,
  type IngestPatentInput,
  type IngestPatentOutput,
  type ListCitationsInput,
  type ListCitationsOutput,
  type ListClassificationsInput,
  type ListClassificationsOutput,
  type ListPartiesInput,
  type ListPartiesOutput,
  type ListPatentsInput,
  type ListPatentsOutput,
  type PartyRecord,
  type PartyView,
  type PatentRecord,
  type PatentView,
} from "./types.js";

const PAGE_LIMIT = 100;
const DEFAULT_MAX_SCAN = 10_000;

async function exists(e: Etzhayyim, collection: string, rkey: string): Promise<boolean> {
  const resp = await e.read({ collection, rkey }).catch(() => ({ records: [] }));
  return Boolean(resp.records[0]?.value);
}

async function scanAll<T>(e: Etzhayyim, collection: string, maxScan: number, onRow: (v: T) => void): Promise<number> {
  let cursor: string | undefined;
  let scanned = 0;
  while (scanned < maxScan) {
    const page = await e.read<T>({ collection, cursor, limit: PAGE_LIMIT });
    for (const r of page.records) {
      if (scanned >= maxScan) break;
      onRow(r.value);
      scanned += 1;
    }
    if (scanned >= maxScan || !page.cursor || page.records.length < PAGE_LIMIT) break;
    cursor = page.cursor;
  }
  return scanned;
}

// ─── Patent ─────────────────────────────────────────────────────────

export async function ingestPatent(e: Etzhayyim, input: IngestPatentInput): Promise<IngestPatentOutput> {
  if (!input.patentId || !input.appNumber || !input.title) return { status: "rejected", error: "missingRequiredFields" };
  const jurisdiction = input.jurisdiction?.toUpperCase();
  if (!isJurisdiction(jurisdiction ?? "")) return { status: "rejected", error: "invalidJurisdiction" };
  if (!KINDS.has(input.kind)) return { status: "rejected", error: "invalidKind" };
  if (!OFFICES.has(input.sourceOffice)) return { status: "rejected", error: "invalidSourceOffice" };
  const rkey = patentRkey(input.patentId);
  const existing = await e.read<PatentRecord>({ collection: PATENT_COLLECTION, rkey }).catch(() => ({ records: [] }));
  if (existing.records[0]?.value) {
    return { status: "alreadyExists", patentUri: existing.records[0].uri, did: existing.records[0].value.did, patentId: input.patentId };
  }
  const did = patentDidFor(input.patentId);
  const record: PatentRecord = {
    did,
    patentId: input.patentId,
    jurisdiction: jurisdiction!,
    appNumber: input.appNumber,
    publicationNumber: input.publicationNumber?.toUpperCase(),
    title: input.title,
    kind: input.kind,
    sourceOffice: input.sourceOffice,
    filedDate: input.filedDate,
    publishedDate: input.publishedDate,
    grantedDate: input.grantedDate,
    createdAt: new Date().toISOString(),
  };
  const receipt = await e.write({ collection: PATENT_COLLECTION, record: record as unknown as Record<string, unknown>, rkey });
  return { status: "ingested", patentUri: receipt.uri, did, patentId: input.patentId };
}

export async function getPatent(e: Etzhayyim, input: GetPatentInput): Promise<GetPatentOutput> {
  if (!input.patentId) return { error: "invalidPatentId" };
  const resp = await e.read<PatentRecord>({ collection: PATENT_COLLECTION, rkey: patentRkey(input.patentId) }).catch(() => ({ records: [] }));
  const r = resp.records[0];
  if (!r) return { error: "notFound" };
  return { patent: { ...r.value, patentUri: r.uri } };
}

export async function listPatents(e: Etzhayyim, input: ListPatentsInput = {}): Promise<ListPatentsOutput> {
  const limit = Math.min(input.limit ?? 50, 200);
  const resp = await e.read<PatentRecord>({ collection: PATENT_COLLECTION, cursor: input.cursor, limit });
  const q = input.q?.toLowerCase();
  const jurisdiction = input.jurisdiction?.toUpperCase();
  const items: PatentView[] = resp.records
    .filter((r) => {
      const v = r.value;
      if (jurisdiction && v.jurisdiction !== jurisdiction) return false;
      if (input.sourceOffice && v.sourceOffice !== input.sourceOffice) return false;
      if (input.kind && v.kind !== input.kind) return false;
      if (q && !v.title.toLowerCase().includes(q)) return false;
      return true;
    })
    .map((r) => ({ ...r.value, patentUri: r.uri }));
  return { items, cursor: resp.cursor, total: items.length };
}

// ─── Party ──────────────────────────────────────────────────────────

export async function addParty(e: Etzhayyim, input: AddPartyInput): Promise<AddPartyOutput> {
  if (!input.partyId || !input.patentId || !input.name) return { status: "rejected", error: "missingRequiredFields" };
  if (!ROLES.has(input.role)) return { status: "rejected", error: "invalidRole" };
  if (input.lei && !isLei(input.lei.toUpperCase())) return { status: "rejected", error: "invalidLei" };
  if (input.naturalPersonDid && !input.naturalPersonDid.startsWith("did:")) return { status: "rejected", error: "invalidNaturalPersonDid" };
  if (!(await exists(e, PATENT_COLLECTION, patentRkey(input.patentId)))) {
    return { status: "patentNotFound", error: `patentNotFound:${input.patentId}` };
  }
  const rkey = partyRkey(input.partyId);
  const existing = await e.read<PartyRecord>({ collection: PARTY_COLLECTION, rkey }).catch(() => ({ records: [] }));
  if (existing.records[0]?.value) {
    return { status: "alreadyExists", partyUri: existing.records[0].uri, did: existing.records[0].value.did, partyId: input.partyId };
  }
  const did = partyDidFor(input.partyId);
  const record: PartyRecord = {
    did,
    partyId: input.partyId,
    patentId: input.patentId,
    role: input.role,
    name: input.name,
    lei: input.lei?.toUpperCase(),
    naturalPersonDid: input.naturalPersonDid,
    createdAt: new Date().toISOString(),
  };
  const receipt = await e.write({ collection: PARTY_COLLECTION, record: record as unknown as Record<string, unknown>, rkey });
  return { status: "added", partyUri: receipt.uri, did, partyId: input.partyId };
}

export async function listParties(e: Etzhayyim, input: ListPartiesInput = {}): Promise<ListPartiesOutput> {
  const limit = Math.min(input.limit ?? 50, 200);
  const resp = await e.read<PartyRecord>({ collection: PARTY_COLLECTION, cursor: input.cursor, limit });
  const lei = input.lei?.toUpperCase();
  const items: PartyView[] = resp.records
    .filter((r) => {
      const v = r.value;
      if (input.patentId && v.patentId !== input.patentId) return false;
      if (input.role && v.role !== input.role) return false;
      if (lei && v.lei !== lei) return false;
      return true;
    })
    .map((r) => ({ ...r.value, partyUri: r.uri }));
  return { items, cursor: resp.cursor, total: items.length };
}

// ─── Classification ─────────────────────────────────────────────────

export async function classify(e: Etzhayyim, input: ClassifyInput): Promise<ClassifyOutput> {
  if (!input.classId || !input.patentId || !input.code) return { status: "rejected", error: "missingRequiredFields" };
  if (!SCHEMES.has(input.scheme)) return { status: "rejected", error: "invalidScheme" };
  if (!(await exists(e, PATENT_COLLECTION, patentRkey(input.patentId)))) {
    return { status: "patentNotFound", error: `patentNotFound:${input.patentId}` };
  }
  const rkey = classRkey(input.classId);
  const existing = await e.read<ClassificationRecord>({ collection: CLASS_COLLECTION, rkey }).catch(() => ({ records: [] }));
  if (existing.records[0]?.value) {
    return { status: "alreadyExists", classUri: existing.records[0].uri, did: existing.records[0].value.did, classId: input.classId };
  }
  const did = classDidFor(input.classId);
  const record: ClassificationRecord = {
    did,
    classId: input.classId,
    patentId: input.patentId,
    scheme: input.scheme,
    code: input.code.toUpperCase(),
    createdAt: new Date().toISOString(),
  };
  const receipt = await e.write({ collection: CLASS_COLLECTION, record: record as unknown as Record<string, unknown>, rkey });
  return { status: "classified", classUri: receipt.uri, did, classId: input.classId };
}

export async function listClassifications(e: Etzhayyim, input: ListClassificationsInput = {}): Promise<ListClassificationsOutput> {
  const limit = Math.min(input.limit ?? 50, 200);
  const resp = await e.read<ClassificationRecord>({ collection: CLASS_COLLECTION, cursor: input.cursor, limit });
  const code = input.code?.toUpperCase();
  const items: ClassificationView[] = resp.records
    .filter((r) => {
      const v = r.value;
      if (input.patentId && v.patentId !== input.patentId) return false;
      if (input.scheme && v.scheme !== input.scheme) return false;
      if (code && v.code !== code) return false;
      return true;
    })
    .map((r) => ({ ...r.value, classUri: r.uri }));
  return { items, cursor: resp.cursor, total: items.length };
}

// ─── Citation ───────────────────────────────────────────────────────

export async function addCitation(e: Etzhayyim, input: AddCitationInput): Promise<AddCitationOutput> {
  if (!input.citationId || !input.citingPatentId || !input.citedRef) return { status: "rejected", error: "missingRequiredFields" };
  if (!(await exists(e, PATENT_COLLECTION, patentRkey(input.citingPatentId)))) {
    return { status: "patentNotFound", error: `patentNotFound:${input.citingPatentId}` };
  }
  const rkey = citationRkey(input.citationId);
  const existing = await e.read<CitationRecord>({ collection: CITATION_COLLECTION, rkey }).catch(() => ({ records: [] }));
  if (existing.records[0]?.value) {
    return { status: "alreadyExists", citationUri: existing.records[0].uri, did: existing.records[0].value.did, citationId: input.citationId };
  }
  const did = citationDidFor(input.citationId);
  const record: CitationRecord = {
    did,
    citationId: input.citationId,
    citingPatentId: input.citingPatentId,
    citedRef: input.citedRef.toUpperCase(),
    createdAt: new Date().toISOString(),
  };
  const receipt = await e.write({ collection: CITATION_COLLECTION, record: record as unknown as Record<string, unknown>, rkey });
  return { status: "added", citationUri: receipt.uri, did, citationId: input.citationId };
}

export async function listCitations(e: Etzhayyim, input: ListCitationsInput = {}): Promise<ListCitationsOutput> {
  const limit = Math.min(input.limit ?? 50, 200);
  const resp = await e.read<CitationRecord>({ collection: CITATION_COLLECTION, cursor: input.cursor, limit });
  const citedRef = input.citedRef?.toUpperCase();
  const items: CitationView[] = resp.records
    .filter((r) => {
      const v = r.value;
      if (input.citingPatentId && v.citingPatentId !== input.citingPatentId) return false;
      if (citedRef && v.citedRef !== citedRef) return false;
      return true;
    })
    .map((r) => ({ ...r.value, citationUri: r.uri }));
  return { items, cursor: resp.cursor, total: items.length };
}

// ─── Coverage ───────────────────────────────────────────────────────

export async function coverage(e: Etzhayyim, input: CoverageInput = {}): Promise<CoverageOutput> {
  const maxScan = Math.min(input.maxScan ?? DEFAULT_MAX_SCAN, DEFAULT_MAX_SCAN);
  const patentsByOffice: Record<string, number> = {};
  const patentCount = await scanAll<PatentRecord>(e, PATENT_COLLECTION, maxScan, (v) => {
    patentsByOffice[v.sourceOffice] = (patentsByOffice[v.sourceOffice] ?? 0) + 1;
  });
  const partiesByRole: Record<string, number> = {};
  const partyCount = await scanAll<PartyRecord>(e, PARTY_COLLECTION, maxScan, (v) => {
    partiesByRole[v.role] = (partiesByRole[v.role] ?? 0) + 1;
  });
  const classificationCount = await scanAll<ClassificationRecord>(e, CLASS_COLLECTION, maxScan, () => {});
  const citationCount = await scanAll<CitationRecord>(e, CITATION_COLLECTION, maxScan, () => {});
  return {
    patentCount,
    partyCount,
    classificationCount,
    citationCount,
    patentsByOffice,
    partiesByRole,
    truncated: patentCount >= maxScan || partyCount >= maxScan || classificationCount >= maxScan || citationCount >= maxScan,
  };
}
