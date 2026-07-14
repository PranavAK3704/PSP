// Dead-simple shared store for the LATEST resolution trace ({ events, phase }).
// CaptainPanel writes to it as its live trace updates; the standalone Resolution
// Trace view subscribes and renders whatever the last resolution produced. This is
// a module singleton (survives view switches, since only the active view is mounted)
// with a React hook built on useSyncExternalStore — no context/provider needed.

let snapshot = { events: [], phase: "idle" };
const listeners = new Set();

// Write the latest trace. Called from CaptainPanel whenever its events/phase change.
export function setLastTrace(events, phase) {
  snapshot = { events: events || [], phase: phase || "idle" };
  listeners.forEach((fn) => fn());
}

// Stable getter — returns the SAME object reference between writes so
// useSyncExternalStore doesn't loop.
export function getLastTrace() {
  return snapshot;
}

function subscribe(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

// React hook: components re-render when a new trace is written.
import { useSyncExternalStore } from "react";
export function useLastTrace() {
  return useSyncExternalStore(subscribe, getLastTrace, getLastTrace);
}
