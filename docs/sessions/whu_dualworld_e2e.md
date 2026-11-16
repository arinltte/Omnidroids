# E2E test card — the WHU dual-world run

The one test session the platform is currently reduced to. Use it as the acceptance
run for the interactive `omni` CLI (§9 K) and for any agent-driven full-pipeline test.

## The inputs

- **Document**: `Prediction/武汉大学品牌声誉深度分析报告.pdf` (2 893 754 B; 20 862 chars
  extracted; 53 chunks at 500/50; the graph build landed 53/53 in 2 h 22 m 35 s on
  2026-11-15 — the dominant pipeline cost).
- **Requirement**: `如果武汉大学发布了对肖某某处分的撤销公告，大众舆情走向会怎么样`
- **Platforms**: dual-world parallel (twitter + reddit), 101 personas, 72 h / 72 rounds,
  peak hours 19–22, off-peak multiplier 0.05.

## Current state (2026-11-16)

| Artifact | Id | State |
|---|---|---|
| project | `proj_58491d9f98a5` | graph_completed |
| graph | `mirofish_115844ea8f7c47a4` | 1 495 nodes, healthy (the only graph left in `backend/data/graphiti/`) |
| simulation | `sim_598227d656d8` | **72/72 rounds, stopped, 100 %** |
| report | `report_31b8e804870b` | written — outline + 3 sections + 19.9 KB markdown; task flagged `failed` **only** by the CJK word band |
| session | `sess_984f7cef2192` | stage `report`, `meta.report_id` set |

## Expected per-step outcomes for a fresh CLI-driven run

1. **Ontology** — 9 entity types, minutes.
2. **Graph build** — 53/53 landed, 0 skipped (skip guard 0.40); ~2 h 23 m; watch the
   per-chunk landed/skipped split (`omni log --phase graph_build`).
3. **Env setup** — 101 entities → 101 personas → config; ~38 min.
4. **Simulation** — 72/72 rounds reach 100 % **regardless of model** (rounds advance on
   the time config). Agent actions beyond the scripted initial posts do **not** land with
   `small` (rambles) and the run 502s with `big` co-resident — expected, not a CLI bug.
   The interview tool inside the report agent warns `模拟环境未运行` — by design.
5. **Report + chat** — the report agent writes the three graph-grounded sections on the
   big model; the final `failed` flag is the #53 band on CJK prose (whitespace-split
   counter; content is complete). The **chat works**:
   `POST /api/report/chat {"simulation_id": ..., "message": ...}`.

## The five step-5 test questions (big model)

1. 谁的影响最大？
2. 结果是什么？
3. 海外校友（#DefendWHU）与国内舆论的分歧点是什么？
4. 图谱中连接最多的人物/机构是谁，说明了什么？
5. 如果校方当初没有撤销处分，推演结果会有什么不同？

## Known traps (measured — see §9 J/K)

- Node-detail 500s (`Mmap for size 8796093022208`) meant an unreadable **orphan graph**
  was being scanned by `_read_node(None)` — the graphs are now cleaned; if it returns,
  check `backend/data/graphiti/` for strays before suspecting the live graph.
- The report band (`REPORT_WORD_COUNT_MIN/MAX`, 250–350) is calibrated on English; a
  complete Chinese body measures ~104 "words". Owner config, untouched.
