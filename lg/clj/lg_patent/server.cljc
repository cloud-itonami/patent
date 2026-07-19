(ns lg-patent.server
  "OSS HTTP server for lg-patent (port of `lg_patent/server.py`, FastAPI → httpkit).

  Same HTTP surface (NSID namespace com.etzhayyim.apps.patent.*):
    POST /runs                 → invoke a graph synchronously → {ok result thread_id}
    POST /runs/stream          → graph superstep events as SSE
    POST /xrpc/{nsid}          → XRPC shim (NSID → graph) → {ok result}
    GET  /threads/{tid}/state  → latest checkpoint snapshot
    GET  /graphs               → {graphs [...]}
    GET  /ok | /health         → liveness / readiness {ok app ts graphs}

  Auth: optional `LG_API_KEY` enforces `x-api-key` on /runs paths (the python
  `_auth` Depends). /xrpc/{nsid} is unauthenticated (parity with the python).

  The request routing is a pure ring-style `handler` (testable without a socket);
  `-main`/`start!` boot org.httpkit.server (bb built-in).

  DEVIATIONS (noted in PR):
   - FastAPI/uvicorn → org.httpkit.server; pydantic body → cheshire parse.
   - The RW `_RwAsyncPostgresSaver` checkpointer (checkpointer.py) is RisingWave/
     Postgres-specific and NOT ported (charter deprecates RisingWave in favour of
     the kotoba Datom log, ADR-2605312345). Graphs compile without a checkpointer,
     so /threads/{tid}/state returns an empty snapshot until one is wired.
   - /runs/stream computes events then flushes them as SSE (not incrementally
     streamed). thread_id is generated as `run-/stream-/xrpc-<epoch>` like python."
  (:require [clojure.string :as str]
            [langgraph.graph :as g]
            [lg-patent.graphs.health :as health]
            [lg-patent.graphs.blob-convert :as blob-convert]
            [lg-patent.graphs.ingest-uspto-weekly :as ingest-uspto-weekly]
            [lg-patent.cron :as cron]
            #?(:clj [cheshire.core :as json])))

(def ^:dynamic *api-key* "")

(def GRAPHS
  {"health"              health/GRAPH
   "blob_convert"        blob-convert/GRAPH
   "ingest_uspto_weekly" ingest-uspto-weekly/GRAPH})

(def NSID-MAP
  {"com.etzhayyim.apps.patent.blobConvert"       "blob_convert"
   "com.etzhayyim.apps.patent.ingestUsptoWeekly" "ingest_uspto_weekly"})

(defn api-key [] (str/trim *api-key*))

;; ── key normalization (camelCase | snake_case → kebab keyword) ──────────

(defn camel->snake [s]
  (->> s
       (map-indexed (fn [i ch]
                      (if (and (Character/isUpperCase ^char ch) (pos? i))
                        (str "_" (Character/toLowerCase ^char ch))
                        (str (Character/toLowerCase ^char ch)))))
       (apply str)))

(defn key->kw [k]
  (-> (name k) camel->snake (str/replace "_" "-") keyword))

(defn normalize-input [m]
  (into {} (map (fn [[k v]] [(key->kw k) v]) m)))

;; ── JSON-safe serialization of graph state ──────────────────────────────

(defn safe-json [v]
  (cond
    (map? v) (into {} (map (fn [[k x]] [k (safe-json x)]) v))
    (sequential? v) (mapv safe-json v)
    (or (string? v) (number? v) (boolean? v) (nil? v)) v
    :else (let [s (pr-str v)] (subs s 0 (min 200 (count s))))))

;; ── responses ───────────────────────────────────────────────────────────

(defn- json-resp [status body]
  {:status status
   :headers {"Content-Type" "application/json"}
   :body #?(:clj (json/generate-string body) :default body)})

(defn- parse-body [req]
  #?(:clj
     (let [b (:body req)
           s (cond (nil? b) "" (string? b) b :else (slurp b))]
       (if (str/blank? s) {} (json/parse-string s)))
     :default (or (:parsed-body req) {})))

(defn- now-ms [] #?(:clj (System/currentTimeMillis) :default 0))
(defn- now-secs [] (quot (now-ms) 1000))

(defn- require-api-key [req]
  (let [k (api-key)]
    (or (str/blank? k)
        (= k (get-in req [:headers "x-api-key"])))))

(defn- thread-id-of
  "Port of the python `(body.config.configurable.thread_id) or <prefix>-<epoch>`."
  [body prefix]
  (or (not-empty (str (get-in body ["config" "configurable" "thread_id"])))
      (str prefix "-" (now-secs))))

;; ── route handlers ───────────────────────────────────────────────────────

(defn- handle-health [_]
  ;; python `/health` + `/ok`: {ok app ts graphs}
  (json-resp 200 {:ok true :app "lg-patent" :ts (now-ms) :graphs (vec (keys GRAPHS))}))

(defn- handle-list-graphs [_]
  (json-resp 200 {:graphs (vec (keys GRAPHS))}))

(defn- resolve-graph
  "NSID_MAP.get(assistant_id, assistant_id) — NSID maps, else the raw name."
  [assistant-id]
  (or (NSID-MAP assistant-id) assistant-id))

(defn- handle-runs [req]
  (if-not (require-api-key req)
    (json-resp 401 {:detail "invalid x-api-key"})
    (let [body (parse-body req)
          assistant (str (or (get body "assistant_id") "health"))
          gname (resolve-graph assistant)]
      (if-not (contains? GRAPHS gname)
        (json-resp 404 {:detail (str "graph '" gname "' not found")})
        (let [graph (GRAPHS gname)
              tid (thread-id-of body "run")
              input (normalize-input (or (get body "input") {}))]
          (try
            (let [result (g/invoke graph input)]
              (json-resp 200 {:ok true :result (safe-json result) :thread_id tid}))
            (catch #?(:clj Exception :default :default) e
              (json-resp 500 {:detail (let [m (str #?(:clj (.getMessage e) :default e))]
                                        (subs m 0 (min 500 (count m))))}))))))))

(defn- handle-stream [req]
  (if-not (require-api-key req)
    (json-resp 401 {:detail "invalid x-api-key"})
    (let [body (parse-body req)
          assistant (str (or (get body "assistant_id") "health"))
          gname (resolve-graph assistant)]
      (if-not (contains? GRAPHS gname)
        (json-resp 404 {:detail (str "graph '" gname "' not found")})
        (let [graph (GRAPHS gname)
              input (normalize-input (or (get body "input") {}))
              sse (try
                    (str (->> (g/stream graph input)
                              (map (fn [ev]
                                     (str "data: "
                                          #?(:clj (json/generate-string {:event "values" :data (safe-json (:state ev))})
                                             :default (pr-str (safe-json (:state ev))))
                                          "\n\n")))
                              (apply str))
                         "data: [DONE]\n\n")
                    (catch #?(:clj Exception :default :default) e
                      (str "data: "
                           #?(:clj (json/generate-string {:event "error" :data (str #?(:clj (.getMessage e) :default e))})
                              :default (str e))
                           "\n\n")))]
          {:status 200 :headers {"Content-Type" "text/event-stream"} :body sse})))))

(defn- handle-xrpc [nsid req]
  (let [gname (NSID-MAP nsid)]
    (if (nil? gname)
      (json-resp 404 {:detail (str "NSID '" nsid "' not mapped")})
      (let [graph (GRAPHS gname)
            body (parse-body req)
            input (normalize-input (if (map? body) body {}))]
        (try
          (let [result (g/invoke graph input)]
            (json-resp 200 {:ok true :result (safe-json result)}))
          (catch #?(:clj Exception :default :default) e
            (json-resp 500 {:detail (let [m (str #?(:clj (.getMessage e) :default e))]
                                      (subs m 0 (min 500 (count m))))})))))))

(defn- handle-thread-state [tid _req]
  ;; python aget_state via the (RW) checkpointer; we have none wired → empty snap.
  (let [snap (try (g/get-state (GRAPHS "health") tid) (catch #?(:clj Exception :default :default) _ nil))]
    (json-resp 200 {:thread_id tid
                    :values (safe-json (or (:state snap) {}))
                    :next (vec (or (:frontier snap) []))})))

;; ── router ────────────────────────────────────────────────────────────────

(defn handler
  "Pure ring-style router. req: {:request-method :uri :headers :body}."
  [req]
  (let [m (:request-method req)
        uri (:uri req)]
    (cond
      (and (= m :get) (= uri "/ok")) (handle-health req)
      (and (= m :get) (= uri "/health")) (handle-health req)
      (and (= m :get) (= uri "/graphs")) (handle-list-graphs req)
      (and (= m :post) (= uri "/runs")) (handle-runs req)
      (and (= m :post) (= uri "/runs/stream")) (handle-stream req)
      (and (= m :post) (str/starts-with? uri "/xrpc/"))
      (handle-xrpc (subs uri (count "/xrpc/")) req)
      (and (= m :get) (str/starts-with? uri "/threads/") (str/ends-with? uri "/state"))
      (handle-thread-state (-> uri (subs (count "/threads/")) (str/replace #"/state$" "")) req)
      :else (json-resp 404 {:detail "not found"}))))

#?(:clj
   (defn start!
     "Boot the httpkit server. opts: {:port n}. Returns the stop fn."
     [run-server & [{:keys [port] :or {port 8000}}]]
     (when-not (fn? run-server)
       (throw (ex-info "Patent requires explicit server capability" {})))
     (let [crons (cron/start-cron GRAPHS)]
       (println (str "lg-patent up: graphs=" (vec (keys GRAPHS))
                     " crons=" (or (:registered crons) 0) " port=" port))
       (run-server handler {:port port}))))

#?(:clj
   (defn -main [& _]
     (throw (ex-info "Patent portable runtime requires a host adapter" {}))))
