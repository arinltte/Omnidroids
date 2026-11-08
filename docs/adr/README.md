# ADRs

Architecture decision records for Omnidroids. Numbering: `NNNN-kebab-title.md`,
zero-padded, strictly increasing, never reused or renumbered — a superseded ADR
keeps its file and number and gains a `Superseded-by:` line.

| ADR | Status | Decision |
|---|---|---|
| [0001 — Graph Build backend: keep Graphiti + embedded Kuzu](0001-graph-build-backend.md) | Accepted | Graph Build stays on Graphiti + embedded Kuzu behind the Zep-compatible seam; the bake-off's incumbent defect is the outgoing small model's JSON-clamp class (#45), not the backend |
