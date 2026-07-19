#!/usr/bin/env bb
;; lg-patent — bb-native test runner (clojure.test; no shell). ADR-2606280030.
;;
;; Per the repo-wide rule (root CLAUDE.md §"Operational code = clj/bb"): new
;; first-party tooling is clj/bb, NOT shell. This is the .clj runner the repo
;; rule mandates for ported actors/apps (replaces run_tests.sh).
;;
;;   bb run_tests.clj          ; from 60-apps/etzhayyim-project-patent/lg/
;;   bb test                   ; via the scoped bb.edn task
(require '[babashka.http-client :as http]
         '[cheshire.core :as json]
         '[clojure.test :as t]
         '[org.httpkit.server :as httpkit]
         '[lg-patent.audit :as audit]
         '[lg-patent.cron :as cron]
         '[lg-patent.graphs.ingest-uspto-weekly :as ingest]
         '[lg-patent.server :as server])

(defn- env [name default] (or (System/getenv name) default))
(defn patentsview-get [url]
  (let [resp (http/get url {:headers {"Accept" "application/json"} :throw false})]
    (if (>= (:status resp) 400)
      {::ingest/http-error (:status resp)}
      (try (json/parse-string (:body resp) true)
           (catch Exception _ {::ingest/parse-error true :raw (:body resp)})))))

(defn handler [request]
  (binding [audit/*config* {:url (env "BPMN_DISPATCHER_INTERNAL_URL" (:url audit/default-config))
                            :secret (env "BPMN_DISPATCHER_INTERNAL_SECRET" "")
                            :timeout-ms (long (* 1000 (Double/parseDouble (env "LG_AUDIT_TIMEOUT_SEC" "3.0"))))
                            :disabled? (= "1" (env "LG_AUDIT_DISABLED" "0"))}
            audit/*http-post* http/post
            cron/*config* {:enabled? (contains? #{"1" "true" "yes"}
                                                 (clojure.string/lower-case (env "LG_CRON_ENABLED" "true")))
                           :langgraph-json (env "LANGGRAPH_JSON" "/app/langgraph.json")}
            ingest/*config* {:patentsview-url (env "PATENTSVIEW_URL" (:patentsview-url ingest/*config*))}
            ingest/*http-get* patentsview-get
            server/*api-key* (env "LG_API_KEY" "")]
    (server/handler request)))

(defn start-server! [port]
  (server/start! (fn [_ options] (httpkit/run-server handler options)) {:port port}))

(def suites
  '[lg-patent.test-audit-cron
    lg-patent.test-graphs
    lg-patent.test-server])

(apply require suites)

(let [{:keys [fail error]} (apply t/run-tests suites)]
  (if (zero? (+ fail error))
    (println "── lg-patent: ALL suites green ──")
    (do (println "── lg-patent: FAILURES above ──")
        (System/exit 1))))
