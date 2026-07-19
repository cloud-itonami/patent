/**
 * patent kotoba — barrel.
 *
 * Per ADR-2606011400. Global patent-registry open-data on the etzhayyim
 * substrate (AT PDS records; no RW).
 *
 *   patent         : ingestPatent / getPatent / listPatents (q = app-layer search)
 *   party          : addParty (applicant/inventor, FK→patent, LEI/natural-person link) / listParties
 *   classification : classify (IPC/CPC, FK→patent) / listClassifications
 *   citation       : addCitation (FK→patent) / listCitations
 *   coverage
 *
 * Public patent-office data; Tier-3 PII delegated to natural-person.
 */

export * from "./types.js";
export {
  ingestPatent,
  getPatent,
  listPatents,
  addParty,
  listParties,
  classify,
  listClassifications,
  addCitation,
  listCitations,
  coverage,
} from "./registry.js";
