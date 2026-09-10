(ns lg-patent.test-graphs
  "Node-behavior tests for the ported health + blob_convert + ingest StateGraphs.
  These run OFFLINE — the store/native/network boundaries are injectable, so the
  pipelines verify under bb without RisingWave, poppler, or the live USPTO API."
  (:require [clojure.test :refer [deftest is]]
            [langgraph.graph :as g]
            [lg-patent.graphs.health :as health]
            [lg-patent.graphs.blob-convert :as bc]
            [lg-patent.graphs.ingest-uspto-weekly :as iuw]))

;; ── health ────────────────────────────────────────────────────────────────

(deftest health-node-shape
  (let [s (health/node-health {})]
    (is (true? (:ok s)))
    (is (number? (:ts s)))))

(deftest health-graph-invokes
  (let [out (g/invoke health/GRAPH {})]
    (is (true? (:ok out)))
    (is (number? (:ts out)))))

;; ── blob_convert ─────────────────────────────────────────────────────────

(deftest blob-convert-no-store-skips
  ;; default *list-pending* → nil → "store not configured", 0 converted
  (let [out (g/invoke bc/GRAPH {:limit 25})]
    (is (= "skipped" (:status out)))
    (is (= "store not configured" (:error out)))
    (is (= 0 (:converted out)))))

(deftest blob-convert-happy-path-stubbed
  (binding [bc/*list-pending* (fn [limit]
                                (is (= 25 limit) "default limit 25 passed to store seam")
                                [{:blob_key "b/1.pdf" :patent_id "US-1"}
                                 {:blob_key "b/2.pdf" :patent_id "US-2"}])
            bc/*convert-blob* (fn [row] (assoc row :webp_key (str (:blob_key row) ".webp")
                                               :ocr_text "TXT" :converted true))
            bc/*write-record* (fn [row] (assoc row :written true))]
    (let [out (g/invoke bc/GRAPH {})]
      (is (= "done" (:status out)))
      (is (= 2 (:converted out)))
      (is (= 2 (:written out))))))

(deftest blob-convert-limit-clamps-take
  (binding [bc/*list-pending* (fn [_limit]
                                (mapv (fn [i] {:blob_key (str "b/" i ".pdf")}) (range 10)))]
    (let [out (g/invoke bc/GRAPH {:limit 3})]
      (is (= "done" (:status out)))
      (is (= 3 (:converted out)) "only :limit rows taken"))))

;; ── ingest_uspto_weekly ───────────────────────────────────────────────────

(deftest ingest-network-disabled-skips
  (let [out (g/invoke iuw/GRAPH {:network false})]
    (is (= "skipped" (:status out)))
    (is (= "network disabled" (:error out)))))

(deftest ingest-missing-http-capability-skips
  (binding [iuw/*http-get* nil]
    (let [out (iuw/fetch-uspto {})]
      (is (= "skipped" (:status out)))
      (is (= "HTTP capability not configured" (:error out))))))

(deftest ingest-http-error-skips
  (binding [iuw/*http-get* (fn [_url] {:lg-patent.graphs.ingest-uspto-weekly/http-error 503})]
    (let [out (g/invoke iuw/GRAPH {})]
      (is (= "skipped" (:status out)))
      (is (re-find #"patentsview http 503" (:error out))))))

(deftest ingest-no-store-skips
  ;; network ok (fetch returns patents) but store seam unconfigured → skip at upsert
  (binding [iuw/*http-get* (fn [_url] {:patents [{:patent_id "US-1"} {:patent_id "US-2"}]})]
    (let [out (g/invoke iuw/GRAPH {})]
      (is (= "skipped" (:status out)))
      (is (= "store not configured" (:error out)))
      (is (= 2 (:patents_seen out))))))

(deftest ingest-happy-path-stubbed
  (binding [iuw/*http-get* (fn [_url] {:patents [{:patent_id "US-1"} {:patent_id "US-2"}]})
            iuw/*write-records* (fn [payload]
                                  (is (= 2 (count (:patents payload))))
                                  {:upserted 2})]
    (let [out (g/invoke iuw/GRAPH {})]
      (is (= "done" (:status out)))
      (is (= 2 (:upserted out)))
      (is (= 2 (:patents_seen out))))))
