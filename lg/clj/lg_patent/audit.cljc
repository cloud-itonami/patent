(ns lg-patent.audit
  "Fire-and-forget OCEL `generic.audit.emit` shim → bpmn-dispatcher.

  clj port helper for the lg-patent graphs (ADR-2606280030). Same contract as
  the sibling wave-1 twins (lg-narou/lg-webmk): each LangGraph node MAY dispatch
  a `generic.audit.emit` XRPC POST to the BPMN dispatcher, preserving the OCEL
  audit trail (vertex_repo_commit traces, process-mining dashboards, RACI
  evidence) WITHOUT re-implementing the OCEL schema.

  The dispatch is fire-and-forget: failure to emit MUST NOT block the node.
  Audit loss is logged but never raised — the checkpoint is the source of truth.

  Host I/O (HTTP + JSON) is injected/bundled: `babashka.http-client` +
  `cheshire.core` are bb built-ins, required only under :clj so the ns stays
  cljc-portable (the pure `build-payload` fn loads everywhere)."
  (:require [clojure.string :as str]
            #?(:clj [cheshire.core :as json])))

(def default-config {:url "http://bpmn-dispatcher.mitama-udf.svc.cluster.local:8080"
                     :secret "" :timeout-ms 3000 :disabled? true})
(def ^:dynamic *config* default-config)
(def ^:dynamic *http-post* nil)

(defn dispatcher-url []
  (-> (:url *config*)
      (str/replace #"/+$" "")))

(defn- internal-secret [] (str/trim (:secret *config*)))
(defn- audit-timeout-ms [] (long (:timeout-ms *config*)))
(defn audit-disabled? [] (boolean (:disabled? *config*)))

(defn build-payload
  "Pure: the OCEL event body (camelCase wire shape, identical to the sibling
  twins' audit shims)."
  [{:keys [actor activity object-id object-type attributes]}]
  {:actor      actor
   :activity   activity
   :objectId   object-id
   :objectType object-type
   :attributes (or attributes {})})

(defn emit-audit!
  "Send one OCEL event to the dispatcher's `generic.audit.emit`. Synchronous,
  best-effort: any failure (or disabled audit) is swallowed and returns nil.

  Returns :disabled / :ok / {:error ...} for observability (the caller
  ignores it — fire-and-forget)."
  [{:keys [activity object-id] :as ev}]
  (if (audit-disabled?)
    :disabled
    #?(:clj
       (let [payload (build-payload ev)
             headers (cond-> {"Content-Type" "application/json"}
                       (seq (internal-secret)) (assoc "x-internal-trust" (internal-secret)))
             url (str (dispatcher-url) "/xrpc/com.etzhayyim.generic.audit.emit")]
         (try
           (when-not (fn? *http-post*) (throw (ex-info "Patent audit requires explicit HTTP" {})))
           (*http-post* url {:headers headers
                           :body (json/generate-string payload)
                           :timeout (audit-timeout-ms)})
           :ok
           (catch Exception e
             (binding [*out* *err*]
               (println (str "audit.emit failed (non-fatal): " (.getMessage e)
                             " | activity=" activity " id=" object-id)))
             {:error (.getMessage e)})))
       :default :disabled)))

(defn emit-audit-bg
  "Schedule `emit-audit!` without blocking the node. On :clj it fires on a
  `future`; everywhere else it is a no-op. Returns the future (or nil) — the
  caller never awaits it."
  [ev]
  #?(:clj (future (emit-audit! ev)) :default nil))
