# etzhayyim-project-patent — Patent Registry

> **T1 Logical Actor**: Manifest-driven (`20-actors/patent/actor-manifest.jsonld`). No Worker deploy — PDS Shared Executor runs 7 pipelines (get/list/citations/family/coverage/cron + site.page subscribeRepos) against `vertex_patent` + `edge_patent_cites` + `edge_family_member` + `edge_classified_as` (migration 0037).

`patent.etzhayyim.com` (nanoid: `p4t3nt01`) — 特許情報 coverage actor。JPO (J-PlatPat) / USPTO (PatentsView) / EPO (OPS) / WIPO (PATENTSCOPE) を 2 次ソースとして統合。

## Role

patent = **知的財産 (特許・実用新案) perspective**。出願・公開・登録・引用ネットワークを NSID `com.etzhayyim.apps.patent.*` に正規化し、申請人 (applicant) / 発明者 (inventor) を他 actor にリンクする。

## Architecture

**1 Worker + N path-based DID per jurisdiction** (ISIN と同形)。

| Level | DID | 用途 |
|---|---|---|
| Primary | `did:web:patent.etzhayyim.com` | Coordinator, heartbeat, social evolution |
| Jurisdiction | `did:web:patent.etzhayyim.com:jp` / `:us` / `:ep` / `:wo` | 国/地域別 coverage 投稿 |

## Lexicon (`com.etzhayyim.apps.patent.*`)

| Collection | 用途 |
|---|---|
| `patent` | 出願/公開/登録 record (primary: `{jurisdiction}-{appNumber}`) |
| `applicant` | 出願人 (LEI/CIK 相互リンク可) |
| `inventor` | 発明者 (naturalPerson cohort) |
| `ipcClass` | IPC / CPC 分類 |
| `citation` | backward/forward 引用 edge |
| `familyMember` | INPADOC patent family |
| `coverageReport` | jurisdiction 毎 coverage |

## Cross-Links

```
(:Patent {appNumber})
  -[:FILED_BY]->(:Applicant)
  -[:INVENTED_BY]->(:Inventor)
  -[:CLASSIFIED_AS {source:"ipc"|"cpc"}]->(:IpcClass)
  -[:CITES]->(:Patent)
  -[:FAMILY_OF]->(:Patent)
  -[:OWNED_BY]->(:LegalEntity {lei})
```

| 軸 | Source Project |
|---|---|
| LEI / 法人 | `etzhayyim-project-legal-entity` |
| ISIC / 産業分類 | `etzhayyim-project-open-isic` |
| natural person | `etzhayyim-project-natural-person` |
| 金融証券 | `etzhayyim-project-isin` |

## Data Sources (Follow-based)

| Source | 1 次 Worker | 取得方法 |
|---|---|---|
| JPO J-PlatPat | `webpage` | Follow → subscribeRepos → HTML/XBRL parse |
| USPTO PatentsView | `webpage` | Follow → subscribeRepos → JSON |
| EPO OPS / INPADOC | `webpage` | Follow → subscribeRepos → XML |
| WIPO PATENTSCOPE | `webpage` | Follow → subscribeRepos |

自前 HTTP pull 禁止 (Follow-based input rule)。

## Component

| Component | nanoid |
|---|---|
| `etzhayyim-wasm-patent-p4t3nt01` | `p4t3nt01` |
