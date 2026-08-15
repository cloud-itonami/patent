# Operator Quickstart — patent

clone から**緑**までの最短経路。

この repo で手元から走るのは 3 つの木で、必要なものがそれぞれ違う。**依存の要らない
順**に並べてある。急いで動作を確かめたいだけなら経路 A で足りる（30 秒、インストール不要）。

| 経路 | 木 | 要るもの | 所要 |
|---|---|---|---|
| **A** | `worker/python/` | Python 3.11+ だけ | 秒 |
| **B** | `lg/clj/` | babashka | 初回のみ依存取得 |
| **C** | `kotoba/` | Node + **`etzhayyim` org への SSH 読み取り権限** | 初回 9 分（実測） |

`appview/` と `lg/lg_patent/`（Python 本体）は**この repo からは走らない** — 理由は
最後の節と README.md の表。

**実測環境**（2026-08-15 にこの手順を通したときの値。他の環境では所要時間が違う）:
macOS / darwin 25.3.0 (arm64) · Python **3.14.5** · babashka **1.12.218** ·
Node **v26.3.0** · npm **11.16.0** · load average 37（このマシンは並行作業で混んでいた）

## 0. clone

```bash
git clone git@github.com:cloud-itonami/patent.git
cd patent
```

---

## 経路 A — 期限切れ医薬品特許 worker（依存なし）

`worker/python/patent_expiry_worker.py` は **標準ライブラリだけ**で動く。
`psycopg` は `try/except` で任意扱いなので、入っていなくても dry-run 経路に落ちる。

```bash
python3 worker/python/patent_expiry_worker.py dry-run
```

**期待する出力**: 1 行の JSON。`collect` → `screen` → `plan` → `handoff` →
`draft` → `validation` → `startRequest` → `startAck` → `progress` の全段が入り、
それぞれ `"ok": true`。exit 0。

サブコマンドは 13 個ある（`--help` で一覧）。個別に叩けば段ごとに確かめられる:

```bash
python3 worker/python/patent_expiry_worker.py blocker \
  '{"patentVertexId":"at://x","patentNumber":"EX-1","jurisdiction":"USA","blockerType":"regulatory_exclusivity","blockingUntil":"2027-01-01","dryRun":true}'
# → {"active": true, "blockingUntil": "2027-01-01", "ok": true, "status": "active_blocker", ...}

python3 worker/python/patent_expiry_worker.py validate-draft \
  '{"batchPayload":{"manufacturerOrgId":"org-demo","plantOrgId":"plant-demo","productCode":"demo-amoxicillin","batchNumber":"B-001"},"dryRun":true}'
# → {"findings": ["recommended_dosageForm", "recommended_targetMarket"], "ok": true, "passed": true, ...}
```

> ⚠ **`dry-run` が exit 0 を返すことは「テストが通った」ではない。** この木には
> テストスイートが無い。dry-run が示すのは *pipeline が最後まで実行された*ことだけで、
> 各段の出力が正しいかどうかは誰も検査していない。緑と読まないこと。
> 本当の検査があるのは経路 B と C である。

`vertexId` は入力から導かれるハッシュなので、**同じ入力なら毎回同じ値**になる
（上の 2 例の値は再現する）。`collect` の `asOf` は実行日なので変わる。

## 経路 B — LangGraph サーバの cljc twin（babashka）

`lg/clj/` は配備されている Python サーバ（`lg/lg_patent/**.py`）の cljc twin で、
**この repo で唯一 test suite を持つ木**である（ADR-2606280030 WAVE 2）。

```bash
cd lg
bb test
```

**期待する出力**: `Ran 38 tests containing 78 assertions.` / `0 failures, 0 errors.` /
`── lg-patent: ALL suites green ──`（3 suite: `test-audit-cron` / `test-graphs` /
`test-server`）。

固定しているのは graph の**位相**（`GRAPHS` レジストリ、NSID マップ、cron 式
`*/5 * * * *` と `0 2 * * 0`）と **HTTP 面**（`/runs` `/runs/stream` SSE
`/xrpc/{nsid}` `/threads/{tid}/state` `/graphs` `/ok` `/health`）が Python 側と
一致していること。ネットワークと native 境界は注入点（`*http-get*` `*convert-blob*`
等）になっていて既定は no-op なので、**オフラインで完結する**。

> `bb.edn` はこの木にローカルなもの。ワークスペース全体では bb は退役済み
> （ADR-2607173000、新しいスクリプトは nbb で書く）だが、**既存のこの suite は
> bb でしか動かない**ので、走らせる分にはこのまま使う。

## 経路 C — `kotoba/` TypeScript package

### 前提

- Node 20 以上（実測 v26.3.0）
- **GitHub `etzhayyim` org への SSH 読み取り権限。** 依存 2 本
  （`@etzhayyim/sdk` / `@etzhayyim/sdk-mock`）は `package.json` に `git+https://…`
  と書かれているが npm は `ssh://git@github.com/etzhayyim/…` に解決する。
  鍵が通らないとここで止まる。

### ⚠ `~/.npmrc` に `allow-scripts[]=` があると必ず失敗する

このワークスペースの標準的な `~/.npmrc` は
`allow-scripts[]=@anthropic-ai/claude-code` を持っている。npm 11 は git 依存を
準備するとき入れ子の `npm install` を **project スコープ**で走らせるので、
user 設定の `allow-scripts[]` がそこへ継承されて拒否される:

```
npm error code EALLOWSCRIPTS
npm error --allow-scripts is not allowed in project-scoped installs.
```

**これは repo の欠陥ではなく手元の設定**である（`gov` repo が先に同じものを踏んで
診断している）。回避は、その行だけ落とした npmrc を渡す（`~/.npmrc` は書き換えない）:

