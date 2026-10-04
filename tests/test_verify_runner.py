"""Pure-function tests for the verification harness runner.

No subprocess is ever invoked here - these tests cover command construction
and output parsing only. Actually running `claude -p ...` is the human's job
(see verify/results.md, "How to run"); see verify/run_arm.py's module
docstring for why.
"""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from verify.run_arm import (
    CSV_HEADER,
    EMPTY_MCP_PATH,
    PREAMBLE_CLASSES,
    PROJECT_ROOT,
    QUESTIONS_PATH,
    build_invocation,
    main,
    parse_questions,
    parse_stream_json,
    repo_path_map,
    run_label,
)

SCHEMA_SAMPLE = Path(__file__).resolve().parent.parent / "verify" / "schema-sample.jsonl"

# Rubric/grading language that must never appear in text sent to a model -
# it belongs only in the indented grader-note sub-bullets questions.md keeps
# underneath each item, which _QUESTION_RE (and therefore the live prompt)
# never captures.
RUBRIC_PHRASES = (
    "correct answer", "counts as", "ground truth", "grader",
    "expected answer", "must come back", "should say", "confidently",
)


# --- questions.md parsing -----------------------------------------------

def test_questions_md_parses_to_exactly_twelve():
    questions = parse_questions(QUESTIONS_PATH)
    assert len(questions) == 12
    assert [q.number for q in questions] == list(range(1, 13))


def test_every_question_has_number_class_repo_text():
    valid_classes = {"cross-repo", "single-repo", "hidden-dependency", "blind-spot"}
    valid_repos = set(repo_path_map())
    for q in parse_questions(QUESTIONS_PATH):
        assert isinstance(q.number, int)
        assert q.cls in valid_classes
        assert q.repo in valid_repos
        assert q.text and not q.text.isspace()


def test_question_class_distribution_matches_the_spec():
    questions = parse_questions(QUESTIONS_PATH)
    counts: dict[str, int] = {}
    for q in questions:
        counts[q.cls] = counts.get(q.cls, 0) + 1
    assert counts == {
        "cross-repo": 5,
        "single-repo": 3,
        "hidden-dependency": 2,
        "blind-spot": 2,
    }


# --- no rubric/ground-truth leakage into the live prompt -------------------

def test_question_text_contains_no_rubric_language():
    for q in parse_questions(QUESTIONS_PATH):
        lowered = q.text.lower()
        for phrase in RUBRIC_PHRASES:
            assert phrase not in lowered, (
                f"Q{q.number} leaks rubric phrase {phrase!r} into the prompt text: {q.text!r}"
            )


def test_q9_through_q12_prompts_contain_no_rubric_language():
    # These are the probes the POC's premise rests on (hidden-dependency,
    # blind-spot) - a prior draft leaked the grading rule and/or the answer
    # itself into the numbered line, which build_prompt() forwards verbatim.
    repos = repo_path_map()
    by_number = {q.number: q for q in parse_questions(QUESTIONS_PATH)}
    for n in (9, 10, 11, 12):
        q = by_number[n]
        for arm in ("control", "treatment"):
            inv = build_invocation(
                arm, q, run=1, model="sonnet", permission_mode="default",
                allowed_tools=None, repos=repos,
            )
            prompt = inv.argv[inv.argv.index("-p") + 1].lower()
            for phrase in RUBRIC_PHRASES:
                assert phrase not in prompt, f"Q{n} {arm} prompt leaks {phrase!r}"


# --- control-arm preamble is scoped to cross-repo/blind-spot only ----------

def test_preamble_classes_are_exactly_cross_repo_and_blind_spot():
    assert PREAMBLE_CLASSES == frozenset({"cross-repo", "blind-spot"})


def test_cross_repo_and_blind_spot_control_prompts_carry_the_preamble():
    repos = repo_path_map()
    for q in parse_questions(QUESTIONS_PATH):
        if q.cls not in ("cross-repo", "blind-spot"):
            continue
        inv = build_invocation(
            "control", q, run=1, model="sonnet", permission_mode="default",
            allowed_tools=None, repos=repos,
        )
        prompt = inv.argv[inv.argv.index("-p") + 1]
        assert "five repositories are available" in prompt
        for path in repos.values():
            assert str(path) in prompt


