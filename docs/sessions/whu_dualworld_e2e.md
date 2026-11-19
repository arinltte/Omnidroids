# E2E test card — the WHU dual-world run

The one test session the platform is currently reduced to. Use it as the acceptance
run for the interactive `omni` CLI (§9 K) and for any agent-driven full-pipeline test.

## The inputs

- **Document**: `Prediction/武汉大学品牌声誉深度分析报告.pdf` (2 893 754 B; 20 862 chars
  extracted; 53 chunks at 500/50; the graph build landed 53/53 in 2 h 22 m 35 s on
  2026-11-15 — the dominant pipeline cost). **That 2 h 22 m is one run of a stochastic
  pipeline, not a floor**: attempt 4 (2026-11-17/18) measured **9 h 04 m** for 51/53 on
  the same document, model and chunking. Budget above the *measured* pace, never below.
- **Requirement**: `如果武汉大学发布了对肖某某处分的撤销公告，大众舆情走向会怎么样`
- **Platforms**: dual-world parallel (twitter + reddit), 101 personas, 72 h / 72 rounds,
  peak hours 19–22, off-peak multiplier 0.05.

## Current state (2026-11-18 — after attempt 4, which restored this state)

**The card is NOT accepted.** Four attempts are measured (see the attempt sections
below): the last one carried steps 1–3 and failed at step 4 on the runner's own 300 s
stall bound while the platform was still advancing to round 12/72. The keeper set
below stands untouched — attempt 4's own project/session/graph/simulation were
deleted through the platform's own DELETE routes and its transcript is kept as
`/tmp/whu_acceptance_run4_20261117_step4_stalled_round6of72.log`. Full status
snapshot for the next session: **HANDOFF §9 L.7**.

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
3. **Env setup** — 101 entities → 101 personas → config; ~38 min (attempt 4 measured
   62.5 min and 155 personas — the entity count is the graph's, not the config's 101
   personas).
4. **Simulation** — 72/72 rounds reach 100 % **regardless of model** (rounds advance on
   the time config). **RETIRED 2026-11-18 (`5e94cb6`, §9 L.8):** the claim "agent
   actions beyond the scripted initial posts do not land with `small`" is false, and
   the cause was this repo's, not the model's — the `/v1` proxy dropped camel's
   `tools`, so no agent action could ever be executed. Measured after the fix, on
   the identical 3-peak-round scenario: **0 → 9 LLM-written actions** (each persona
   writing in its own voice), 285.1 s → 129.9 s. Attempt 4's 36 actions were the old
   path's ceiling; the action counter froze at camel's own 180 s `MODEL_TIMEOUT`
   while a round's calls queue on one MLX worker thread, not at a model failure.
   `big` co-resident still 502s — expected, not a CLI bug.
   **Measured 2026-11-17 (attempt 4):** the rounds **do** advance with `small`, at
   **~12.5 min/round**, and **36 actions land** (18 twitter + 18 reddit, real LLM
   content) before camel's own `openai.APITimeoutError` freezes the counter. The
   "no action ever lands" claim is therefore false as stated; what is true is that
   no run of this length can be watched by the console's **300 s** `--run-stall-seconds`
   default, which fired at round 6/72 while the platform was still advancing (see
   Attempt 4 below). Any acceptance run must pass a stall bound above a round's pace.
   **As of `5e94cb6` the stall bound is pace-derived** (`max(floor, 2.5 × the run's
   own longest inter-advance gap)`, capped 7 200 s) and the floor is
   `--run-stall-seconds`; carry **7200**.
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
- **The runner's own stall bound is the trap that cost attempt 4.** The console's
  `--run-stall-seconds` defaults to 300 s and no WHU round can advance inside it
  (measured ~12.5 min/round), so the run ends at the first slow round with the
  platform still building — the verbatim line is a *runner* verdict
  (`... did not advance for 302s at round 6/72 (8%)`), never a platform failure.
  Pass `--run-stall-seconds` above a round's pace on every WHU acceptance run.
