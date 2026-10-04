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

## Results

Populate from `verify/results.csv` (one row per run) after running the
experiment. "tokens" per run = `input_tokens + output_tokens` from that row;
`cache_read_tokens` is recorded in the CSV separately and is deliberately
**not** folded into this figure, since cached input is billed at a fraction
of fresh input and mixing them in would distort the ratio. "correct (all
runs)" is No if even one run for that arm was wrong or confidently
misleading - see the acceptance rule below.

| # | Class | Control tokens (r1, r2, …) | Control median | Treatment tokens (r1, r2, …) | Treatment median | Median ratio (T / C) | Control correct (all runs) | Treatment correct (all runs) | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | cross-repo | | | | | | | | |
| 2 | cross-repo | | | | | | | | |
| 3 | cross-repo | | | | | | | | |
| 4 | cross-repo | | | | | | | | |
| 5 | cross-repo | | | | | | | | |
| 6 | single-repo | | | | | | | | expected treatment loss is informative, not a failure |
| 7 | single-repo | | | | | | | | expected treatment loss is informative, not a failure |
| 8 | single-repo | | | | | | | | expected treatment loss is informative, not a failure |
| 9 | hidden-dependency | | | | | | | | treatment must hedge/decline, never assert a concrete wrong answer |
| 10 | hidden-dependency | | | | | | | | treatment must hedge/decline, never assert a concrete wrong answer |
| 11 | blind-spot | | | | | | | | treatment must name the extractor gap, not just report "zero nodes" |
| 12 | blind-spot | | | | | | | | treatment must name the cross-language gap, not stay silent or fabricate |

## Verdict

- Median token ratio (treatment vs control), all 12 questions: ____
- Median token ratio, cross-repo/impact questions only (1-5): ____
- Median token ratio, single-repo grep-favourable questions only (6-8): ____
- Questions where treatment was **wrong** (any run): ____
- Questions where treatment was **confidently wrong** (stated a concrete,
  incorrect answer rather than hedging) on a hidden-dependency or
  blind-spot question: ____ — any non-empty answer here is an automatic
  reject, regardless of the token ratio.
- Questions only treatment could answer (control ran out of budget, turns,
  or gave up): ____
- Blind spots correctly identified (Q9, Q10, Q11, Q12): ____
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

1. `.venv/bin/python verify/run_arm.py --dry-run` - prints every command it
   would run (48 by default: 12 questions × 2 arms × 2 runs) and writes
   nothing. Read a few of the printed commands and sanity-check the cwd and
   flags before spending anything for real.
2. `.venv/bin/python verify/run_arm.py --runs 2` (or `--runs 3` for a less
   thin sample) - actually runs everything, writing `verify/runs/<arm>-q<NN>
   -r<K>.json` per run and appending to `verify/results.csv`. Narrow with
   `--questions 1,5,9` or `--arm treatment` while iterating; `--model` to
   change the model (default `sonnet`, cheaper for ~48 runs than the
   session default).
   - If a run hangs or silently fails on a tool permission prompt (headless
     mode has no human to answer one), re-run with
     `--permission-mode acceptEdits`, or better, pass `--allowed-tools`
     scoped to exactly what that question needs (e.g.
     `--allowed-tools "Read Grep Glob"` for a read-only question) rather than
     widening permissions globally.
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
     a genuinely slow question.
   - **Resuming.** Re-running the same command after a partial or failed
     batch does not repeat work: any run whose `verify/runs/<label>.json`
     already exists is printed as `skip (exists)` and left alone - this
     applies to `--dry-run` too, so a resumed dry run shows accurately what
     would still happen. Pass `--force` to re-run everything regardless
     (e.g. after fixing something that could have produced a bad answer).
     An `.error.json` from a previous failed attempt does **not** block a
     retry - only a completed `.json` does, so simply re-running without
     `--force` naturally retries anything that failed last time.
   - **`malformed_lines`.** Both `verify/runs/<label>.json` and the matching
     `results.csv` row carry a `malformed_lines` count - how many lines of
     that run's raw stream-json output failed to parse as JSON and were
     skipped (truncated output, a stray stderr line that leaked onto
     stdout, ...). The run still completed as long as a `result` event was
     found; a nonzero count here is a flag to look at that run's `.json`
     more carefully, not itself a failure. A run with no `result` event at
     all is a different thing entirely - that raises and produces an
     `.error.json` instead, per the isolation note above.
3. Judge correctness: for every file under `verify/runs/*.json`, read the
   `answer` field against the real code (the file paths named in
   `questions.md` are a starting point, not the full answer) and fill in the
   Control correct / Treatment correct columns above. This is the one step
   that cannot be automated - it is why this task's output is a harness, not
   a result.
4. Fill in the Verdict and "Which MCP server answered" sections from
   `verify/results.csv` plus your correctness judgments, and make the
   accept/reject call using the rule above: treatment must be materially
   cheaper **and** no less correct across every run, not just on average.
