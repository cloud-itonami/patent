(ns lg-patent.graphs.blob-convert
  "patent `blob_convert` graph — convert pending PDF blobs → webp + OCR.

  Port of `lg/lg_patent/graphs/blob_convert.py` (ADR-2606280030), which in the
  python re-exported `kotodama.langgraph_graphs.patent_blob_convert/build_graph`.
  That kotodama module is NOT vendored into this checkout, so its internal node
  bodies cannot be byte-faithfully transcribed; this port reconstructs the
  pipeline's TOPOLOGY + contract from the langgraph.json cron (`{limit: 25}`,
  every 5 min) + the actor CLAUDE.md (`vertex_patent` + blob conversion), with
  the two side-effecting boundaries left INJECTABLE per the actor-swap pattern:

    *list-pending*  — store seam: pending-blob rows to convert. RisingWave/psycopg
                      is FORBIDDEN by the substrate boundary; the target is the
                      kotoba Datom log. Default = nil (store not configured).
    *convert-blob*  — native boundary: PDF → webp + OCR (poppler/ffmpeg/tesseract
                      via babashka.process at deploy). Default = identity-marker.
    *write-record*  — store seam: persist the converted record. Default = no-op.

  Topology (faithful intent): claim-pending → convert → record → END.
  A node short-circuits (passes state through) once `:error`/`:skip` is present,
  mirroring the recap twin's error-skips-downstream idiom.

  DEVIATIONS (noted in PR): kotodama source absent → topology reconstructed +
  injectable seams; RisingWave → kotoba-Datom-log store seam; no per-node
  RetryPolicy (langgraph-clj has no add-node equivalent)."
  (:require [langgraph.graph :as g]))

(def ^:dynamic *list-pending*
  "Store seam → seq of pending blob rows (each {:blob_key ... :patent_id ...}),
  or nil when no store is configured. Bind in tests / wire to kotoba at deploy."
  (fn [_limit] nil))

(def ^:dynamic *convert-blob*
  "Native boundary: convert one pending PDF blob → {:webp_key ... :ocr_text ...}.
  Default is an offline identity-marker (no poppler/tesseract under bb)."
  (fn [row] (assoc row :converted true :webp_key nil :ocr_text nil)))

(def ^:dynamic *write-record*
  "Store seam: persist one converted record. Default no-op returns the row."
  (fn [row] row))

(defn claim-pending
  "Read up to :limit (default 25) pending blobs from the store seam. When the
  store is unconfigured (nil), short-circuit with a skip marker."
  [state]
  (let [limit (or (:limit state) 25)
        rows  (*list-pending* limit)]
    (if (nil? rows)
      {:status "skipped" :error "store not configured" :converted 0 :pending []}
      {:pending (vec (take limit rows)) :limit limit})))

(defn convert
  "Convert each pending blob via the native boundary. Pass-through on skip."
  [state]
  (if (:error state)
    {}
    (let [converted (mapv *convert-blob* (or (:pending state) []))]
      {:records converted :converted (count converted)})))

(defn record
  "Persist each converted record via the store seam. Pass-through on skip."
  [state]
  (if (:error state)
    {}
    (let [written (mapv *write-record* (or (:records state) []))]
      {:status "done" :written (count written) :converted (count written)})))

(defn build []
  (-> (g/state-graph)
      (g/add-node :claim-pending claim-pending)
      (g/add-node :convert convert)
      (g/add-node :record record)
      (g/set-entry-point :claim-pending)
      (g/add-edge :claim-pending :convert)
      (g/add-edge :convert :record)
      (g/set-finish-point :record)
      (g/compile-graph)))

(def GRAPH (build))