def test_single_repo_and_hidden_dependency_control_prompts_carry_no_preamble():
    repos = repo_path_map()
    for q in parse_questions(QUESTIONS_PATH):
        if q.cls not in ("single-repo", "hidden-dependency"):
            continue
        inv = build_invocation(
            "control", q, run=1, model="sonnet", permission_mode="default",
            allowed_tools=None, repos=repos,
        )
        prompt = inv.argv[inv.argv.index("-p") + 1]
        assert prompt == q.text
        assert "five repositories" not in prompt


def test_treatment_prompt_never_carries_the_preamble_regardless_of_class():
    repos = repo_path_map()
    for q in parse_questions(QUESTIONS_PATH):
        inv = build_invocation(
            "treatment", q, run=1, model="sonnet", permission_mode="default",
            allowed_tools=None, repos=repos,
        )
        prompt = inv.argv[inv.argv.index("-p") + 1]
        assert prompt == q.text


# --- repo_path_map --------------------------------------------------------

def test_repo_path_map_has_all_five_tags():
    repos = repo_path_map()
    assert set(repos) == {
        "knowledge-retrieval",
        "graphify",
        "neural-particle-method",
        "diffusion-bridge",
        "claude",
    }
    for path in repos.values():
        assert isinstance(path, Path)


# --- command construction -------------------------------------------------

def _first_question():
    return parse_questions(QUESTIONS_PATH)[0]


def test_treatment_invocation_uses_project_cwd_and_no_mcp_config():
    q = _first_question()
    inv = build_invocation(
        "treatment", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None,
    )
    assert inv.cwd == PROJECT_ROOT
    assert "--mcp-config" not in inv.argv
    assert "--strict-mcp-config" not in inv.argv
    assert q.text in " ".join(inv.argv)


def test_control_invocation_uses_question_repo_cwd_and_empty_mcp_config():
    q = _first_question()
    repos = repo_path_map()
    inv = build_invocation(
        "control", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None,
    )
    assert inv.cwd == repos[q.repo]
    assert "--mcp-config" in inv.argv
    idx = inv.argv.index("--mcp-config")
    assert inv.argv[idx + 1] == str(EMPTY_MCP_PATH)
    assert "--strict-mcp-config" in inv.argv


def test_control_prompt_lists_all_five_repo_paths_for_fairness():
    q = _first_question()
    inv = build_invocation(
        "control", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None,
    )
    prompt = inv.argv[inv.argv.index("-p") + 1]
    for path in repo_path_map().values():
        assert str(path) in prompt


def test_permission_mode_default_omits_the_flag():
    q = _first_question()
    inv = build_invocation(
        "treatment", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None,
    )
    assert "--permission-mode" not in inv.argv


def test_permission_mode_explicit_value_is_passed_through():
    q = _first_question()
    inv = build_invocation(
        "treatment", q, run=1, model="sonnet", permission_mode="acceptEdits", allowed_tools=None,
    )
    assert "--permission-mode" in inv.argv
    idx = inv.argv.index("--permission-mode")
    assert inv.argv[idx + 1] == "acceptEdits"


def test_allowed_tools_passed_through_when_given():
    q = _first_question()
    inv = build_invocation(
        "control", q, run=1, model="sonnet", permission_mode="default",
        allowed_tools="Read Grep Glob",
    )
    assert "--allowedTools" in inv.argv
    idx = inv.argv.index("--allowedTools")
    assert inv.argv[idx + 1] == "Read Grep Glob"


def test_both_arms_request_stream_json_and_a_model():
    q = _first_question()
    for arm in ("control", "treatment"):
        inv = build_invocation(
            arm, q, run=1, model="haiku", permission_mode="default", allowed_tools=None,
        )
        assert "--output-format" in inv.argv
        assert inv.argv[inv.argv.index("--output-format") + 1] == "stream-json"
        assert "--model" in inv.argv
        assert inv.argv[inv.argv.index("--model") + 1] == "haiku"
        assert "--verbose" in inv.argv


