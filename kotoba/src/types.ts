/**
 * patent kotoba — global patent-registry open-data record types.
 *
 * Per ADR-2606011400. patent is the global patent coverage hub integrating
 * JPO/USPTO/EPO/WIPO (public 2次ソース). This package models:
 *   patent → party (applicant/inventor, FK→patent)
 *          → classification (IPC/CPC, FK→patent)
 *          → citation (FK→patent)
 * Registry on AT PDS records (replaces vertex_patent + edge_patent_cites +
 * edge_family_member + edge_classified_as). ADR-2605172000 kotoba.
 *
 * AXIS NOTE (ADR-2605172400): axis-clean public open-data — published patents +
 * applicants/inventors (public on patents, like business-person; Tier-3 PII is
 * delegated to natural-person) + classification + citation graph. No personal-PII
 * custody, no settlement, no fulfillment liability.
 *
 * Identity hierarchy:
 *   did:web:patent.etzhayyim.com                          — controller
 *   did:web:patent.etzhayyim.com:patent:{patentId}        — a patent
 *   did:web:patent.etzhayyim.com:party:{partyId}          — applicant/inventor
 *   did:web:patent.etzhayyim.com:class:{classId}          — a classification
 *   did:web:patent.etzhayyim.com:cite:{citationId}        — a citation
 */

export const PATENT_DID_PREFIX = "did:web:patent.etzhayyim.com:" as const;

export const PATENT_COLLECTION = "com.etzhayyim.apps.patent.patent";
export const PARTY_COLLECTION = "com.etzhayyim.apps.patent.party";
export const CLASS_COLLECTION = "com.etzhayyim.apps.patent.classification";
export const CITATION_COLLECTION = "com.etzhayyim.apps.patent.citation";

// ─── Patent ─────────────────────────────────────────────────────────

export type PatentKind = "application" | "publication" | "grant";
export type SourceOffice = "JPO" | "USPTO" | "EPO" | "WIPO";

export interface PatentRecord {
  did: string;
  /** {jurisdiction}-{appNumber}, e.g. "JP-2026-000001". */
  patentId: string;
  jurisdiction: string;
  appNumber: string;
  publicationNumber?: string;
  title: string;
  kind: PatentKind;
  sourceOffice: SourceOffice;
  filedDate?: string;
  publishedDate?: string;
  grantedDate?: string;
  createdAt: string;
}
export interface PatentView extends PatentRecord {
  patentUri: string;
}
export interface IngestPatentInput {
  patentId: string;
  jurisdiction: string;
  appNumber: string;
  title: string;
  kind: PatentKind;
  sourceOffice: SourceOffice;
  publicationNumber?: string;
  filedDate?: string;
  publishedDate?: string;
  grantedDate?: string;
}
export interface IngestPatentOutput {
  status: "ingested" | "alreadyExists" | "rejected";
  patentUri?: string;
  did?: string;
  patentId?: string;
  error?: string;
}
export interface GetPatentInput {
  patentId: string;
}
export interface GetPatentOutput {
  patent?: PatentView;
  error?: string;
}
export interface ListPatentsInput {
  jurisdiction?: string;
  sourceOffice?: SourceOffice;
  kind?: PatentKind;
  q?: string;
  limit?: number;
  cursor?: string;
}
export interface ListPatentsOutput {
  items: PatentView[];
  cursor?: string;
  total: number;
}

// ─── Party (applicant / inventor) ───────────────────────────────────

export type PartyRole = "applicant" | "inventor";

export interface PartyRecord {
  did: string;
  partyId: string;
  /** FK → patent patentId. */
  patentId: string;
  role: PartyRole;
  name: string;
  /** GLEIF LEI (applicants) — link to legal-entity, optional. */
  lei?: string;
  /** natural-person DID (Tier-3 PII custodian), optional. */
  naturalPersonDid?: string;
  createdAt: string;
}
export interface PartyView extends PartyRecord {
  partyUri: string;
}
export interface AddPartyInput {
  partyId: string;
  patentId: string;
  role: PartyRole;
  name: string;
  lei?: string;
  naturalPersonDid?: string;
}
export interface AddPartyOutput {
  status: "added" | "alreadyExists" | "rejected" | "patentNotFound";
  partyUri?: string;
  did?: string;
  partyId?: string;
  error?: string;
}
export interface ListPartiesInput {
  patentId?: string;
  role?: PartyRole;
  lei?: string;
  limit?: number;
  cursor?: string;
}
export interface ListPartiesOutput {
  items: PartyView[];
  cursor?: string;
  total: number;
}