- The graph build's pace is per-run: 2 h 22 m / 8–10 min per chunk / 9 h 04 m are
  all measured on this same document. Read `Graphiti 处理中... N/53 完成` on the
  board for the real remaining time, not a budget you set in advance.

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
**Attempt 4 (2026-11-17/18) measured the build at 9 h 04 m (51 landed / 2
skipped) and the simulation's rounds at ~12.5 min each, so a passing run needs
all three per-run flags — `--phase-timeout 36000 --report-timeout 10800`
**plus** `--run-stall-seconds` above a round's pace (≈3600–7200). Omit the
third and the runner's own stall detector ends the run at the first slow round
while the platform is still building (attempt 4's failure, verbatim: `simulation
sim_d413bad79015 stalled: the platform's own run state did not advance for 302s
at round 6/72 (8%)`).

**Done means:** all five questions answered by the report agent (each answer
printed verbatim, with its tool calls), every step's board row `completed`, and
a failing step — if any — carrying the platform's own reason verbatim (never a
generic failure message). Only then is this card's acceptance pass recorded
in `HANDOFF.md` §9 L.5.

**The acceptance rule:** a step that fails is reported with the **platform's own
reason** — the console prints the platform's error line verbatim, never a
generic failure message. The three failures this card already names are
platform facts, not CLI bugs:

- the simulation's agent actions land with `small` (`36` measured on attempt 4:
  18 twitter + 18 reddit, real LLM content) and then camel's own
  `openai.APITimeoutError` freezes the counter — the rounds still advance on the
  time config, at the measured ~12.5 min/round, so the round wall time is the
  simulation's cost, not the model's (§9 J's "no action ever lands" is superseded
  by attempt 4's measurement). **Fixed at `5e94cb6`:** the proxy now carries
  `tools`/`tool_calls`, and `MODEL_TIMEOUT=1800` must be exported before the
  launcher starts (the console adopts the backend and cannot set its env);
- the report's `failed` flag is the #53 word-band mis-measuring CJK prose;
- the agent-interview half of step 5 (`/api/simulation/interview`) needs a live
  simulation environment. **Changed at `5e94cb6`:** a run that finishes its rounds
  now publishes its terminal status **and keeps its child alive for interviews**
  (`COMPLETED_GRACE_S` = 60 s, `_interview_processes`), so a *completed* run's
  interviews work; only a run that was explicitly **stopped** loses them.

### The state this card's session left behind (measured 2026-11-16)

| Artifact | Id | State |
|---|---|---|
| simulation run state | `sim_598227d656d8` | `runner_status=stopped`, `current_round=72`, `total_rounds=72`, `progress_percent=100.0`, `total_actions_count=20` (the config's initial posts — no LLM action landed, §9 J) |
| simulation log tail | — | the 9B's 502 (`MLX model Ornith-1.5-9B-MLX-6bit has no worker thread`) — the co-residency failure §9 J names |

The `run_state.json` is what the console's session board reads for step 3's
status: the rounds are complete, and the platform's own log is where the
co-residency 502 is to be read.

## The first unattended attempt (2026-11-17) — step 1 failed, the card is not accepted

`OMNI_INTERFACE=cli ./Omnidroids.sh < /tmp/whu.cmd` (this card's unattended form,
the launcher's CLI branch, console `--adopt-backend --port 5001`) started
17:05:05Z and failed at **step 1, the ontology route**. Recorded so the next attempt
does not re-derive it; the full evidence is `logs/backend.log` from that run, the
runner's `scratch/omni_runs/20261116T170505Z-ad12ed/`, and the console transcript
`/tmp/whu_acceptance.log`.

**The platform's own reason, verbatim:**

```
! run error: ontology route returned HTTP 502
LLM returned unusable JSON (finish_reason=length); retrying content generation with
the provider's own output limit (the truncated attempt asked for 4096 tokens; a
provider with no model-specific limit uses MLX_MAX_TOKENS=4096 instead, so if that
is below 4096 raise MLX_MAX_TOKENS)
[01:05:11] WARNING: mlx LFM3-1.7B-MLX-4bit prompt_tokens=17409 exceeds the
operational cap LLM_CONTEXT_LENGTH=8192 but fits the model's 262144-token window
(headroom 244735); raise LLM_CONTEXT_LENGTH or shorten the prompt
[01:08:05] ERROR: LLM returned an unusable ontology response
app.utils.llm_client.LLMResponseError: LLM returned invalid JSON (line 159, column 5)
127.0.0.1 - - [17/Nov/2026 01:08:05] "POST /api/graph/ontology/generate HTTP/1.1" 502 -
```

and the session row the platform projected for it: `sess_d4e9eb6ee95d`,
`status: failed`, `error: LLM returned invalid JSON (line 159, column 5)`.

**The state after it:** the run's own artifacts (its session row and its project
`proj_b32d80a141fb`) were removed through the platform's own routes —
`DELETE /api/sessions/sess_d4e9eb6ee95d?with_project=1` — leaving the machine
exactly as §9 L.3/L.5 describe it: one project `proj_58491d9f98a5`, one session
`sess_984f7cef2192`, one graph `mirofish_115844ea8f7c47a4`, the 72/72 simulation
and its report, plus the untouched historical `sim-*` / `report_*` fixtures. The
launcher's SIGTERM stopped the backend; `:5001` is free. The console printed the
platform's own line and exited — no generic failure message, no re-diagnosis.

## The token budget, measured (2026-11-17)

Before the rerun, the budget question was settled by measurement — the knob named
by the platform's own 502 line (`raise MLX_MAX_TOKENS`) is not the cause:

- `ONTOLOGY_LLM_MAX_TOKENS` / `MLX_MAX_TOKENS`: both **4096**, the platform's own
  default; the owner's `.env` sets neither.
- A complete WHU ontology is **1,552 tokens** (the keeper's, this document) — the
  budget has 2.6× headroom, and the model stops on its own at 1,683 / 3,549 /
  1,739 completion tokens in three probe samples: the cap is never reached.
- Raising both to **8192** and re-running the acceptance failed step 1 again with
  the platform's own reason: `LLM JSON output was truncated at the token limit`.
- The model's failure mode is its own JSON discipline — a stray token inside the
  repetitive `source_targets` blocks (`Expecting ',' delimiter`), which appears
  at 4,096 and at 16,384 alike.
- **Step 1 passes with #48's compact-ontology `additional_context`** (≤1 attribute
  per type, no `examples`, few edge types, short `analysis_summary`): measured
  **1/3 route calls bare vs 3/3 compact** at the platform's own budget; the live
  run completed step 1 in **95.48 s, entity_types=10 edge_types=9**. It only
  shapes the ontology prompt — never the simulation requirement or a later phase.
  Carry it as the console's own `--additional-context`, through the launcher's
  `OMNI_CONSOLE_ARGS` (commit `ae361ec`).

**Step 2's pace, measured on this run** (the platform's own task message):
`Graphiti 处理中... 11/53 完成, 42 待处理 (5264秒)` = **8.0 min/chunk** against the
card's own 2.7 min/chunk basis (53/53 in 2 h 22 m 35 s). Cost structure from the
platform's own log: ~22.6 generate calls per chunk, median inter-call gap 15 s;
168 nodes / 180 edges at 11 chunks = 15.3 nodes per chunk against the keeper
graph's 8.2. The two ontologies are structurally identical (10 entity / 9 edge
types, 1 source_target each, 0.74 vs 1.21 attributes per type), so this run's
ontology is leaner, not heavier — its entity-type set is simply junkier
(`CourtCase`, `PersonEvent`, `PersonGroup`), which plausibly costs extraction
calls. Projection at the measured pace: 53/53 at ~02:43Z, i.e. ~7 h — the
console's 4 h phase budget (calibrated on 2.7 min/chunk) fires at ~23:41Z at
~29/53 with the platform still building.

## Attempt 3 (2026-11-17) — step 1 passed, step 2 hit the runner's 4 h bound at 26/53

`OMNI_CONSOLE_ARGS='--additional-context <compact-ontology>' OMNI_INTERFACE=cli
./Omnidroids.sh < /tmp/whu.cmd` (run `20261116T193929Z-6dceec`, transcript
`/tmp/whu_acceptance.log`):

* **Step 1 — completed, 95.48 s**: `entity_types=10 edge_types=9`, the run's own
  block and board row agree.
* **Step 2 — failed, the runner's own verdict, verbatim**:
  `graph build c9bd7cae-54df-47d8-a60e-c9d445736886 did not reach a terminal
  state within 14400s` — with the platform's own record at that moment:
  **26 landed · 0 skipped · 27 never_run** (the run's `events.jsonl`, and the
  platform's own `Graphiti 处理中... 26/53 完成, 27 待处理`). The session row:
  `interrupted before completion; resume continues from the last checkpointed
  chunk`.
* The platform kept building after the runner stopped watching (§9 L.5 rule 1,
  at step 2 instead of step 5). The bound was calibrated on 2.7 min/chunk; this
  run measured **8.0–9.9 min/chunk** start to finish — cause not established
  (the graphs' node/edge rates explain ~1.16× of it).
* The transcript's live tally `landed=52` is a console defect the run itself
  measured (double-counted per-chunk notes), fixed in
  `scratch/omni_runner/console.py`; the contract's single-count semantics is now
  pinned again.
* Cleanup and machine state: the run's session/project deleted through the
  platform's own routes, its orphan graph directory removed from disk, SIGTERM
  only, `:5001` free — the one-project / one-session / one-graph state stands.
* **The next attempt is not accepted yet.** A budget above the measured pace is
  needed — per-run flags, the console's defaults untouched:
  `--phase-timeout 36000 --report-timeout 10800`, plus the compact-ontology
  `--additional-context` that carries step 1.

## Attempt 4 (2026-11-17/18) — steps 1–3 completed, step 4 failed on the runner's own stall bound — **not accepted**

`NEXT_SESSION_PROMPT.md` option 1, exactly: the compact-ontology
`--additional-context` + `--phase-timeout 36000 --report-timeout 10800`
(run `20261117T034842Z-9a41fb`, transcript `/tmp/whu_acceptance.log`). Wall
**10 h 17 m**, `exit_code: 1`. Full record: **HANDOFF §9 L.5.5**.

**The board rows, verbatim:**

```
  1 document + ontology: state=completed detail=entity_types=10 edge_types=8 text_chars=20862 duration_s=38.90
  2 graph build: state=completed detail=chunks_landed=51 chunks_skipped=2 nodes=594 edges=978 duration_s=32649.76
  3 environment setup: state=completed detail=stage=generating_config progress=76 duration_s=3753.80
  4 simulation: state=failed detail=round 6/72 progress=8.3% actions=None status=stalled duration_s=589.84
  5 report + chat: state=pending detail=-
