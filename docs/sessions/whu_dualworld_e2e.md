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

## Running it through the interactive console (ticket #69)

The console is the acceptance surface for this card. The documented procedure
(HANDOFF §9 L.5 — the console shipped in `scratch/omni_runner/console.py`;
§9 L.4's measured runs are the pre-acceptance evidence):

```bash
cd /Users/chenjinshen/Downloads/Prediction/Omnidroids
./Omnidroids.sh                 # asks "CLI or browser?" → CLI
#   → the console starts, adopts the launcher's backend (never signaled)
#   → new ../武汉大学品牌声誉深度分析报告.pdf 如果武汉大学发布了对肖某某处分的撤销公告，大众舆情走向会怎么样
#   → steps 1-5 run with the live per-step board
#   → step 5: ask the five questions below, one at a time
```

The five step-5 questions, in order (big model, `POST /api/report/chat`):

1. 谁的影响最大？
2. 结果是什么？
3. 海外校友（#DefendWHU）与国内舆论的分歧点是什么？
4. 图谱中连接最多的人物/机构是谁，说明了什么？
5. 如果校方当初没有撤销处分，推演结果会有什么不同？

### The unattended form (a script file, one command per line)

```bash
cat > /tmp/whu_acceptance.cmd <<'EOF'
new "../武汉大学品牌声誉深度分析报告.pdf" 如果武汉大学发布了对肖某某处分的撤销公告，大众舆情走向会怎么样
chat 谁的影响最大？
chat 结果是什么？
chat 海外校友（#DefendWHU）与国内舆论的分歧点是什么？
chat 图谱中连接最多的人物/机构是谁，说明了什么？
chat 如果校方当初没有撤销处分，推演结果会有什么不同？
report
logs agent 20
quit
EOF
./Omnidroids.sh < /tmp/whu_acceptance.cmd     # or: OMNI_INTERFACE=cli ./Omnidroids.sh < ...
```

**Budgets the run needs above** (the console's own defaults already are):
the phase budget is 4 h (the graph build measured 2 h 23 m) and the report's
is 1 h (three sections measured 24–27 min on the big model). Do not lower
them; a lower bound is the runner's `report_timeout`, not a platform failure.

**Done means:** all five questions answered by the report agent (each answer
printed verbatim, with its tool calls), every step's board row `completed`, and
a failing step — if any — carrying the platform's own reason verbatim (never a
generic failure message). Only then is this card's acceptance pass recorded
in `HANDOFF.md` §9 L.5.

**The acceptance rule:** a step that fails is reported with the **platform's own
reason** — the console prints the platform's error line verbatim, never a
generic failure message. The three failures this card already names are
platform facts, not CLI bugs:

- the simulation's agent actions do not land with `small` (§9 J) — the rounds
  still reach 72/72;
- the report's `failed` flag is the #53 word-band mis-measuring CJK prose;
- the agent-interview half of step 5 refuses while the simulation is stopped
  (`/api/simulation/interview` needs a running simulation).

### The state this card's session left behind (measured 2026-11-16)

| Artifact | Id | State |
|---|---|---|
| simulation run state | `sim_598227d656d8` | `runner_status=stopped`, `current_round=72`, `total_rounds=72`, `progress_percent=100.0`, `total_actions_count=20` (the config's initial posts — no LLM action landed, §9 J) |
| simulation log tail | — | the 9B's 502 (`MLX model Ornith-1.5-9B-MLX-6bit has no worker thread`) — the co-residency failure §9 J names |

The `run_state.json` is what the console's session board reads for step 3's
status: the rounds are complete, and the platform's own log is where the
co-residency 502 is to be read.