// ─── Classification ─────────────────────────────────────────────────

export type ClassScheme = "IPC" | "CPC" | "FI" | "F-term";

export interface ClassificationRecord {
  did: string;
  classId: string;
  /** FK → patent patentId. */
  patentId: string;
  scheme: ClassScheme;
  code: string;
  createdAt: string;
}
export interface ClassificationView extends ClassificationRecord {
  classUri: string;
}
export interface ClassifyInput {
  classId: string;
  patentId: string;
  scheme: ClassScheme;
  code: string;
}
export interface ClassifyOutput {
  status: "classified" | "alreadyExists" | "rejected" | "patentNotFound";
  classUri?: string;
  did?: string;
  classId?: string;
  error?: string;
}
export interface ListClassificationsInput {
  patentId?: string;
  scheme?: ClassScheme;
  code?: string;
  limit?: number;
  cursor?: string;
}
export interface ListClassificationsOutput {
  items: ClassificationView[];
  cursor?: string;
  total: number;
}

// ─── Citation ───────────────────────────────────────────────────────

export interface CitationRecord {
  did: string;
  citationId: string;
  /** FK → patent patentId (citing). */
  citingPatentId: string;
  citedRef: string;
  createdAt: string;
}
export interface CitationView extends CitationRecord {
  citationUri: string;
}
export interface AddCitationInput {
  citationId: string;
  citingPatentId: string;
  citedRef: string;
}
export interface AddCitationOutput {
  status: "added" | "alreadyExists" | "rejected" | "patentNotFound";
  citationUri?: string;
  did?: string;
  citationId?: string;
  error?: string;
}
export interface ListCitationsInput {
  citingPatentId?: string;
  citedRef?: string;
  limit?: number;
  cursor?: string;
}
export interface ListCitationsOutput {
  items: CitationView[];
  cursor?: string;
  total: number;
}

// ─── Coverage ───────────────────────────────────────────────────────

export interface CoverageInput {
  maxScan?: number;
}
export interface CoverageOutput {
  patentCount?: number;
  partyCount?: number;
  classificationCount?: number;
  citationCount?: number;
  patentsByOffice?: Record<string, number>;
  partiesByRole?: Record<string, number>;
  truncated?: boolean;
  error?: string;
}

// ─── Validation + helpers ───────────────────────────────────────────

export const KINDS: ReadonlySet<string> = new Set(["application", "publication", "grant"]);
export const OFFICES: ReadonlySet<string> = new Set(["JPO", "USPTO", "EPO", "WIPO"]);
export const ROLES: ReadonlySet<string> = new Set(["applicant", "inventor"]);
export const SCHEMES: ReadonlySet<string> = new Set(["IPC", "CPC", "FI", "F-term"]);

export function isJurisdiction(s: string): boolean {
  return /^[A-Z]{2}$/.test(s);
}
export function isLei(s: string): boolean {
  return /^[A-Z0-9]{20}$/.test(s);
}

export function patentDidFor(id: string): string {
  return `${PATENT_DID_PREFIX}patent:${id.toLowerCase()}`;
}
export function patentRkey(id: string): string {
  return `patent-${id.toLowerCase()}`;
}
export function partyDidFor(id: string): string {
  return `${PATENT_DID_PREFIX}party:${id.toLowerCase()}`;
}
export function partyRkey(id: string): string {
  return `party-${id.toLowerCase()}`;
}
export function classDidFor(id: string): string {
  return `${PATENT_DID_PREFIX}class:${id.toLowerCase()}`;
}
export function classRkey(id: string): string {
  return `class-${id.toLowerCase()}`;
}
export function citationDidFor(id: string): string {
  return `${PATENT_DID_PREFIX}cite:${id.toLowerCase()}`;
}
export function citationRkey(id: string): string {
  return `cite-${id.toLowerCase()}`;
}
