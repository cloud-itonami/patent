# patent — 特許の公開情報を記録・照会する参照実装（etzhayyim substrate）

`patent` は、**特許庁が公開している出願・公開・登録の情報**を AT Protocol PDS の
レコードとして登録し、出願人・発明者・IPC/CPC 分類・引用関係を紐付けて数える
ための参照実装である。主キーは `{jurisdiction}-{appNumber}`。

- 動く手順: [`docs/operator-quickstart.md`](docs/operator-quickstart.md)
- 正準メタデータ: `README.edn`（`:canonical-metadata :edn`）。この README は
  人間向けの入口であって、機械可読な正本ではない。

**これは特許データベースではなく記録層である。** 渡されたものを検証して保存する。
JPO / USPTO / EPO / WIPO に問い合わせに行くことはしないし、ある出願番号が実在するか
どうかも知らない。`sourceOffice` が `USPTO` であることは「USPTO の語彙に含まれる 4 値の
1 つだった」という意味であって、USPTO に照会した結果ではない。

**法的判断もしない。** `worker/python/` は期限切れ医薬品特許の候補を作るが、
自身の docstring が明示するとおり *does not assert legal freedom to operate* —
規制上の独占期間や二次特許が残っている可能性を判定するのは、後段の
QA / 規制レビューであってこの repo ではない。期限が明示されていない場合、
worker は `filed_at + 20 年` を**レビュー候補を出すためだけの目安**として使う。

## `CLAUDE.md` より先にこれを読む — あれは設計文書であって状態報告ではない

`CLAUDE.md` は、この repo に入っているものよりかなり大きな系を記述している。
「T1 Logical Actor」「PDS Shared Executor が 7 本の pipeline を回す」
「`vertex_patent` + `edge_patent_cites` + … (migration 0037)」と書かれているが、
**その executor も migration も、この repo には無い。** 同じく `kotodama.edn` は
11 本の BPMN プロセスを列挙するが、BPMN の実体は `../../etzhayyim-root/00-contracts/`
を指しており、ここには無い。

これらは移行前の monorepo（`etzhayyim/root` の `60-apps/etzhayyim-project-patent`、
`migration.edn` 参照）を前提に書かれた記述で、抽出時にパスが取り残されている。
`lg/langgraph.json` の `dependencies` にある
`../../../40-engine/kotoba/crates/kotoba-kotodama/py` も同種で、**この repo からは
解決しない**。

## この repo に入っている 4 つの木

`git ls-files` は 36 ファイル。**実際に手元で走るのは `worker/python/` と
`lg/clj/` と `kotoba/` の 3 つ**で、`appview/` は宣言だけである。

| 木 | 中身 | 状態 |
|---|---|---|
| `worker/python/` | 期限切れ医薬品特許 → 製造候補の Zeebe worker、1,066 行 | **動く**（stdlib のみ。`psycopg` は任意で、無ければ dry-run 経路に落ちる） |
| `lg/clj/` | Python LangGraph サーバの cljc twin、854 行 + 3 suite | **動く**（`bb test` で 38 tests / 78 assertions） |
| `kotoba/` | TS package `@etzhayyim/patent-kotoba`。src 592 行 + test 69 行、vitest | **動く**（要 SSH 権限と `npm install`。手順は quickstart） |
| `lg/lg_patent/` | 配備されている Python LangGraph サーバ、245 行 | **ここでは動かない**。`langgraph.json` の依存パスが移行前の monorepo を指す |
| `appview/etzhayyim-wasm-patent-p4t3nt01/` | `kotodama.jsonld` **1 ファイルだけ** | 宣言のみ。`staticDir: /wasm/svelte/dist` と HTTP route を宣言するが、svelte も wasm もこの repo に無い |

**ルート直下に `src/` も `test/` も無い。** 探しているものは `kotoba/src/`、
`kotoba/test/`、`lg/clj/lg_patent/`、`worker/python/` にある。

## `kotoba/` が固定している不変条件

vitest の 4 本（`kotoba/test/patent.test.ts`）が押さえているのは、
**語彙の外を拒否すること**と**参照先の実在**である:

| 検査 | 通る | 拒否する |
|---|---|---|
| `jurisdiction` | `US`（ISO 3166-1 alpha-2、2 文字） | `JPN` → `invalidJurisdiction` |
| `sourceOffice` | `JPO` `USPTO` `EPO` `WIPO` | それ以外 → `invalidSourceOffice` |
| `scheme` | `IPC` `CPC` `FI` `F-term` | それ以外 → `invalidScheme` |
| `lei` | 英数 20 桁 | `SHORT` → `invalidLei` |
| party / classification / citation の `patentId` | 実在する patent | 不在 → `patentNotFound` |

`coverage` はこの 4 コレクションを集計して `patentsByOffice` / `partiesByRole` に
畳む。テストは `MockEtzhayyim`（`@etzhayyim/sdk-mock`）を使うので **PDS も
ネットワークも要らない**。

## 他 actor との接続

出願人は LEI で法人に、発明者は natural-person に外部リンクする（Tier-3 PII は
この repo に置かず natural-person 側に残す）。分類は IPC/CPC。

```
(:Patent {appNumber})
  -[:FILED_BY]->(:Applicant)   -[:OWNED_BY]->(:LegalEntity {lei})
  -[:INVENTED_BY]->(:Inventor)
  -[:CLASSIFIED_AS]->(:IpcClass)
  -[:CITES]->(:Patent)
```

## ライセンス

Apache-2.0 + etzhayyim Charter Compliance Rider v3.1（`NOTICE`）。
