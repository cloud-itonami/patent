(ns lg-patent.test-server
  "clojure.test port of tests/test_smoke.py (server / NSID / langgraph.json parity
  + handler routing without a socket)."
  (:require [clojure.test :refer [deftest is]]
            [cheshire.core :as json]
            [langchain.runnable :as r]
            [lg-patent.server :as srv]))

(def expected-graphs #{"health" "blob_convert" "ingest_uspto_weekly"})

(def expected-nsid-map
  {"com.etzhayyim.apps.patent.blobConvert"       "blob_convert"
   "com.etzhayyim.apps.patent.ingestUsptoWeekly" "ingest_uspto_weekly"})

(def expected-cron-graphs #{"blob_convert" "ingest_uspto_weekly"})

;; ── registry parity (mirrors test_smoke.py) ─────────────────────────────────

(deftest graphs-match-expected-set
  (is (= expected-graphs (set (keys srv/GRAPHS)))))

(deftest nsid-map-completeness
  (is (= expected-nsid-map srv/NSID-MAP)))

(deftest nsid-map-references-known-graphs
  (doseq [[nsid gname] srv/NSID-MAP]
    (is (contains? srv/GRAPHS gname) (str nsid " → " gname " not in GRAPHS"))))

(deftest all-graphs-are-invocable
  (doseq [[_ graph] srv/GRAPHS]
    (is (satisfies? r/IRunnable graph))))

;; ── langgraph.json parity (test_langgraph_json_*) ───────────────────────────

(def lg-dir
  (let [f (java.io.File. ^String (str *file*))]
    ;; clj/lg_patent/test_server.cljc → lg/ is three parents up from this file
    (.getParentFile (.getParentFile (.getParentFile (.getAbsoluteFile f))))))

(defn- read-langgraph-json []
  (json/parse-string (slurp (java.io.File. lg-dir "langgraph.json"))))

(deftest langgraph-json-graphs-match-server
  (let [cfg (read-langgraph-json)]
    (is (= expected-graphs (set (keys (get cfg "graphs")))))))

(deftest langgraph-json-cron-graphs
  (let [cfg (read-langgraph-json)
        cron-graphs (set (keep #(get % "graph") (get cfg "crons")))]
    (is (= expected-cron-graphs cron-graphs))))

(deftest langgraph-json-blob-convert-every-5min
  (let [cfg (read-langgraph-json)
        blob (first (filter #(= "blob_convert" (get % "graph")) (get cfg "crons")))]
    (is (some? blob))
    (is (= "*/5 * * * *" (get blob "schedule")))))

(deftest langgraph-json-rw-checkpointer
  (let [cfg (read-langgraph-json)]
    (is (= "RW_URL" (get-in cfg ["checkpointer" "conn_str_env"])))))

;; ── handler routing (no socket) ──────────────────────────────────────────

(deftest ok-endpoint-lists-graphs
  (let [resp (srv/handler {:request-method :get :uri "/ok"})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (true? (:ok body)))
    (is (= "lg-patent" (:app body)))
    (is (= expected-graphs (set (:graphs body))))))

(deftest health-endpoint
  (let [resp (srv/handler {:request-method :get :uri "/health"})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (true? (:ok body)))
    (is (= "lg-patent" (:app body)))))

(deftest graphs-endpoint
  (let [resp (srv/handler {:request-method :get :uri "/graphs"})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (= expected-graphs (set (:graphs body))))))

(deftest runs-unknown-graph-404
  (let [resp (srv/handler {:request-method :post :uri "/runs"
                           :body (json/generate-string {:assistant_id "nonexistent"})})]
    (is (= 404 (:status resp)))))

(deftest runs-health-ok
  (let [resp (srv/handler {:request-method :post :uri "/runs"
                           :body (json/generate-string {:assistant_id "health" :input {}})})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (true? (:ok body)))
    (is (true? (get-in body [:result :ok])))
    (is (string? (:thread_id body)))))

(deftest runs-nsid-resolves-to-graph
  ;; assistant_id may be an NSID → NSID-MAP resolves it (python NSID_MAP.get)
  (let [resp (srv/handler {:request-method :post :uri "/runs"
                           :body (json/generate-string
                                  {:assistant_id "com.etzhayyim.apps.patent.blobConvert"
                                   :input {}})})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (true? (:ok body)))
    ;; no store wired → graph runs + returns its skip marker in :result
    (is (= "skipped" (get-in body [:result :status])))))

(deftest unknown-nsid-xrpc-404
  (let [resp (srv/handler {:request-method :post
                           :uri "/xrpc/com.etzhayyim.apps.patent.unknownMethod"
                           :body "{}"})]
    (is (= 404 (:status resp)))))

(deftest xrpc-blob-convert-dispatches
  (let [resp (srv/handler {:request-method :post
                           :uri "/xrpc/com.etzhayyim.apps.patent.blobConvert"
                           :body (json/generate-string {:limit 5})})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (true? (:ok body)))
    (is (map? (:result body)))))

(deftest thread-state-empty-snapshot
  (let [resp (srv/handler {:request-method :get :uri "/threads/abc/state"})
        body (json/parse-string (:body resp) true)]
    (is (= 200 (:status resp)))
    (is (= "abc" (:thread_id body)))))

(deftest api-key-guard-rejects-mismatch
  (with-redefs [srv/api-key (constantly "secret")]
    (let [resp (srv/handler {:request-method :post :uri "/runs"
                             :headers {"x-api-key" "wrong"}
                             :body (json/generate-string {:assistant_id "health"})})]
      (is (= 401 (:status resp))))))

(deftest key-normalization
  (is (= {:object-type "patent" :patent-id "n1"}
         (srv/normalize-input {"objectType" "patent" "patent_id" "n1"}))))

(deftest server-start-requires-explicit-capability
  (is (thrown-with-msg? clojure.lang.ExceptionInfo #"explicit server capability"
                        (srv/start! nil {:port 0}))))