# --- stream-json parsing ---------------------------------------------------

def test_parse_stream_json_extracts_usage_from_a_minimal_result_event():
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hi"}]},
        }),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "duration_ms": 1699,
            "total_cost_usd": 0.0516,
            "result": "OK",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 63,
                "cache_read_input_tokens": 0,
            },
        }),
    ]
    parsed = parse_stream_json(lines)
    assert parsed["answer"] == "OK"
    assert parsed["input_tokens"] == 10
    assert parsed["output_tokens"] == 63
    assert parsed["cache_read_tokens"] == 0
    assert parsed["total_cost_usd"] == 0.0516
    assert parsed["duration_ms"] == 1699
    assert parsed["num_turns"] == 1
    assert parsed["graphify_calls"] == 0
    assert parsed["neo4j_calls"] == 0


def test_parse_stream_json_tallies_mcp_tool_calls_by_server():
    lines = [
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "mcp__graphify__query_graph", "input": {}},
                {"type": "tool_use", "name": "mcp__graphify__get_node", "input": {}},
            ]},
        }),
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "mcp__neo4j__read_neo4j_cypher", "input": {}},
            ]},
        }),
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {}},
            ]},
        }),
        json.dumps({
            "type": "result", "is_error": False, "num_turns": 3, "duration_ms": 1,
            "total_cost_usd": 0.01, "result": "answer", "usage": {},
        }),
    ]
    parsed = parse_stream_json(lines)
    assert parsed["graphify_calls"] == 2
    assert parsed["neo4j_calls"] == 1


def test_parse_stream_json_raises_without_a_result_event():
    with pytest.raises(ValueError):
        parse_stream_json([json.dumps({"type": "system", "subtype": "init"})])


def test_parse_stream_json_skips_malformed_lines_and_counts_them():
    lines = [
        "not json at all {{{",
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hi"}]},
        }),
        "{also broken",
        json.dumps({
            "type": "result", "is_error": False, "num_turns": 1, "duration_ms": 1,
            "total_cost_usd": 0.01, "result": "answer", "usage": {},
        }),
    ]
    parsed = parse_stream_json(lines)
    assert parsed["answer"] == "answer"
    assert parsed["malformed_lines"] == 2


def test_parse_stream_json_raises_without_result_even_with_malformed_lines():
    with pytest.raises(ValueError):
        parse_stream_json(["not json", "{also bad", "still not json"])


@pytest.mark.skipif(not SCHEMA_SAMPLE.exists(), reason="no captured schema sample")
def test_parser_handles_the_real_captured_schema_sample():
    lines = SCHEMA_SAMPLE.read_text(encoding="utf-8").splitlines()
    parsed = parse_stream_json(lines)
    assert parsed["answer"] == "OK"
    assert parsed["num_turns"] == 1
    assert parsed["input_tokens"] == 10
    assert parsed["output_tokens"] == 63
    assert parsed["cache_read_tokens"] == 0
    assert parsed["duration_ms"] == 1699
    assert parsed["total_cost_usd"] == pytest.approx(0.051607)
    assert parsed["graphify_calls"] == 0
    assert parsed["neo4j_calls"] == 0


# --- CSV header -------------------------------------------------------------

def test_csv_header_matches_the_documented_columns():
    assert CSV_HEADER == [
        "question", "arm", "run", "model", "input_tokens", "output_tokens",
        "cache_read_tokens", "total_cost_usd", "duration_ms", "num_turns",
        "malformed_lines", "graphify_calls", "neo4j_calls", "answer_file",
    ]


