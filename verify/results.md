# Verification results

Instance: AuraDB Free. Graph built from 5 repos. Date: ____

This file is the completed-by-a-human deliverable; `verify/run_arm.py`
produces the raw per-run data (`verify/results.csv`, `verify/runs/*.json`)
this table summarizes. Judging correctness and filling in the blanks below
is explicitly **not** automated - see "How to run".

## Why replicate (read before filling in the table)

Each question runs **at least twice per arm** (3 recommended). A single
pass per arm cannot separate a real control-vs-treatment difference from
noise: Claude models flip their answer to the same prompt at temperature 0
roughly 9% of the time per instance (arXiv 2607.09691). Report the **median**
token count per arm per question, *and* the spread across runs, not a single
number. A wrong answer in **any** run counts against that arm for that
question - more replication makes a clean accept harder to get, not easier,
which is the right direction for a gate like this one. Even 2-3 runs is a
thin sample against a ~9% flip rate; treat the verdict below as indicative,
not proof, and say so again in the verdict section rather than overstating
it.

## The structural blind spot (read before filling in Q9/Q10/Q12)

An import/call graph is structurally blind to **dependency injection,
registries, and configuration-driven wiring**: any coupling decided at
runtime by a parameter, a config value, a dynamic `getattr`, or a framework's
own container, rather than by a name a parser can resolve while reading the
source. That is the dominant coupling style in the kind of enterprise
(e.g. Spring-style) codebase this POC is meant to inform a decision about.
Questions 9 and 10 in `questions.md` exist to check that the graph *knows*
it is blind there instead of silently fabricating a plausible edge; Question
12 checks the same thing across a language boundary (TypeScript/Python, no
possible static edge at all). A framework-aware extractor that resolves DI
containers or config-driven registries into real edges is out of scope for
this POC - it is the named gap this POC's result should be read against, not
something this POC attempts to close.

## What "tokens" means here (read before filling in the table)

The comparison figure is **`context_tokens`** from `results.csv`:

    context_tokens = input_tokens + cache_creation_tokens + cache_read_tokens

With prompt caching, nearly everything the model reads - system prompt, tool
schemas, file contents, tool results - arrives as `cache_creation_input_tokens`
or `cache_read_input_tokens`, **not** `input_tokens`. The one real sample this
harness was built against (`verify/schema-sample.jsonl`) has `input_tokens =
10` against `cache_creation_input_tokens = 25,641`: "input + output" would
have reported 73 tokens for a 25,714-token turn and omitted ~99% of the
context. `context_tokens` is what the model actually had to read, which is
the quantity the POC's premise ("the graph lets the agent read less") is
about. The three components are kept as separate CSV columns so the split
can be inspected.

Show **`total_cost_usd`** beside it. Cache reads are billed at a fraction of
fresh input, so cost and context can rank the arms differently - a treatment
run that re-reads a big cached tool schema many times is cheap but not small.
Both numbers are in every CSV row; report both, compare on `context_tokens`.

## Results

**Third arm, `kgc`** (added 2026-10-04): the bespoke server from
`docs/specs/2026-10-04-kgc-bespoke-mcp-design.md`, same twelve questions, same
metrics, tool calls tallied as `kgc_calls`. Acceptance: `kgc` matches or beats
`treatment` on correct answers while using no more `context_tokens`, and the
fraction of runs with at least one graph-tool call is reported alongside. The
`treatment` baseline must exist before `kgc` is judged.

Populate from `verify/results.csv` (one row per run) after running the
experiment. "tokens" below means `context_tokens` (see above); "cost" is
`total_cost_usd`. "correct (all runs)" is No if even one run for that arm
was wrong or confidently misleading - see the acceptance rule below. A run
with `permission_denials > 0` is **invalid** and must not be entered here;
re-run it (see "How to run", step 3). A run with `contamination > 0` is
entered but flagged in Notes.

| # | Class | Control tokens (r1, r2, …) | Control median | Treatment tokens (r1, r2, …) | Treatment median | Median ratio (T / C) | Median cost T / C (USD) | Control correct (all runs) | Treatment correct (all runs) | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | cross-repo | | | | | | | | | |
| 2 | cross-repo | | | | | | | | | |
| 3 | cross-repo | | | | | | | | | |
| 4 | cross-repo | | | | | | | | | |
| 5 | cross-repo | | | | | | | | | |
| 6 | single-repo | | | | | | | | | expected treatment loss is informative, not a failure |
| 7 | single-repo | | | | | | | | | expected treatment loss is informative, not a failure |
| 8 | single-repo | | | | | | | | | expected treatment loss is informative, not a failure |
| 9 | hidden-dependency | | | | | | | | | treatment must hedge/decline, never assert a concrete wrong answer |
| 10 | hidden-dependency | | | | | | | | | treatment must hedge/decline, never assert a concrete wrong answer |
| 11 | blind-spot | | | | | | | | | treatment must name the extractor gap, not just report "zero nodes" |
| 12 | blind-spot | | | | | | | | | treatment must name the cross-language gap, not stay silent or fabricate |

