# Third-Party Notices

This project is a derivative of MiroFish (AGPL-3.0) and combines components under the licenses
below. This file records the obligations that follow from using them.

| Component | License | Used for | Notes |
|---|---|---|---|
| MiroFish | AGPL-3.0 | Application base (backend services, Vue frontend, OASIS simulation wiring) | Derivative works must remain AGPL-3.0 with source availability (including over a network). |
| Graphiti | Apache-2.0 | Entity/edge extraction, graph memory | Vendored copy in the reference workspace; interim graph backend (see `HANDOFF.md` §3.2 bake-off). |
| Kuzu | MIT | Embedded graph store | Bundled as a Python dependency. |
| Cognee | Apache-2.0 | **Evaluation candidate** graph-memory backend | Referenced during the phase-3 bake-off; becomes a declared dependency only if it wins. |
| Semantica | MIT | **Evaluation candidate** graph-memory backend | Same evaluation status as Cognee. |
| MLX / mlx-lm (mlx-examples) | MIT | macOS inference backend (small + big model runners) | In-process, load-on-demand. |
| llama.cpp / llama-cpp-python | MIT | Cross-platform GGUF inference backend | Used on Windows and for GGUF weights generally. |
| huggingface_hub | Apache-2.0 | Model repo downloads | Downloads land inside `Omnidroids/models/` (HF_HOME redirected here). |
| Flask | BSD-3-Clause | HTTP API + static SPA serving | |
| Vue.js + UI kit | MIT | Frontend SPA | |
| OASIS (simulation runner, from MiroFish) | per upstream | Multi-agent persona simulation | Inherited from MiroFish; verify its upstream license file and record it here at Phase 0 completion. |

**Rules for contributors/agents:**
1. Do not strip copyright headers from copied files.
2. New files carry `SPDX-License-Identifier: AGPL-3.0-or-later`.
3. Downloaded model weights are NOT licensed for redistribution by this file — they follow
   their own HuggingFace repo licenses and are never committed to this repository.
4. Update this table whenever a dependency or candidate is promoted to a real dependency.
