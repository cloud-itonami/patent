# Patent Expiry Worker

Collects estimated-expired pharmaceutical patents, screens eligibility, and
creates generic / biosimilar manufacturing candidates for downstream
`open-seiyaku` review.

Local dry-run:

```sh
python worker/python/patent_expiry_worker.py dry-run
python worker/python/patent_expiry_worker.py blocker '{"patentVertexId":"...","patentNumber":"...","jurisdiction":"USA","blockerType":"regulatory_exclusivity","blockingUntil":"2027-01-01","dryRun":true}'
python worker/python/patent_expiry_worker.py handoff '{"genericCandidateVid":"...","productId":"demo-amoxicillin","dryRun":true}'
python worker/python/patent_expiry_worker.py batch-draft '{"handoffVid":"...","productId":"demo-amoxicillin","manufacturerOrgId":"org-demo","plantOrgId":"plant-demo","dryRun":true}'
python worker/python/patent_expiry_worker.py validate-draft '{"batchPayload":{"manufacturerOrgId":"org-demo","plantOrgId":"plant-demo","productCode":"demo-amoxicillin","batchNumber":"B-001"},"dryRun":true}'
python worker/python/patent_expiry_worker.py queue-start '{"batchDraftVid":"...","validationPassed":true,"batchPayload":{"manufacturerOrgId":"org-demo","plantOrgId":"plant-demo","productCode":"demo-amoxicillin","batchNumber":"B-001"},"dryRun":true}'
python worker/python/patent_expiry_worker.py ack-start '{"startRequestVid":"...","seiyakuInstanceKey":123456789,"status":"started","dryRun":true}'
python worker/python/patent_expiry_worker.py summarize-progress '{"startRequestVid":"...","ackStatus":"started","seiyakuInstanceKey":123456789,"dryRun":true}'
python worker/python/patent_expiry_worker.py pipeline '{"dryRun":true,"limit":10,"rows":[]}'
```

LangServer mode:

```sh
export AGENTGATEWAY_MCP_URL=zeebe-gateway:26500
export RW_URL=postgres://...
python worker/python/patent_expiry_worker.py serve
```

When patent records do not carry an explicit expiry date, backlog collection
uses `filed_at + 20 years` as a review heuristic. The worker only creates
auditable candidates. It does not assert legal freedom to operate when
regulatory exclusivity or secondary patents are still blocking.