## Verdict

- Median `context_tokens` ratio (treatment vs control), all 12 questions: ____
- Median `context_tokens` ratio, cross-repo/impact questions only (1-5): ____
- Median `context_tokens` ratio, single-repo grep-favourable questions only (6-8): ____
- Median `total_cost_usd` ratio (treatment vs control), all 12 questions: ____
- Questions where treatment was **wrong** (any run): ____
- Questions where treatment was **confidently wrong** (stated a concrete,
  incorrect answer rather than hedging) on a hidden-dependency or
  blind-spot question: ____ — any non-empty answer here is an automatic
  reject, regardless of the token ratio.
- Questions only treatment could answer (control ran out of budget, turns,
  or gave up): ____
- Blind spots correctly identified (Q9, Q10, Q11, Q12): ____
- Runs discarded for `permission_denials > 0` and re-run with an allow-list: ____
- Runs with `contamination > 0` (a built-in tool touched a `graphify-out/`
  path) and how they were treated: ____
- Replication note: with only 2-3 runs per arm against a ~9% per-instance
  flip rate, this verdict is indicative, not proof. Say explicitly whether
  any run disagreed with its siblings for the same question/arm, and how
  that was resolved (majority, or flagged as unstable).

**Accept or reject?** The POC succeeds only if treatment is materially
cheaper **and** no less correct. A good median hiding one confidently wrong
answer is a failure, because the failure mode of a stale or
extraction-blind graph is *plausibility* - it does not look wrong until
checked against the code. Fill in: ____

## Which MCP server answered

Fed by `graphify_calls` / `neo4j_calls` in `results.csv` (tallied by
`run_arm.py` from `mcp__graphify__*` / `mcp__neo4j__*` tool-use blocks in the
treatment transcripts - control never has these columns populated since its
MCP config is empty).

| # | graphify calls (median) | neo4j calls (median) | Which server actually answered |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |
| 11 | | | |
| 12 | | | |

If `neo4j` (Cypher) was never actually needed - `graphify`'s own
`query_graph`/`get_neighbors`/`shortest_path` sufficed everywhere - that is a
useful negative result: it simplifies the eventual work build to one server
instead of two. Say so explicitly if that is what the tally shows (see open
question 1 in the spec).

## How to run

This harness was built, not run - see the task division of labour: running
the experiment spends real tokens and judging correctness needs a human
reading the code. In order:

```bash
.venv/bin/kgc push                                       # once after Plan 1 lands: creates kgc_text + Repo nodes
.venv/bin/python setup_mcp.py --out verify/treatment-mcp.json
.venv/bin/kgc init --out verify/kgc-mcp.json
.venv/bin/python verify/run_arm.py --questions 1 --runs 1 --arm kgc   # pre-flight: isolation_ok, kgc_calls > 0
```

1. **Clear stale per-repo graphs.** Earlier `kgc build`s wrote
   `<repo>/graphify-out/` into each of the five target repos; the current
   build writes to `~/.graphify/repos/<tag>/graphify-out/` instead and
   touches nothing in the repos. Delete any `graphify-out/` that remains in
   a target repo (`git -C <repo> status --short` lists it as untracked; it
   holds nothing `kgc` needs). Reason: the control arm runs with its cwd in
   that repo, and the user's global graphify skill says to treat any
   question as a graphify query when `graphify-out/` exists - a control run
   reading a ready-made graph is not a control run. The `contamination`
   column (below) catches it after the fact; this step prevents it.