```

* **Steps 1–3 all passed**, on the flags the owner prescribed: ontology 38.90 s;
  the platform's own batch record 51 succeeded / 2 skipped / 0 never_run
  (`skip_fraction: 0.0377`, 594 nodes / 978 edges, terminal
  `task_status=completed … 图谱构建完成`) in 9 h 04 m; env setup 62.5 min,
  `prepare_profiles_count: 155`, `prepare_status: ready`.
* **Step 4 failed on the runner's own bound, not the platform's.** Verbatim:
  `! run error: simulation sim_d413bad79015 stalled: the platform's own run state
  did not advance for 302s at round 6/72 (8%)` — the console's default
  `--run-stall-seconds 300`, which the prescribed command did not raise. The
  platform's own `run_state.json` after the runner stopped watching reads
  `runner_status: running`, `current_round: 12`, `progress_percent: 16.7`,
  `error: None` — **6 → 12 rounds in the following ~75 minutes**, so the state
  was advancing the whole time.
* **Step 5 never ran and the five questions therefore have no report.** The
  first `chat` returned the platform's own route error verbatim
  (`omni chat: /api/report/chat -> HTTP 500: 'name'`); the other four returned
  the report agent's own "no report yet" answer. No acceptance answer exists
  from this attempt.
* **Actions land with `small` on this document: 36** (18 twitter + 18 reddit,
  real LLM-written content), then camel's own `openai.APITimeoutError` freezes
  the counter — see the corrected expectation under "Expected per-step outcomes"
  item 4.
* Cleanup and machine state: session `sess_80e3b2adae91` + project
  `proj_d99b6bc1c14f` deleted through the platform's own
  `DELETE /api/sessions/<id>?with_project=1`; the orphan graph
  `mirofish_24967d32ed884f04` and the orphan
  `uploads/simulations/sim_d413bad79015/` removed from disk; SIGTERM only
  (the launcher's own cleanup plus a second SIGTERM to the draining backend,
  never escalated); one project / one session / one graph / the 72/72
  simulation and its report; `:5001` free; Ollama clear on both sides.

**Still not accepted.** The next attempt is the same command **plus**
`--run-stall-seconds` above a round's measured pace (~12.5 min/round), e.g.
`--run-stall-seconds 7200`. Whether to spend the ~14 h the remaining 66 rounds
now measure — with the graph build's 9 h as the irreducibly expensive half —
is the owner's call.

## The performance question, answered from this repo's measurements (2026-11-17)

The owner asked whether the run can be shortened. The honest answer, from
measurements already in the handoff:

* **Graph build — the dominant cost — cannot be shortened by swapping the model.**
  Every substitution is measured and rejected: the 9B on graphiti's prompts
  (5/8 chunks, schema-echo skips, sparse graph — session 14); the 8-bit `small`
  (failed the 0.40 skip guard at 32 %); MiniCPM5-2B (not a better `small`);
  chunk 1500 (rejected). `GRAPHITI_MAX_COROUTINES` cannot help: the MLX runtime
  serves one model on one worker thread. The floor is the same small model: the
  best measured build is **53/53 in 2 h 22 m 35 s (2.7 min/chunk)**; this session
  measured **8.0–9.9 min/chunk** on the same document — a 3× spread whose cause is
  not established. Plan the budget above the *measured* pace, never below it.
* **Simulation — agent actions do not land locally, and no knob changes that.**
  `small` rambles instead of tool-calling, the rounds reach 72/72 on the time
  config regardless, `big` 502s co-resident with the sim stack on this 16 GB
  machine. A fully local agent-driven simulation is out of reach with the two
  keepers; the local run is scripted-initial-posts + time-driven rounds.
* **Total at the measured floor: ~4 h 15 m – 5 h** (ontology minutes · build 2 h 23 m
  · env setup ~38 min · simulation ~36–54 min · report ~27 min). Anything longer is
  the build's per-run variance, not the pipeline.

The retro that followed is recorded in HANDOFF §9 L.6, and the next session's
instructions are `NEXT_SESSION_PROMPT.md`.

## Attempt 5 (2026-11-18/19) — **MEASURED: ran end to end; the watched step-2 bound failed, the platform completed everything anyway; step 5 answered 5/5**

Branch `feat/69-omni-interactive-console` @ `5e94cb6` (§9 L.8). The full record is
**HANDOFF §9 L.10**; this section carries the card-side facts.

**The command, exactly as run** (the `<compact ontology>` placeholder is now the
literal string — do not retype it, it carries no spaces):

```bash
MODEL_TIMEOUT=1800 \
OMNI_CONSOLE_ARGS='--additional-context 请输出紧凑本体：每个实体类型最多1个属性，不要examples字段，关系类型固定为6个（如WORKS_FOR、COMMENTS_ON、SUPPORTS、OPPOSES、REPORTS_ON、AFFILIATED_WITH），analysis_summary用一两句话概括。 --phase-timeout 36000 --report-timeout 10800 --run-stall-seconds 7200' \
OMNI_INTERFACE=cli nohup ./Omnidroids.sh < /tmp/whu.cmd
```

**The measured board** (the platform's own numbers; the runner's verdicts verbatim):

| Step | Verdict | Measured |
|---|---|---|
| 1 document + ontology | completed | 135.1 s, `entity_types=10 edge_types=9`, text 20862 chars |
| 2 graph build | **failed — the runner's own 36000 s bound**, verbatim: `graph build ab24923c-15c9-4235-8f7a-259f34e28ccb did not reach a terminal state within 36000s` (platform at 49/53) | the platform **completed on its own: 53/53 landed, 0 skipped, 624 nodes / 1105 edges**, graph `mirofish_dc45666f908a47c6` (10 h 23 m at a 2.7–13.5 min/chunk pace) |
| 3 environment setup | (drove via the platform's own routes — the console cannot resume an interrupted session) | ~60 min, 163 personas, `ready` |
| 4 simulation | (same) | **`completed`, 72/72 rounds, both platforms 72/72, 497 actions** (twitter 382 + reddit 115), `error: null`; 9 h 49 m |
| 5 report + chat | (same) | 3 failed attempts (Metal OOM ×2, 9B eviction ×1) then **written** — 3 sections + 17.6 KB, `failed` **only** by the #53 CJK band (`Report body is 100 words, outside the configured band 250-350`); **the five questions answered 5/5**, verbatim in `scratch/whu_phase2_evidence/q1..q5.txt`, each citing this run's own graph/report content, on `Ornith-1.5-9B-MLX-6bit` (73/92/103/66/88 s) |
| interview | (same) | **verified on the completed-not-stopped run for the first time** — HTTP 200, 105.9 s, in-persona answer citing the run's own facts |

**Settled by this attempt:** the action ceiling is **497, not 36** — the
`5e94cb6` tool-calling seam works end to end (`max_tokens=512` in the runtime log);
a 72-round dual-world run is ~10 h; the report's co-residency ceiling needs the
sim child released (the platform's own stop, which preserves the run's result);
and the cheap step-5 check (L.9's reader commands) reads right — the Q3 chat defect
it found (`KeyError: 'name'`, empty `success: true`) is **latent, not fixed**.

**Not recorded accepted:** the watched row 2 carries the runner's own bound, and
steps 3–5 ran outside the console. A watched pass needs `--phase-timeout` above
this document's slow-end build (≥43200) and a console resume path (#69). The five
verbatim answers and the interview evidence are the acceptance payload.