def test_run_one_persists_malformed_lines_in_json_and_csv(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    results_csv = tmp_path / "results.csv"
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", results_csv)
    # _run_one records answer_file relative to PROJECT_ROOT; relocate that
    # too so the tmp_path-based RUNS_DIR is actually underneath it.
    monkeypatch.setattr("verify.run_arm.PROJECT_ROOT", tmp_path)

    stdout = "\n".join([
        "not json at all {{{",
        json.dumps({
            "type": "result", "is_error": False, "num_turns": 1, "duration_ms": 1,
            "total_cost_usd": 0.01, "result": "answer", "usage": {},
        }),
    ])
    monkeypatch.setattr("verify.run_arm._execute", lambda inv, *, timeout: stdout)

    from verify.run_arm import _run_one

    q = parse_questions(QUESTIONS_PATH)[0]
    _run_one(
        "control", q, 1, model="sonnet", permission_mode="default",
        allowed_tools=None, repos=repo_path_map(), timeout=10,
    )

    answer_file = runs_dir / "control-q01-r1.json"
    data = json.loads(answer_file.read_text())
    assert data["malformed_lines"] == 1

    with results_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["malformed_lines"] == "1"


# --- --dry-run writes nothing ----------------------------------------------

def test_dry_run_creates_no_files_under_verify_runs(tmp_path, monkeypatch, capsys):
    runs_dir = tmp_path / "runs"
    results_csv = tmp_path / "results.csv"
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", results_csv)

    result = main(["--dry-run", "--questions", "1", "--runs", "1"])

    assert result == 0
    assert not runs_dir.exists() or not list(runs_dir.glob("*.json"))
    assert not results_csv.exists()
    out = capsys.readouterr().out
    assert "claude" in out
    assert "-p" in out


def test_dry_run_prints_both_arms_by_default(capsys):
    result = main(["--dry-run", "--questions", "1", "--runs", "1"])
    assert result == 0
    out = capsys.readouterr().out
    assert "control-q01-r1" in out
    assert "treatment-q01-r1" in out


def test_dry_run_respects_arm_filter(capsys):
    result = main(["--dry-run", "--questions", "1", "--runs", "1", "--arm", "treatment"])
    assert result == 0
    out = capsys.readouterr().out
    assert "treatment-q01-r1" in out
    assert "control-q01-r1" not in out


# --- _execute: timeout plumbing (subprocess.run is monkeypatched, never real) --

def test_execute_passes_timeout_to_subprocess_run(monkeypatch):
    from verify.run_arm import Invocation, _execute

    recorded_kwargs = {}

    def fake_run(argv, **kwargs):
        recorded_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"type": "result", "result": "ok", "usage": {}}), stderr="",
        )

    monkeypatch.setattr("verify.run_arm.subprocess.run", fake_run)
    inv = Invocation(argv=["claude", "-p", "x"], cwd=Path("."))

    out = _execute(inv, timeout=123)

    assert recorded_kwargs["timeout"] == 123
    assert "ok" in out


def test_execute_raises_run_failed_on_timeout(monkeypatch):
    from verify.run_arm import Invocation, RunFailed, _execute

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"), output=b"", stderr=b"stuck")

    monkeypatch.setattr("verify.run_arm.subprocess.run", fake_run)
    inv = Invocation(argv=["claude", "-p", "x"], cwd=Path("."))

    with pytest.raises(RunFailed) as exc_info:
        _execute(inv, timeout=5)
    assert "timed out" in str(exc_info.value)
    # CPython gives TimeoutExpired.stderr as bytes when text=True and the
    # child wrote to stderr before the kill (reproduced on this repo's
    # Python 3.13). RunFailed.stderr_tail must always be str - it is written
    # straight into an .error.json by main() later.
    assert isinstance(exc_info.value.stderr_tail, str)
    assert "stuck" in exc_info.value.stderr_tail


def test_execute_raises_run_failed_on_nonzero_exit_with_no_stdout(monkeypatch):
    from verify.run_arm import Invocation, RunFailed, _execute

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr("verify.run_arm.subprocess.run", fake_run)
    inv = Invocation(argv=["claude", "-p", "x"], cwd=Path("."))

    with pytest.raises(RunFailed) as exc_info:
        _execute(inv, timeout=5)
    assert "permission denied" in exc_info.value.stderr_tail


