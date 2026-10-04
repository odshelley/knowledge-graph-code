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
- **treatment**: cwd is this project (`PROJECT_ROOT`), so `.mcp.json` (the
  `graphify` + `neo4j` MCP servers) and `CLAUDE.md` load normally. No
  `--mcp-config` is passed - the project's own config is used.
- **control**: cwd is the question's primary repo (`repo_path_map()[q.repo]`),
  and an empty `--mcp-config` + `--strict-mcp-config` is passed so no MCP
  server can leak in even if one happens to be configured globally. The
  prompt is prefixed with the on-disk path of all five repos (see
  `CONTROL_PREAMBLE`) so a cross-repo question is not unfairly impossible for
  control purely because of which directory it started in - the comparison
  this experiment cares about is "graph vs. files", not "told where things
  are vs. not". Treatment still benefits from `CLAUDE.md` beyond that; that
  asymmetry is intentional (see questions.md and results.md) - the graph
  audit found agents rarely reach for graph tools unprompted.

CLI flags verified against `claude --help` (Claude Code 2.1.258) while
writing this file:
  --output-format stream-json, --verbose, --model, --mcp-config,
  --strict-mcp-config, --allowedTools/--allowed-tools, --permission-mode
  (choices: acceptEdits, auto, bypassPermissions, manual, dontAsk, plan - note
  there is no literal "default" choice; see `_permission_mode_args` below for
  how this script spells "use the implicit default" anyway).
  `--max-turns` does **not** exist in this CLI version and is never passed.
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
RUNS_DIR = HERE / "runs"
RESULTS_CSV = HERE / "results.csv"
SCHEMA_SAMPLE_PATH = HERE / "schema-sample.jsonl"
REPOS_YAML = PROJECT_ROOT / "repos.yaml"

CSV_HEADER = [
    "question", "arm", "run", "model", "input_tokens", "output_tokens",
    "cache_read_tokens", "total_cost_usd", "duration_ms", "num_turns",
    "graphify_calls", "neo4j_calls", "answer_file",
]

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


def build_prompt(arm: str, question: Question, repos: dict[str, Path]) -> str:
    if arm == "control":
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

    if arm == "treatment":
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
    """
    result_event: dict | None = None
    graphify_calls = 0
    neo4j_calls = 0
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        event = json.loads(raw_line)
        etype = event.get("type")
        if etype == "assistant":
            content = event.get("message", {}).get("content", []) or []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name.startswith("mcp__graphify__"):
                    graphify_calls += 1
                elif name.startswith("mcp__neo4j__"):
                    neo4j_calls += 1
        elif etype == "result":
            result_event = event
    if result_event is None:
        raise ValueError("no 'result' event found in stream-json output")

    usage = result_event.get("usage", {}) or {}
    return {
        "answer": result_event.get("result", ""),
        "is_error": result_event.get("is_error"),
        "num_turns": result_event.get("num_turns"),
        "duration_ms": result_event.get("duration_ms"),
        "total_cost_usd": result_event.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "graphify_calls": graphify_calls,
        "neo4j_calls": neo4j_calls,
        "raw_result_event": result_event,
    }


def _ensure_csv_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CSV_HEADER)


def _append_csv_row(path: Path, row: dict) -> None:
    _ensure_csv_header(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([row[col] for col in CSV_HEADER])


def _execute(inv: Invocation) -> str:
    proc = subprocess.run(
        inv.argv, cwd=inv.cwd, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(
            f"claude exited {proc.returncode} with no stdout; stderr:\n{proc.stderr}"
        )
    return proc.stdout


def _run_one(
    arm: str, question: Question, run: int, *, model: str,
    permission_mode: str, allowed_tools: str | None, repos: dict[str, Path],
) -> None:
    inv = build_invocation(
        arm, question, run=run, model=model, permission_mode=permission_mode,
        allowed_tools=allowed_tools, repos=repos,
    )
    stdout = _execute(inv)
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
                "result_event": parsed["raw_result_event"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    _append_csv_row(RESULTS_CSV, {
        "question": question.number,
        "arm": arm,
        "run": run,
        "model": model,
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "cache_read_tokens": parsed["cache_read_tokens"],
        "total_cost_usd": parsed["total_cost_usd"],
        "duration_ms": parsed["duration_ms"],
        "num_turns": parsed["num_turns"],
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
        help="passed through to claude's --allowedTools verbatim, e.g. 'Read Grep Glob'",
    )
    p.add_argument("--dry-run", action="store_true", help="print every command; write nothing")
    p.add_argument(
        "--schema-check", metavar="PATH", nargs="?", const=str(SCHEMA_SAMPLE_PATH),
        help="parse a saved stream-json sample (default: verify/schema-sample.jsonl) and print the result",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.schema_check is not None:
        return _schema_check(Path(args.schema_check))

    questions = _parse_question_filter(args.questions, parse_questions(QUESTIONS_PATH))
    arms = ARMS if args.arm == "both" else (args.arm,)
    repos = repo_path_map()

    for question in questions:
        for arm in arms:
            for run in range(1, args.runs + 1):
                label = run_label(arm, question, run)
                inv = build_invocation(
                    arm, question, run=run, model=args.model,
                    permission_mode=args.permission_mode,
                    allowed_tools=args.allowed_tools, repos=repos,
                )
                if args.dry_run:
                    print(f"[{label}] cwd={inv.cwd}")
                    print("  " + " ".join(_shell_quote(a) for a in inv.argv))
                    continue
                print(f"[{label}] running...")
                _run_one(
                    arm, question, run, model=args.model,
                    permission_mode=args.permission_mode,
                    allowed_tools=args.allowed_tools, repos=repos,
                )
    return 0


def _shell_quote(arg: str) -> str:
    if arg and all(c.isalnum() or c in "-_./:" for c in arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
