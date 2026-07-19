(ns lg-patent.graphs.health
  "patent `health` graph — liveness probe.

  Faithful clj port of `lg/lg_patent/graphs/health.py` (ADR-2606280030).
  Topology: START → health → END. The `health` node returns {:ok true :ts <ms>}."
  (:require [langgraph.graph :as g]))

(defn- now-ms [] #?(:clj (System/currentTimeMillis) :default 0))

(defn node-health
  "Port of `_node_health`: returns {:ok true :ts <epoch-ms>}."
  [_state]
  {:ok true :ts (now-ms)})

(defn build
  "Compile the health StateGraph (START → health → END)."
  []
  (-> (g/state-graph)
      (g/add-node :health node-health)
      (g/set-entry-point :health)
      (g/set-finish-point :health)
      (g/compile-graph)))

(def GRAPH (build))
