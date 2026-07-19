(ns lg-patent.test-audit-cron
  "Tests for the audit shim payload + cron spec loading / fire-input shaping
  (clojure.test analogue of the cron/langgraph.json assertions in test_smoke.py)."
  (:require [clojure.test :refer [deftest is]]
            [lg-patent.audit :as audit]
            [lg-patent.cron :as cron]))

;; ── audit ─────────────────────────────────────────────────────────────────

(deftest audit-build-payload
  (let [p (audit/build-payload {:actor "did:web:patent.etzhayyim.com"
                                :activity "patent.blob.convert"
                                :object-id "blob:1"
                                :object-type "patent.blob"
                                :attributes {:converted 3}})]
    (is (= "patent.blob.convert" (:activity p)))
    (is (= "blob:1" (:objectId p)))
    (is (= "patent.blob" (:objectType p)))
    (is (= {:converted 3} (:attributes p)))))

(deftest audit-build-payload-default-attributes
  (is (= {} (:attributes (audit/build-payload {:actor "a" :activity "x"
                                               :object-id "i" :object-type "t"})))))

(deftest audit-secret-is-an-explicit-capability
  (let [wire (atom nil)]
    (binding [audit/*config* {:url "http://audit.internal" :secret "purpose-bound"
                              :timeout-ms 1000 :disabled? false}
              audit/*http-post* (fn [url opts] (reset! wire [url opts]) {:status 200})]
      (is (= :ok (audit/emit-audit! {:actor "a" :activity "x" :object-id "i"})))
      (is (= "purpose-bound" (get-in @wire [1 :headers "x-internal-trust"]))))))

;; ── cron ──────────────────────────────────────────────────────────────────

(deftest cron-load-specs-filters
  (let [cfg {"crons" [{"schedule" "*/5 * * * *" "graph" "blob_convert" "input" {"limit" 25}}
                      {"schedule" "0 2 * * 0" "graph" "ingest_uspto_weekly" "input" {}}
                      {"schedule" "* * * * *"}                ; no graph → dropped
                      {"graph" "blob_convert"}                ; no schedule → dropped
                      "not-a-map"]}]
    (is (= 2 (count (cron/load-cron-specs cfg))))))

(deftest cron-empty-crons
  (is (= [] (cron/load-cron-specs {"crons" []}))))

(deftest cron-fire-input-copy
  (is (= {"limit" 25} (cron/build-fire-input {"limit" 25})))
  (is (= {} (cron/build-fire-input nil))))

(deftest cron-thread-id
  (is (= "cron:blob_convert:1800" (cron/fire-thread-id "blob_convert" 1800))))

(deftest cron-start-plans-known-graphs
  ;; both patent crons reference known graphs → 2 jobs planned
  (with-redefs [cron/read-langgraph-json
                (fn [_] {"crons" [{"schedule" "*/5 * * * *" "graph" "blob_convert" "input" {"limit" 25}}
                                  {"schedule" "0 2 * * 0" "graph" "ingest_uspto_weekly" "input" {}}
                                  {"schedule" "@daily" "graph" "unknown_graph"}]})]
    (let [plan (cron/start-cron {"blob_convert" :g1 "ingest_uspto_weekly" :g2})]
      (is (= 2 (:registered plan)))
      (is (= #{"blob_convert" "ingest_uspto_weekly"}
             (set (map :graph (:jobs plan))))))))
