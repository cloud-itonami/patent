(ns lg-patent.cron
  "In-process cron spec loader (port of `lg_patent/cron.py`).

  The python module registered APScheduler `CronTrigger` jobs read from
  `langgraph.json`'s `crons` array (patent ships TWO crons: blob_convert every
  5 min, ingest_uspto_weekly Sun 02:00 UTC). What carries behaviour and is
  test-covered here is the SPEC LOADING + the per-fire input/thread-id shaping;
  the wall-clock scheduling runtime itself is left to the deployment layer.

  DEVIATION (noted in PR): APScheduler crontab scheduling is NOT reimplemented
  (no cron-expression scheduler under bb). The repo direction (clj/bb over the
  kotoba Datom log) prefers cell-runner cron residency anyway. `start-cron`
  returns a {:registered n :jobs [...]} plan of which jobs WOULD register (those
  whose `graph` is a known graph), or nil when disabled / no specs. The spec
  filtering + fire-input/thread-id logic are faithful to `_load_cron_specs` /
  `_make_fire` and covered by tests."
  (:require [clojure.string :as str]
            #?(:clj [cheshire.core :as json])))

(def ^:dynamic *config* {:enabled? true :langgraph-json "/app/langgraph.json"})

(defn cron-enabled?
  "Port of `start_cron`'s env gate (LG_CRON_ENABLED ∈ {1,true,yes})."
  []
  (boolean (:enabled? *config*)))

(defn load-cron-specs
  "Filter a parsed langgraph.json `crons` array to dict entries that have both
  a :schedule and a :graph (faithful to `_load_cron_specs`)."
  [cfg]
  (->> (or (get cfg "crons") (get cfg :crons) [])
       (filter #(and (map? %)
                     (or (get % "schedule") (get % :schedule))
                     (or (get % "graph") (get % :graph))))
       vec))

(defn read-langgraph-json
  "Read + parse langgraph.json (LANGGRAPH_JSON env or the given path). Returns
  {} on missing/invalid (logged), like `_load_cron_specs`'s guards."
  [path]
  #?(:clj
     (let [p (or path (:langgraph-json *config*))
           f (java.io.File. ^String p)]
       (if-not (.exists f)
         (do (binding [*out* *err*] (println (str "langgraph.json not found at " p))) {})
         (try (json/parse-string (slurp f)) (catch Exception e
                                              (binding [*out* *err*]
                                                (println (str "langgraph.json parse failed: " (.getMessage e))))
                                              {}))))
     :default {}))

(defn build-fire-input
  "Pure: shape one fire's graph input from a cron spec's base `input`
  (faithful to `_make_fire`, which fires `dict(base_input)` — a copy). patent's
  crons carry static inputs ({limit 25} / {}), so this is a defensive copy."
  [base]
  (into {} (or base {})))

(defn fire-thread-id
  "Port of `_make_fire`'s thread_id = `cron:{name}:{epoch}`."
  [graph-name epoch-secs]
  (str "cron:" graph-name ":" epoch-secs))

(defn start-cron
  "Port of `start_cron`. Returns nil when disabled or when no valid specs
  reference a known graph; otherwise a {:registered n :jobs [...]} plan. A real
  scheduler is not booted (see DEVIATION)."
  [graphs & [{:keys [langgraph-json-path]}]]
  (when (cron-enabled?)
    (let [specs (load-cron-specs (read-langgraph-json langgraph-json-path))
          jobs (->> specs
                    (keep (fn [spec]
                            (let [gname (str (or (get spec "graph") (get spec :graph)))]
                              (when (contains? graphs gname)
                                {:graph gname
                                 :schedule (str (or (get spec "schedule") (get spec :schedule)))
                                 :base-input (build-fire-input
                                              (or (get spec "input") (get spec :input) {}))}))))
                    vec)]
      (when (seq jobs)
        {:registered (count jobs) :jobs jobs}))))

(defn stop-cron
  "Port of `stop_cron` — nil-guarded no-op (no live scheduler object; see
  DEVIATION)."
  [scheduler]
  (when scheduler :stopped))
