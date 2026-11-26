// Re-export from new location for backward compatibility.
// Relative path on purpose: plain `node` (used by scripts/verify-trace-*.mjs)
// has no awareness of the `@/` tsconfig alias and fails with ERR_MODULE_NOT_FOUND.
export * from "./trace/timeline.mjs";