```bash
grep -v '^allow-scripts' ~/.npmrc > /tmp/npmrc-patent
cd kotoba
npm install --userconfig /tmp/npmrc-patent
```

**実測 9 分 / `node_modules` 240 MB / exit 0**（load average 37 のマシンで。空いていれば
もっと速い）。長いのは git 依存を入れ子で install して `tsc` でビルドするため
（`@etzhayyim/sdk` が `atproto-client` `base-l2` 等を芋づるで引く）。初回だけ。

> このワークスペースには build を 1 本に絞る governor がある。他の作業と衝突させたく
> なければ次を使う:
> `node <superproject>/scripts/resource-guard.mjs run build -- npm install --userconfig /tmp/npmrc-patent`

### ⚠ install が終わるまで測らない（`node_modules/` の存在は完了の合図ではない）

この手順を書いたとき、**`node_modules/` ディレクトリができた時点を完了と誤認して**
テストと型検査を走らせ、次の 2 つを「repo の欠陥」として記録しかけた:

- `sh: vitest: command not found`（`node_modules/.bin/` がまだ空だった）
- `tsc` が exit 2 で `TS2307: Cannot find module '@etzhayyim/sdk'` + `TS7006` 8 件

**どちらも install 途中の姿で、完了後は消える。** git 依存の `dist/*.d.ts` は
install の最後の方に書かれるので、その前に `tsc` を回すと「型が無い」と正しく
報告され、それが恒久的な欠陥に見える。**`npm install` の exit を待つこと** ——
ディレクトリの有無で判定しない。

```bash
ls node_modules/.bin/ | wc -l   # 14 なら bin は張られている（tsc, vitest ほか）
```

### テスト

```bash
npm test          # vitest run
npm run typecheck # tsc --noEmit（src/** のみ。test/ は tsconfig の include 外）
```

**期待する出力**: `Test Files 1 passed (1)` / `Tests 4 passed (4)`（実測 326 ms）。
`npm run typecheck` は**無出力・exit 0**（実測）。

固定している不変条件は README.md の表のとおり——**語彙の外を拒否すること**
（`jurisdiction` は 2 文字、`sourceOffice` は 4 値、`scheme` は 4 値、`lei` は
英数 20 桁）と、party / classification / citation が**実在する patent を
参照していること**（不在なら `patentNotFound`）。`MockEtzhayyim` を使うので
PDS もネットワークも要らない。

固定している不変条件は README.md の表のとおり。`MockEtzhayyim` を使うので
PDS もネットワークも要らない。

---

## この検査が本当に効いていることを確かめる

**緑を信じる前に、赤くできることを一度見る。** 落ちない検査は劇場である。

### 経路 B で（依存が軽いのでこちらが速い）

`health` ノードの戻り値を反転させる:

```bash
cd lg
cp clj/lg_patent/graphs/health.cljc /tmp/health.cljc.orig
perl -0pi -e 's/\{:ok true :ts \(now-ms\)\}/{:ok false :ts (now-ms)}/' clj/lg_patent/graphs/health.cljc
bb test
```

**期待する出力**: `Ran 38 tests containing 78 assertions.` / **`3 failures, 0 errors.`** /
`── lg-patent: FAILURES above ──`。`test-graphs` と `test-server` の両方が落ちる
（`expected: (true? (:ok s))` と `expected: (true? (get-in body [:result :ok]))`）——
graph 単体と HTTP 面の両方から見ているため。

戻す:

```bash
cp /tmp/health.cljc.orig clj/lg_patent/graphs/health.cljc
bb test   # 0 failures に戻ることを確認する
```

### 経路 C で

LEI の桁数検査を緩めて、短い LEI が通るようにする:

```bash
cd kotoba
cp src/types.ts /tmp/types.ts.orig
perl -pi -e 's/\[A-Z0-9\]\{20\}/[A-Z0-9]{1,20}/' src/types.ts
npm test
```

**期待する出力: `Tests 1 failed | 3 passed (4)`**（実測）。落ちるのは
`test/patent.test.ts:43` ——`"SHORT"` という 5 文字の LEI を `rejected` と
期待している行が `Received: "added"` になる。戻す:

```bash
cp /tmp/types.ts.orig src/types.ts
npm test   # 4 passed に戻ることを確認する
```

---

## この repo から走らないもの

手順を探して時間を溶かさないように、**動かないものを名指しで**書いておく。

| もの | なぜ走らないか |
|---|---|
| `lg/lg_patent/**.py`（配備されている Python サーバ） | `lg/langgraph.json` の `dependencies` が `../../../40-engine/kotoba/crates/kotoba-kotodama/py` を指しており、移行後のこの repo からは解決しない。`pyproject.toml` も `kotodama` を要求するが同じ場所に無い |
| `appview/etzhayyim-wasm-patent-p4t3nt01/` | `kotodama.jsonld` **1 ファイルだけ**。`staticDir: /wasm/svelte/dist` と HTTP route を宣言するが、svelte も wasm もこの repo に無い |
| `CLAUDE.md` の «PDS Shared Executor が 7 pipeline» / `vertex_patent` (migration 0037) | executor も migration もこの repo に無い（移行前の monorepo の記述） |
| `kotodama.edn` の 11 本の BPMN プロセス | `.bpmn` の実体は `../../etzhayyim-root/00-contracts/` にあり、ここには無い |
| worker の `serve` サブコマンド | Zeebe gateway（`AGENTGATEWAY_MCP_URL`）と RisingWave（`RW_URL`）が要る。手元では `dry-run` を使う |

移行の出所は `migration.edn`（`etzhayyim/root` の
`60-apps/etzhayyim-project-patent`、34 ファイル / 147,256 バイト）。
