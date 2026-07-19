import { describe, it, expect, beforeEach } from "vitest";
import { MockEtzhayyim } from "@etzhayyim/sdk-mock";
import {
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
} from "../src/index.js";

const LEI = "353800ABCDEFGHIJ1234";

describe("patent kotoba", () => {
  let e: any;
  beforeEach(() => {
    e = new MockEtzhayyim({ did: "did:web:patent.etzhayyim.com" });
  });

  describe("patent registry", () => {
    it("ingests (jurisdiction/kind/office validated), reads, lists", async () => {
      expect((await ingestPatent(e, { patentId: "JP-2026-000001", jurisdiction: "jp", appNumber: "2026-000001", title: "冷却装置", kind: "publication", sourceOffice: "JPO" })).status).toBe("ingested");
      expect((await getPatent(e, { patentId: "JP-2026-000001" })).patent?.sourceOffice).toBe("JPO");
      expect((await ingestPatent(e, { patentId: "X", jurisdiction: "JPN", appNumber: "1", title: "x", kind: "grant", sourceOffice: "JPO" })).status).toBe("rejected"); // jurisdiction
      expect((await ingestPatent(e, { patentId: "Y", jurisdiction: "US", appNumber: "1", title: "y", kind: "grant", sourceOffice: "FOO" as any })).status).toBe("rejected"); // office
      await ingestPatent(e, { patentId: "US-17000001", jurisdiction: "US", appNumber: "17/000001", title: "Cooling apparatus", kind: "grant", sourceOffice: "USPTO" });
      expect((await listPatents(e, { sourceOffice: "USPTO" })).total).toBe(1);
      expect((await listPatents(e, { q: "cooling" })).total).toBe(1);
    });
  });

  describe("parties / classifications / citations FK to patent", () => {
    beforeEach(async () => {
      await ingestPatent(e, { patentId: "P-1", jurisdiction: "US", appNumber: "1", title: "X", kind: "grant", sourceOffice: "USPTO" });
    });
    it("adds applicant (LEI) + inventor (natural-person link); rejects bad LEI/missing patent", async () => {
      expect((await addParty(e, { partyId: "PA-1", patentId: "P-1", role: "applicant", name: "Acme Corp", lei: LEI })).status).toBe("added");
      expect((await addParty(e, { partyId: "PA-2", patentId: "P-1", role: "inventor", name: "Jane Doe", naturalPersonDid: "did:web:natural-person.etzhayyim.com:np:x" })).status).toBe("added");
      expect((await addParty(e, { partyId: "PA-X", patentId: "P-1", role: "applicant", name: "x", lei: "SHORT" })).status).toBe("rejected");
      expect((await addParty(e, { partyId: "PA-Y", patentId: "GHOST", role: "inventor", name: "x" })).status).toBe("patentNotFound");
      expect((await listParties(e, { patentId: "P-1", role: "inventor" })).total).toBe(1);
      expect((await listParties(e, { lei: LEI })).total).toBe(1);
    });
    it("classifies (IPC/CPC) + adds citations, rejects bad scheme/missing patent", async () => {
      expect((await classify(e, { classId: "CL-1", patentId: "P-1", scheme: "IPC", code: "f25b9/00" })).status).toBe("classified");
      expect((await classify(e, { classId: "CL-X", patentId: "P-1", scheme: "BOGUS" as any, code: "x" })).status).toBe("rejected");
      expect((await listClassifications(e, { patentId: "P-1", scheme: "IPC" })).total).toBe(1);
      expect((await addCitation(e, { citationId: "C-1", citingPatentId: "P-1", citedRef: "us9000000b1" })).status).toBe("added");
      expect((await addCitation(e, { citationId: "C-X", citingPatentId: "GHOST", citedRef: "x" })).status).toBe("patentNotFound");
      expect((await listCitations(e, { citingPatentId: "P-1" })).total).toBe(1);
    });
    it("coverage rolls up the four collections", async () => {
      await addParty(e, { partyId: "PA-1", patentId: "P-1", role: "applicant", name: "Acme" });
      await classify(e, { classId: "CL-1", patentId: "P-1", scheme: "CPC", code: "F25B" });
      await addCitation(e, { citationId: "C-1", citingPatentId: "P-1", citedRef: "US9000000B1" });
      const cov = await coverage(e);
      expect(cov.patentCount).toBe(1);
      expect(cov.partyCount).toBe(1);
      expect(cov.classificationCount).toBe(1);
      expect(cov.citationCount).toBe(1);
      expect(cov.patentsByOffice?.USPTO).toBe(1);
      expect(cov.partiesByRole?.applicant).toBe(1);
    });
  });
});