2. `.venv/bin/python verify/run_arm.py --dry-run` - prints every command it
   would run (48 by default: 12 questions × 2 arms × 2 runs) and writes
   nothing. Read a few of the printed commands and sanity-check the cwd and
   flags before spending anything for real. What you should see:
   - **both arms:** `--disallowedTools Skill --disable-slash-commands`, so
     no user skill (in particular the graphify skill) can take part;
   - **treatment:** cwd is this project; `--mcp-config <project>/.mcp.json
     --strict-mcp-config`, so `graphify` and `neo4j` are the only MCP
     servers in the session (without `--strict-mcp-config` every server in
     the user's global config would load too: a tool-schema token tax on
     treatment, and untallied servers that could answer the question);
   - **control:** cwd is the question's repo; `--mcp-config
     verify/empty-mcp.json --strict-mcp-config`, so no MCP server at all.
3. **Pre-flight one treatment run before the batch:**
   `.venv/bin/python verify/run_arm.py --questions 1 --runs 1 --arm treatment`.
   Then open `verify/runs/treatment-q01-r1.json` and confirm all three of:
   `graphify_calls > 0` (the agent actually used the graph),
   `permission_denials == 0`, and `init.mcp_servers` names exactly
   `graphify` and `neo4j`, both `connected`. If `permission_denials > 0`,
   the agent *asked* for a tool and headless mode said no - in the answer
   text that is indistinguishable from the agent choosing not to use the
   graph, so such a run is invalid and must be re-run with an allow-list:
   `--allowed-tools "mcp__graphify mcp__neo4j Read Grep Glob"` for
   treatment (an MCP server's tools are allowed by server name; Claude Code's
   MCP permission rules take no wildcard, so `mcp__graphify__*` is not a
   rule) and `--allowed-tools "Read Grep Glob"` for control. Prefer that to
   a broad `--permission-mode`. Delete the invalid `verify/runs/<label>.json`
   (or pass `--force`) before re-running.
4. `.venv/bin/python verify/run_arm.py --runs 2` (or `--runs 3` for a less
   thin sample) - actually runs everything, writing `verify/runs/<arm>-q<NN>
   -r<K>.json` per run and a row in `verify/results.csv`. Narrow with
   `--questions 1,5,9` or `--arm treatment` while iterating; `--model` to
   change the model (default `sonnet`, cheaper for ~48 runs than the
   session default). `--runs` below 1 is rejected.
   - **Per-run JSON.** Besides `answer` and the raw `result_event`, each
     `verify/runs/<label>.json` records `context_tokens`,
     `cache_creation_tokens`, `cache_read_tokens`, `permission_denials`,
     `contamination`, `malformed_lines`, and `init` - the session's
     `cwd`, `mcp_servers`, `skills`, `memory_paths`, `permissionMode` and
     `model` as reported by Claude Code's init event - so the isolation of
     every run can be checked afterwards, not assumed.
   - **`permission_denials`.** Any run with a non-zero count is invalid (see
     step 3). Do not enter it in the table; re-run it with an allow-list.
   - **`contamination`.** The number of built-in `Read`/`Grep`/`Glob`/`Bash`
     tool calls whose input mentioned `graphify-out`. Non-zero in a control
     run means the agent found a per-repo graph on disk (step 1 was
     skipped); non-zero in treatment means it read a graph file instead of
     querying the servers. Flag such runs in Notes.
   - **Per-run isolation.** A single run that hangs, exits nonzero, or
     produces unparseable output does not abort the batch: the loop moves
     on to the next run and writes `verify/runs/<label>.error.json`
     (`run_label`, `exception_type`, `exception_message`, and a
     `stderr_tail` when one was captured) instead of the usual
     `<label>.json`. `run_arm.py` exits 1 if anything failed and prints the
     list of failed labels, even though every run that *did* succeed is
     already on disk. A run that hangs for longer than `--timeout` seconds
     (default 900) is killed and counted as a failure the same way -
     e.g. because it is stuck on a permission prompt no human is there to
     answer; lower it with `--timeout 120` while iterating, or raise it for
     a genuinely slow question. The timeout kills `claude` itself; the MCP
     servers it spawned (`uvx mcp-neo4j-cypher`, `python -m graphify.serve`)
     are separate processes that exit once their stdio closes - check `ps`
     after a timed-out treatment run and kill any that linger.
   - **Resuming.** Re-running the same command after a partial or failed
     batch does not repeat work: any run whose `verify/runs/<label>.json`
     already exists is printed as `skip (exists)` and left alone - this
     applies to `--dry-run` too, so a resumed dry run shows accurately what
     would still happen. Pass `--force` to re-run everything regardless
     (e.g. after fixing something that could have produced a bad answer).
     A re-run **replaces** that label's row in `results.csv` - the file
     always holds one row per (question, arm, run), the latest - and
     removes the label's `.error.json` if one was left by an earlier
     failure. An `.error.json` from a previous failed attempt does **not**
     block a retry - only a completed `.json` does, so simply re-running
     without `--force` naturally retries anything that failed last time.
   - **`malformed_lines`.** Both `verify/runs/<label>.json` and the matching
     `results.csv` row carry a `malformed_lines` count - how many lines of
     that run's raw stream-json output failed to parse as JSON and were
     skipped (truncated output, a stray stderr line that leaked onto
     stdout, ...). The run still completed as long as a `result` event was
     found; a nonzero count here is a flag to look at that run's `.json`
     more carefully, not itself a failure. A run with no `result` event at
     all is a different thing entirely - that raises and produces an
     `.error.json` instead, per the isolation note above.
5. Judge correctness: for every completed run file - `verify/runs/<arm>-q<NN>-r<K>.json`,
   **not** the `*.error.json` records, which hold no answer - read the
   `answer` field against the real code (the file paths named in
   `questions.md` are a starting point, not the full answer) and fill in the
   Control correct / Treatment correct columns above. This is the one step
   that cannot be automated - it is why this task's output is a harness, not
   a result.
6. Fill in the Verdict and "Which MCP server answered" sections from
   `verify/results.csv` plus your correctness judgments, and make the
   accept/reject call using the rule above: treatment must be materially
   cheaper **and** no less correct across every run, not just on average.