# --- main(): one run failing does not abort the batch -----------------------

def test_main_continues_after_one_run_fails_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", tmp_path / "results.csv")

    calls: list[str] = []

    def fake_run_one(arm, question, run, *, model, permission_mode, allowed_tools, repos, timeout):
        label = run_label(arm, question, run)
        calls.append(label)
        if label == "treatment-q01-r1":
            raise RuntimeError("boom")

    monkeypatch.setattr("verify.run_arm._run_one", fake_run_one)

    result = main(["--questions", "1", "--runs", "1"])

    assert result == 1
    assert set(calls) == {"control-q01-r1", "treatment-q01-r1"}
    error_file = tmp_path / "runs" / "treatment-q01-r1.error.json"
    assert error_file.exists()
    data = json.loads(error_file.read_text())
    assert data["run_label"] == "treatment-q01-r1"
    assert data["exception_type"] == "RuntimeError"
    assert "boom" in data["exception_message"]


def test_main_error_handler_survives_a_non_str_stderr_tail(tmp_path, monkeypatch):
    # The error handler itself must never be able to raise: if some exception
    # (not necessarily RunFailed's own normalised path) carries a non-str
    # stderr_tail, json.dumps(..., indent=2) alone raises TypeError inside
    # the except block, aborting the whole batch - the exact bug this guards.
    from verify.run_arm import RunFailed

    monkeypatch.setattr("verify.run_arm.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", tmp_path / "results.csv")

    calls: list[str] = []

    def fake_run_one(arm, question, run, **kwargs):
        label = run_label(arm, question, run)
        calls.append(label)
        if label == "treatment-q01-r1":
            raise RunFailed("boom", stderr_tail=b"\xff\xfe raw bytes")

    monkeypatch.setattr("verify.run_arm._run_one", fake_run_one)

    result = main(["--questions", "1", "--runs", "1"])

    assert result == 1
    assert set(calls) == {"control-q01-r1", "treatment-q01-r1"}
    error_file = tmp_path / "runs" / "treatment-q01-r1.error.json"
    assert error_file.exists()
    data = json.loads(error_file.read_text())  # must not raise
    assert data["run_label"] == "treatment-q01-r1"
    assert data["exception_type"] == "RunFailed"
    assert "boom" in data["exception_message"]


def test_main_returns_zero_when_every_run_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", tmp_path / "results.csv")
    monkeypatch.setattr("verify.run_arm._run_one", lambda *a, **k: None)

    result = main(["--questions", "1", "--runs", "1"])

    assert result == 0
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").glob("*.error.json"))


# --- resume: skip a run whose output file already exists, unless --force ---

def test_resume_skips_a_run_whose_output_file_already_exists(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "control-q01-r1.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", tmp_path / "results.csv")

    calls: list[str] = []
    monkeypatch.setattr(
        "verify.run_arm._run_one",
        lambda arm, question, run, **k: calls.append(run_label(arm, question, run)),
    )

    result = main(["--questions", "1", "--runs", "1", "--arm", "control"])

    assert result == 0
    assert calls == []


def test_force_reruns_a_run_whose_output_file_already_exists(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "control-q01-r1.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", tmp_path / "results.csv")

    calls: list[str] = []
    monkeypatch.setattr(
        "verify.run_arm._run_one",
        lambda arm, question, run, **k: calls.append(run_label(arm, question, run)),
    )

    result = main(["--questions", "1", "--runs", "1", "--arm", "control", "--force"])

    assert result == 0
    assert calls == ["control-q01-r1"]


def test_dry_run_also_reports_skip_for_an_existing_run(tmp_path, monkeypatch, capsys):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "control-q01-r1.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", tmp_path / "results.csv")

    result = main(["--dry-run", "--questions", "1", "--runs", "1", "--arm", "control"])

    assert result == 0
    out = capsys.readouterr().out
    assert "skip" in out.lower()
