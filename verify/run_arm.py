#!/usr/bin/env python3
"""Headless runner for the Task 7 verification experiment (see questions.md).

This script automates **token capture only** - it builds and (optionally)
executes `claude -p ...` invocations for the control and treatment arms and
records tokens/cost/duration/tool-tallies to `verify/results.csv` and one
`verify/runs/<arm>-q<NN>-r<K>.json` per run. It does **not** judge
correctness - that needs a human reading each answer against the actual
code (see results.md, "How to run"). Running the experiment also spends the
user's tokens, which is why this script, unlike the harness around it, is
meant to be invoked by a human, not by an agent.

Arms
----
- **treatment**: cwd is this project (`PROJECT_ROOT`), so `CLAUDE.md` loads
  normally, and `--mcp-config <PROJECT_ROOT>/.mcp.json --strict-mcp-config`
  is passed so the `graphify` + `neo4j` servers are the ONLY MCP servers in
  the session. Without `--strict-mcp-config` every server in the user's
  global config loads too - a tool-schema token tax on treatment, and
  untallied servers (e.g. another Neo4j MCP) that could answer the question.
- **control**: cwd is the question's primary repo (`repo_path_map()[q.repo]`),
  and an empty `--mcp-config` + `--strict-mcp-config` is passed so no MCP
  server can leak in even if one happens to be configured globally. For a
  `cross-repo` or `blind-spot` question only (`PREAMBLE_CLASSES`), the
  prompt is prefixed with the on-disk path of all five repos so that class
  of question is not unfairly impossible for control purely because of
  which directory it started in - the comparison this experiment cares
  about is "graph vs. files", not "told where things are vs. not". The
  other two classes (`single-repo`, `hidden-dependency`) only ever concern
  the one repo named in `[repo]`, so they get no preamble - adding one would
  be a token tax on control with nothing to justify it. Treatment still
  benefits from `CLAUDE.md` beyond that, regardless of class; that
  asymmetry is intentional (see questions.md and results.md) - the graph
  audit found agents rarely reach for graph tools unprompted.
- **both arms** get `SKILL_ISOLATION_ARGS` (`--disallowedTools Skill
  --disable-slash-commands`): the user's global skills include a graphify
  skill whose trigger text says to treat any question as a graphify query
  when `graphify-out/` exists - either arm picking that up would contaminate
  the comparison. The init event's `mcp_servers`, `skills`, `memory_paths`
  and `cwd` are recorded into each run's JSON so the isolation can be
  checked after the fact, and a `contamination` counter tallies Read/Grep/
  Glob/Bash tool calls whose input mentions `graphify-out` (a control run
  reading a stale per-repo graph instead of the code). `kgc build` no longer
  writes into the target repos, but earlier builds did: delete any
  `<repo>/graphify-out/` before control runs (see results.md).

Metrics
-------
The comparison figure is `context_tokens = input_tokens +
cache_creation_input_tokens + cache_read_input_tokens`. With prompt caching
nearly everything the model reads arrives as cache creation or cache read,
not `input_tokens` - the captured sample has input_tokens=10 against
cache_creation_input_tokens=25,641 - so input+output alone would omit ~99%
of the context. `total_cost_usd` is kept beside it (cache reads are billed
at a fraction of fresh input, so the two can rank arms differently).
`permission_denials` (from the result event) is recorded because a denied
MCP call is indistinguishable, in the answer text, from the agent choosing
not to use the graph; results.md treats any run with denials as invalid.

Every question's live prompt is the bare numbered line from questions.md -
never the indented grader-note sub-bullets beneath it, which carry the
grounding/verified-answer/grading-rule content a human needs to judge
correctness. `_QUESTION_RE` only matches the numbered line, by construction
(see `parse_questions`); keeping the rubric out of the prompt is enforced by
`tests/test_verify_runner.py`'s rubric-language guard tests, not just by
convention in the markdown.

CLI flags verified against `claude --help` (Claude Code 2.1.258, re-checked
2026-10-04) while writing this file:
  --output-format stream-json, --verbose, --model, --mcp-config,
  --strict-mcp-config, --allowedTools/--allowed-tools,
  --disallowedTools/--disallowed-tools, --disable-slash-commands ("Disable
  all skills"), --permission-mode
  (choices: acceptEdits, auto, bypassPermissions, manual, dontAsk, plan - note
  there is no literal "default" choice; see `_permission_mode_args` below for
  how this script spells "use the implicit default" anyway).
  `--max-turns` does **not** exist in this CLI version and is never passed.

Per-run isolation: a malformed stdout line, a hung process, or any other
single-run failure must not abort the other ~47 runs in a batch. See
`parse_stream_json` (skips/counts malformed lines), `_execute` (`timeout=`,
default 900s via `--timeout`; note the timeout kills `claude` only - the MCP
servers it spawned, `uvx mcp-neo4j-cypher` and `python -m graphify.serve`,
are separate children that exit on their own once their stdio closes, so
check `ps` after a timed-out treatment run), and `main` (each run is wrapped
in its own try/except; a failure writes `verify/runs/<label>.error.json`
instead of aborting, and the exit code is 1 iff anything failed). Re-running
after a partial batch skips any run whose `<label>.json` already exists;
`--force` re-runs everything anyway, and a re-run replaces that label's
`results.csv` row (one row per (question, arm, run), latest wins) and
removes any stale `<label>.error.json`.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kgc.config import load_repos

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
QUESTIONS_PATH = HERE / "questions.md"
EMPTY_MCP_PATH = HERE / "empty-mcp.json"
MCP_CONFIG_PATH = PROJECT_ROOT / ".mcp.json"
RUNS_DIR = HERE / "runs"
RESULTS_CSV = HERE / "results.csv"
SCHEMA_SAMPLE_PATH = HERE / "schema-sample.jsonl"
REPOS_YAML = PROJECT_ROOT / "repos.yaml"

CSV_HEADER = [
    "question", "arm", "run", "model", "input_tokens", "output_tokens",
    "cache_creation_tokens", "cache_read_tokens", "context_tokens",
    "total_cost_usd", "duration_ms", "num_turns", "permission_denials",
    "contamination", "malformed_lines", "graphify_calls", "neo4j_calls",
    "answer_file",
]
# The columns that identify one run; a re-run (--force) replaces that row.
CSV_KEY = ("question", "arm", "run")

# Passed to BOTH arms - see module docstring, "Arms".
SKILL_ISOLATION_ARGS = ["--disallowedTools", "Skill", "--disable-slash-commands"]

# Built-in tools whose input mentioning this marker means the agent reached
# for a per-repo graphify output on disk instead of (or as well as) the code.
CONTAMINATION_TOOLS = frozenset({"Read", "Grep", "Glob", "Bash"})
CONTAMINATION_MARKER = "graphify-out"

# Fields copied from the stream's `system`/`init` event into the run record.
INIT_FIELDS = ("cwd", "mcp_servers", "skills", "memory_paths", "permissionMode", "model")

# One line per question in questions.md: "N. **class** [repo] text".
_QUESTION_RE = re.compile(r"^(\d+)\.\s+\*\*([a-z-]+)\*\*\s+\[([a-z-]+)\]\s+(.+)$")

ARMS = ("control", "treatment")


@dataclass(frozen=True)
class Question:
    number: int
    cls: str
    repo: str
    text: str


@dataclass(frozen=True)
class Invocation:
    """A `claude` invocation this script would run: the argv (argv[0] is
    always "claude") and the working directory it must run in."""
    argv: list[str]
    cwd: Path


def parse_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    """Parse the numbered question list out of questions.md. Non-matching
    lines (headings, prose, the format example) are silently skipped."""
    questions: list[Question] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        m = _QUESTION_RE.match(raw_line.strip())
        if not m:
            continue
        number, cls, repo, text = m.groups()
        questions.append(Question(number=int(number), cls=cls, repo=repo, text=text.strip()))
    return questions


def repo_path_map(repos_yaml: Path = REPOS_YAML) -> dict[str, Path]:
    """tag -> absolute repo path, straight from repos.yaml - the same file
    `kgc build`/`kgc push` use, so this can never drift from what is actually
    in the graph."""
    return {repo.tag: repo.path for repo in load_repos(repos_yaml)}


def _control_preamble(repos: dict[str, Path]) -> str:
    lines = ["The following five repositories are available on disk:"]
    for tag, path in repos.items():
        lines.append(f"- {tag}: {path}")
    return "\n".join(lines)


# Only these two classes genuinely need to know the other four repos exist
# (a cross-repo impact question, or a blind-spot question about something
# that might live anywhere) - single-repo and hidden-dependency questions
# only ever need the one repo named in [repo]. Applying the preamble to all
# twelve would be a small, systematic token tax on control for the seven
# questions that don't need it, tilting the ratio toward treatment for free.
PREAMBLE_CLASSES = frozenset({"cross-repo", "blind-spot"})


def build_prompt(arm: str, question: Question, repos: dict[str, Path]) -> str:
    if arm == "control" and question.cls in PREAMBLE_CLASSES:
        return f"{_control_preamble(repos)}\n\n{question.text}"
    return question.text


def _permission_mode_args(permission_mode: str) -> list[str]:
    # "default" is not a valid --permission-mode CLI choice (confirmed via
    # `claude --help`); Claude Code's own stream-json init event reports
    # permissionMode "default" when the flag is simply omitted, so that is
    # how this script spells "use the implicit default" - see module
    # docstring and results.md.
    if permission_mode == "default":
        return []
    return ["--permission-mode", permission_mode]


def build_invocation(
    arm: str,
    question: Question,
    *,
    run: int,
    model: str,
    permission_mode: str,
    allowed_tools: str | None,
    repos: dict[str, Path] | None = None,
) -> Invocation:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}, expected one of {ARMS}")
    repos = repos if repos is not None else repo_path_map()
    prompt = build_prompt(arm, question, repos)

    argv = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
    ]
    argv += _permission_mode_args(permission_mode)
    if allowed_tools:
        argv += ["--allowedTools", allowed_tools]
    argv += SKILL_ISOLATION_ARGS

    if arm == "treatment":
        argv += ["--mcp-config", str(MCP_CONFIG_PATH), "--strict-mcp-config"]
        cwd = PROJECT_ROOT
    else:
        argv += ["--mcp-config", str(EMPTY_MCP_PATH), "--strict-mcp-config"]
        cwd = repos[question.repo]

    return Invocation(argv=argv, cwd=cwd)


def run_label(arm: str, question: Question, run: int) -> str:
    return f"{arm}-q{question.number:02d}-r{run}"


def parse_stream_json(lines: Iterable[str]) -> dict:
    """Parse the raw lines of a `claude -p ... --output-format stream-json`
    run. Schema confirmed against a real captured sample
    (verify/schema-sample.jsonl, produced by the one schema-discovery call
    this task was permitted to make) - the final `result` event carries
    `usage`, `total_cost_usd`, `duration_ms`, `num_turns`, and the answer
    text itself in `result`. Tool calls are tallied from `tool_use` content
    blocks inside `assistant` events by MCP server prefix
    (`mcp__graphify__*` / `mcp__neo4j__*`); the captured sample used no
    tools, so that half of the parser is exercised only by the synthetic
    tests in tests/test_verify_runner.py, not by the sample itself.

    A single malformed/interleaved line (truncated output, a stray stderr
    line that leaked onto stdout, ...) must not blow up the whole run: such
    lines are skipped and counted in `malformed_lines`, not raised on. The
    one thing that still raises is finding no `result` event at all - that
    is a real failure (the run never completed), not noise to shrug off.

    Also captured: the `system`/`init` event's isolation fields (`init`), the
    result event's `permission_denials` count, cache token counts and the
    derived `context_tokens`, and a `contamination` tally - see the module
    docstring for what each is for.
    """
    result_event: dict | None = None
    init: dict = {}
    graphify_calls = 0
    neo4j_calls = 0
    contamination = 0
    malformed_lines = 0
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            # Hook events are also type=system; only the init event carries
            # the session's servers/skills/paths.
            init = {k: event[k] for k in INIT_FIELDS if k in event}
        elif etype == "assistant":
            content = event.get("message", {}).get("content", []) or []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name.startswith("mcp__graphify__"):
                    graphify_calls += 1
                elif name.startswith("mcp__neo4j__"):
                    neo4j_calls += 1
                elif name in CONTAMINATION_TOOLS and CONTAMINATION_MARKER in json.dumps(
                        block.get("input", {}), default=str):
                    contamination += 1
        elif etype == "result":
            result_event = event
    if result_event is None:
        raise ValueError("no 'result' event found in stream-json output")

    usage = result_event.get("usage", {}) or {}
    context_parts = [usage.get("input_tokens"),
                     usage.get("cache_creation_input_tokens"),
                     usage.get("cache_read_input_tokens")]
    context_tokens = (
        None if all(part is None for part in context_parts)
        else sum(part or 0 for part in context_parts)
    )
    return {
        "answer": result_event.get("result", ""),
        "is_error": result_event.get("is_error"),
        "num_turns": result_event.get("num_turns"),
        "duration_ms": result_event.get("duration_ms"),
        "total_cost_usd": result_event.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "context_tokens": context_tokens,
        "permission_denials": len(result_event.get("permission_denials") or []),
        "contamination": contamination,
        "init": init,
        "graphify_calls": graphify_calls,
        "neo4j_calls": neo4j_calls,
        "malformed_lines": malformed_lines,
        "raw_result_event": result_event,
    }


def _ensure_csv_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CSV_HEADER)


def _upsert_csv_row(path: Path, row: dict) -> None:
    """Write `row`, replacing any existing row for the same (question, arm,
    run) - a `--force` re-run must not leave two rows for one label. The
    file is small (one row per run) so rewriting it whole is fine; rows
    written by an older column set are carried forward with blanks."""
    _ensure_csv_header(path)
    key = tuple(str(row[col]) for col in CSV_KEY)
    with path.open(newline="", encoding="utf-8") as f:
        kept = [r for r in csv.DictReader(f)
                if tuple(str(r.get(col, "")) for col in CSV_KEY) != key]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for r in kept:
            writer.writerow([r.get(col, "") for col in CSV_HEADER])
        writer.writerow([row[col] for col in CSV_HEADER])


class RunFailed(RuntimeError):
    """One `claude -p` invocation failed (nonzero exit with no stdout, or a
    timeout). Carries a stderr tail (possibly empty) so `main`'s per-run
    except-block can write it into the `.error.json` without re-parsing
    anything."""

    def __init__(self, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


_STDERR_TAIL_CHARS = 2000


def _stderr_tail(raw: str | bytes | None) -> str:
    """Normalise then truncate a captured stderr blob for an error record.

    CPython's `subprocess.TimeoutExpired.stderr` is **bytes** even with
    `text=True`, if the child had written to stderr before being killed
    (reproduced directly against this repo's Python 3.13:
    `subprocess.run([...], capture_output=True, text=True, timeout=0.3)` on
    a process that writes to stderr then sleeps past the timeout). Returning
    anything but `str` here means `RunFailed.stderr_tail` can carry bytes,
    which `json.dumps` in `main`'s error handler cannot serialise - an error
    handler that can itself raise, aborting the whole batch. Normalise here,
    at the source, so every caller gets `str` unconditionally.
    """
    if not raw:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw[-_STDERR_TAIL_CHARS:]


def _execute(inv: Invocation, *, timeout: float) -> str:
    try:
        proc = subprocess.run(
            inv.argv, cwd=inv.cwd, capture_output=True, text=True, check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunFailed(
            f"claude timed out after {timeout}s", stderr_tail=_stderr_tail(exc.stderr),
        ) from exc
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RunFailed(
            f"claude exited {proc.returncode} with no stdout",
            stderr_tail=_stderr_tail(proc.stderr),
        )
    return proc.stdout


def _run_file_exists(label: str) -> bool:
    return (RUNS_DIR / f"{label}.json").exists()


def _run_one(
    arm: str, question: Question, run: int, *, model: str,
    permission_mode: str, allowed_tools: str | None, repos: dict[str, Path],
    timeout: float,
) -> None:
    inv = build_invocation(
        arm, question, run=run, model=model, permission_mode=permission_mode,
        allowed_tools=allowed_tools, repos=repos,
    )
    stdout = _execute(inv, timeout=timeout)
    parsed = parse_stream_json(stdout.splitlines())

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    label = run_label(arm, question, run)
    answer_file = RUNS_DIR / f"{label}.json"
    answer_file.write_text(
        json.dumps(
            {
                "question": question.number,
                "arm": arm,
                "run": run,
                "model": model,
                "answer": parsed["answer"],
                "context_tokens": parsed["context_tokens"],
                "cache_creation_tokens": parsed["cache_creation_tokens"],
                "cache_read_tokens": parsed["cache_read_tokens"],
                "permission_denials": parsed["permission_denials"],
                "contamination": parsed["contamination"],
                "init": parsed["init"],
                "malformed_lines": parsed["malformed_lines"],
                "result_event": parsed["raw_result_event"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # A failed earlier attempt at this label is superseded by this success.
    (RUNS_DIR / f"{label}.error.json").unlink(missing_ok=True)

    _upsert_csv_row(RESULTS_CSV, {
        "question": question.number,
        "arm": arm,
        "run": run,
        "model": model,
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "cache_creation_tokens": parsed["cache_creation_tokens"],
        "cache_read_tokens": parsed["cache_read_tokens"],
        "context_tokens": parsed["context_tokens"],
        "total_cost_usd": parsed["total_cost_usd"],
        "duration_ms": parsed["duration_ms"],
        "num_turns": parsed["num_turns"],
        "permission_denials": parsed["permission_denials"],
        "contamination": parsed["contamination"],
        "malformed_lines": parsed["malformed_lines"],
        "graphify_calls": parsed["graphify_calls"],
        "neo4j_calls": parsed["neo4j_calls"],
        "answer_file": str(answer_file.relative_to(PROJECT_ROOT)),
    })
    print(f"  wrote {answer_file.relative_to(PROJECT_ROOT)}")


def _parse_question_filter(spec: str | None, questions: list[Question]) -> list[Question]:
    if not spec:
        return questions
    wanted = {int(n) for n in spec.split(",") if n.strip()}
    return [q for q in questions if q.number in wanted]


def _schema_check(path: Path) -> int:
    if not path.exists():
        print(f"no sample at {path}", file=sys.stderr)
        return 1
    parsed = parse_stream_json(path.read_text(encoding="utf-8").splitlines())
    print(json.dumps({k: v for k, v in parsed.items() if k != "raw_result_event"}, indent=2))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arm", choices=(*ARMS, "both"), default="both")
    p.add_argument("--runs", type=int, default=2, help="runs per question per arm (2 min, 3 recommended)")
    p.add_argument("--questions", default=None, help="comma-separated question numbers, e.g. 1,5,9")
    p.add_argument("--model", default="sonnet", help="cheaper than the default for ~48 runs")
    p.add_argument(
        "--permission-mode", default="default",
        help="acceptEdits|auto|bypassPermissions|manual|dontAsk|plan, or 'default' "
             "(default) to omit the flag entirely. Headless runs may need "
             "acceptEdits for grep/Bash to proceed without a human to approve "
             "them; prefer --allowed-tools scoping over a broad permission mode "
             "when you can state exactly which tools a question needs.",
    )
    p.add_argument(
        "--allowed-tools", default=None,
        help="passed through to claude's --allowedTools verbatim, e.g. 'Read Grep Glob' "
             "for control or 'mcp__graphify mcp__neo4j Read Grep Glob' for treatment "
             "(an MCP server's tools are allowed by server name; MCP rules take no "
             "wildcard). A run with permission_denials > 0 is invalid - see results.md.",
    )
    p.add_argument("--dry-run", action="store_true", help="print every command; write nothing")
    p.add_argument(
        "--timeout", type=float, default=900.0,
        help="seconds before a single `claude -p` invocation is killed and that run "
             "counted as failed (default: 900). A hung headless run - e.g. stuck on a "
             "permission prompt with no human to answer it - must not hang the batch.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="re-run even a question/arm/run whose verify/runs/<label>.json already "
             "exists. Without it, re-running after a partial or failed batch just "
             "continues - every completed run is skipped, not repeated.",
    )
    p.add_argument(
        "--schema-check", metavar="PATH", nargs="?", const=str(SCHEMA_SAMPLE_PATH),
        help="parse a saved stream-json sample (default: verify/schema-sample.jsonl) and print the result",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error(f"--runs must be at least 1, got {args.runs}")

    if args.schema_check is not None:
        return _schema_check(Path(args.schema_check))

    questions = _parse_question_filter(args.questions, parse_questions(QUESTIONS_PATH))
    arms = ARMS if args.arm == "both" else (args.arm,)
    repos = repo_path_map()
    failed_labels: list[str] = []

    for question in questions:
        for arm in arms:
            for run in range(1, args.runs + 1):
                label = run_label(arm, question, run)

                if not args.force and _run_file_exists(label):
                    print(f"[{label}] skip (exists)")
                    continue

                if args.dry_run:
                    inv = build_invocation(
                        arm, question, run=run, model=args.model,
                        permission_mode=args.permission_mode,
                        allowed_tools=args.allowed_tools, repos=repos,
                    )
                    print(f"[{label}] cwd={inv.cwd}")
                    print("  " + " ".join(_shell_quote(a) for a in inv.argv))
                    continue

                print(f"[{label}] running...")
                try:
                    _run_one(
                        arm, question, run, model=args.model,
                        permission_mode=args.permission_mode,
                        allowed_tools=args.allowed_tools, repos=repos,
                        timeout=args.timeout,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad run must not abort the batch
                    failed_labels.append(label)
                    print(f"  FAILED {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    # The error handler itself must never be able to raise -
                    # that would abort the batch on the very path meant to
                    # keep one bad run from doing exactly that. `default=str`
                    # means no field's type can blow up json.dumps (e.g. a
                    # stray non-str stderr_tail from an exception this script
                    # didn't raise itself), and the write is its own
                    # try/except so even a disk/permissions error here just
                    # gets logged, never propagated.
                    record = {
                        "run_label": label,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "stderr_tail": getattr(exc, "stderr_tail", ""),
                    }
                    try:
                        RUNS_DIR.mkdir(parents=True, exist_ok=True)
                        error_path = RUNS_DIR / f"{label}.error.json"
                        error_path.write_text(
                            json.dumps(record, indent=2, default=str), encoding="utf-8",
                        )
                    except Exception as write_exc:  # noqa: BLE001 - see above
                        print(
                            f"  (also failed to write error record for {label}: "
                            f"{type(write_exc).__name__}: {write_exc})", file=sys.stderr,
                        )
                        print(f"  {record}", file=sys.stderr)

    if failed_labels:
        print(f"{len(failed_labels)} run(s) failed: {', '.join(failed_labels)}", file=sys.stderr)
        return 1
    return 0


def _shell_quote(arg: str) -> str:
    if arg and all(c.isalnum() or c in "-_./:" for c in arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
