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
    ARM_MCP_CONFIG,
    ARMS,
    CSV_HEADER,
    EMPTY_MCP_PATH,
    EXPECTED_SERVERS,
    MCP_ARMS,
    PREAMBLE_CLASSES,
    PROJECT_ROOT,
    QUESTIONS_PATH,
    TREATMENT_CLAUDE_MD,
    build_invocation,
    isolation_ok,
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


def test_both_arms_disable_user_skills():
    # The user's global graphify skill says "treat any question as a graphify
    # query first when graphify-out/ exists" - either arm picking it up would
    # contaminate the comparison.
    q = _first_question()
    for arm in ARMS:
        inv = build_invocation(
            arm, q, run=1, model="sonnet", permission_mode="default", allowed_tools=None,
        )
        idx = inv.argv.index("--disallowedTools")
        assert inv.argv[idx + 1] == "Skill"
        assert "--disable-slash-commands" in inv.argv


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
    assert parsed["cache_creation_tokens"] is None
    assert parsed["context_tokens"] == 10
    assert parsed["permission_denials"] == 0
    assert parsed["init"] == {}
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
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "mcp__kgc__callers", "input": {}},
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
    assert parsed["kgc_calls"] == 1


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
    # The whole point of the harness: nearly all context arrives as cache
    # creation/read, not input_tokens (10 of 25,651 here).
    assert parsed["cache_creation_tokens"] == 25641
    assert parsed["cache_read_tokens"] == 0
    assert parsed["context_tokens"] == 25651
    assert parsed["permission_denials"] == 0
    assert parsed["contamination"] == 0
    assert parsed["duration_ms"] == 1699
    assert parsed["total_cost_usd"] == pytest.approx(0.051607)
    assert parsed["graphify_calls"] == 0
    assert parsed["neo4j_calls"] == 0
    init = parsed["init"]
    assert init["cwd"]
    assert isinstance(init["mcp_servers"], list) and init["mcp_servers"]
    assert {"name", "status"} <= set(init["mcp_servers"][0])
    assert isinstance(init["skills"], list)
    assert "memory_paths" in init


# --- CSV header -------------------------------------------------------------

def test_csv_header_matches_the_documented_columns():
    assert CSV_HEADER == [
        "question", "arm", "run", "model", "input_tokens", "output_tokens",
        "cache_creation_tokens", "cache_read_tokens", "context_tokens",
        "total_cost_usd", "duration_ms", "num_turns", "permission_denials",
        "contamination", "malformed_lines", "graphify_calls", "neo4j_calls", "kgc_calls",
        "answer_file",
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

    # --arm both: this test is about one run failing not aborting the batch,
    # not about how many arms exist - pin it to the pre-kgc two-arm scope.
    result = main(["--questions", "1", "--runs", "1", "--arm", "both"])

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

    # --arm both: this test is about the error handler surviving a non-str
    # stderr_tail, not about how many arms exist - pin it to the pre-kgc
    # two-arm scope.
    result = main(["--questions", "1", "--runs", "1", "--arm", "both"])

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


# --- isolation and validity signals -----------------------------------------

def _init_line(**fields):
    base = {"type": "system", "subtype": "init", "cwd": "/x",
            "mcp_servers": [{"name": "graphify", "status": "connected"},
                            {"name": "neo4j", "status": "connected"}],
            "skills": [], "memory_paths": {"auto": "/m"}, "permissionMode": "default"}
    base.update(fields)
    return json.dumps(base)


def _result_line(**fields):
    base = {"type": "result", "is_error": False, "num_turns": 1, "duration_ms": 1,
            "total_cost_usd": 0.01, "result": "answer", "usage": {}}
    base.update(fields)
    return json.dumps(base)


def _tool_line(*blocks):
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def test_parse_stream_json_counts_permission_denials_and_contamination():
    lines = [
        _init_line(),
        # a hook event is also type=system; only subtype=init is the init event
        json.dumps({"type": "system", "subtype": "hook_started", "cwd": "/not-this"}),
        _tool_line(
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/r/graphify-out/graph.json"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat graphify-out/GRAPH_REPORT.md"}},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "dagster", "path": "/r/pipeline"}},
            # an MCP call mentioning the marker is the graph being used, not contamination
            {"type": "tool_use", "name": "mcp__graphify__query_graph", "input": {"query": "graphify-out"}},
        ),
        _result_line(
            usage={"input_tokens": 5, "cache_creation_input_tokens": 100,
                   "cache_read_input_tokens": 20, "output_tokens": 7},
            permission_denials=[{"tool_name": "mcp__neo4j__read_neo4j_cypher"},
                                {"tool_name": "Bash"}],
        ),
    ]
    parsed = parse_stream_json(lines)
    assert parsed["permission_denials"] == 2
    assert parsed["contamination"] == 2
    assert parsed["graphify_calls"] == 1
    assert parsed["cache_creation_tokens"] == 100
    assert parsed["cache_read_tokens"] == 20
    assert parsed["context_tokens"] == 125
    assert [s["name"] for s in parsed["init"]["mcp_servers"]] == ["graphify", "neo4j"]
    assert parsed["init"]["cwd"] == "/x"
    assert parsed["init"]["memory_paths"] == {"auto": "/m"}
    assert parsed["init"]["skills"] == []


def test_parse_stream_json_context_tokens_is_none_when_usage_is_empty():
    parsed = parse_stream_json([_result_line()])
    assert parsed["context_tokens"] is None
    assert parsed["permission_denials"] == 0
    assert parsed["contamination"] == 0
    assert parsed["init"] == {}


def test_run_one_persists_isolation_fields_and_clears_a_stale_error_record(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    results_csv = tmp_path / "results.csv"
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", results_csv)
    monkeypatch.setattr("verify.run_arm.PROJECT_ROOT", tmp_path)
    # a failed earlier attempt at the same label must not outlive its success
    stale = runs_dir / "control-q01-r1.error.json"
    stale.write_text("{}", encoding="utf-8")

    stdout = "\n".join([
        _init_line(skills=["graphify"]),
        _tool_line({"type": "tool_use", "name": "Glob", "input": {"pattern": "**/graphify-out/*.json"}}),
        _result_line(
            usage={"input_tokens": 1, "cache_creation_input_tokens": 2,
                   "cache_read_input_tokens": 3, "output_tokens": 4},
            permission_denials=[{"tool_name": "Bash"}],
        ),
    ])
    monkeypatch.setattr("verify.run_arm._execute", lambda inv, *, timeout: stdout)

    from verify.run_arm import _run_one

    q = parse_questions(QUESTIONS_PATH)[0]
    _run_one(
        "control", q, 1, model="sonnet", permission_mode="default",
        allowed_tools=None, repos=repo_path_map(), timeout=10,
    )

    assert not stale.exists()
    data = json.loads((runs_dir / "control-q01-r1.json").read_text())
    assert data["init"]["skills"] == ["graphify"]
    assert data["init"]["cwd"] == "/x"
    assert data["permission_denials"] == 1
    assert data["contamination"] == 1
    assert data["cache_creation_tokens"] == 2
    assert data["cache_read_tokens"] == 3
    assert data["context_tokens"] == 6

    with results_csv.open(newline="", encoding="utf-8") as f:
        (row,) = list(csv.DictReader(f))
    assert row["permission_denials"] == "1"
    assert row["contamination"] == "1"
    assert row["cache_creation_tokens"] == "2"
    assert row["cache_read_tokens"] == "3"
    assert row["context_tokens"] == "6"
    assert row["total_cost_usd"] == "0.01"


def test_rerunning_a_label_replaces_its_csv_row_instead_of_appending(tmp_path, monkeypatch):
    # --force re-runs a label; the CSV must end up with one row per
    # (question, arm, run), carrying the latest run, not two rows.
    runs_dir = tmp_path / "runs"
    results_csv = tmp_path / "results.csv"
    monkeypatch.setattr("verify.run_arm.RUNS_DIR", runs_dir)
    monkeypatch.setattr("verify.run_arm.RESULTS_CSV", results_csv)
    monkeypatch.setattr("verify.run_arm.PROJECT_ROOT", tmp_path)

    from verify.run_arm import _run_one

    questions = parse_questions(QUESTIONS_PATH)
    for cost, (q, arm) in ((0.01, (questions[0], "control")),
                           (0.05, (questions[1], "control")),
                           (0.02, (questions[0], "control"))):
        monkeypatch.setattr(
            "verify.run_arm._execute",
            lambda inv, *, timeout, cost=cost: _result_line(total_cost_usd=cost),
        )
        _run_one(arm, q, 1, model="sonnet", permission_mode="default",
                 allowed_tools=None, repos=repo_path_map(), timeout=10)

    with results_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["question"], r["arm"], r["run"]) for r in rows] == [("2", "control", "1"), ("1", "control", "1")]
    assert rows[1]["total_cost_usd"] == "0.02"


# --- --runs validation -------------------------------------------------------

def test_runs_below_one_is_rejected_by_the_parser(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run", "--questions", "1", "--runs", "0"])
    assert exc_info.value.code == 2
    assert "--runs" in capsys.readouterr().err


# --- the kgc arm --------------------------------------------------------------

from kgc.server.claude_md import MARKER, render_claude_md


def test_arms_are_control_treatment_and_kgc():
    assert ARMS == ("control", "treatment", "kgc")
    assert MCP_ARMS == ("treatment", "kgc")
    assert set(ARM_MCP_CONFIG) == set(MCP_ARMS)
    assert ARM_MCP_CONFIG["treatment"].name == "treatment-mcp.json"
    assert ARM_MCP_CONFIG["kgc"].name == "kgc-mcp.json"
    assert EXPECTED_SERVERS == {"control": frozenset(), "treatment": frozenset({"graphify", "neo4j"}),
                                "kgc": frozenset({"kgc"})}


def test_mcp_arms_use_their_own_mcp_config_and_carry_their_own_claude_md():
    q = _first_question()
    for arm in MCP_ARMS:
        inv = build_invocation(arm, q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
        assert inv.cwd is None                                   # materialised per run
        idx = inv.argv.index("--mcp-config")
        assert inv.argv[idx + 1] == str(ARM_MCP_CONFIG[arm])
        assert "--strict-mcp-config" in inv.argv
        assert q.text in " ".join(inv.argv)
    treatment = build_invocation("treatment", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
    kgc = build_invocation("kgc", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
    assert treatment.claude_md == TREATMENT_CLAUDE_MD.read_text(encoding="utf-8")
    assert "read_neo4j_cypher" in treatment.claude_md and MARKER not in treatment.claude_md
    assert kgc.claude_md == render_claude_md(sorted(repo_path_map()))


def test_control_arm_still_runs_in_the_question_repo_with_no_claude_md():
    q = _first_question()
    inv = build_invocation("control", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
    assert inv.cwd == repo_path_map()[q.repo]
    assert inv.claude_md is None


def test_mcp_arm_runs_in_a_temp_cwd_holding_only_its_claude_md(monkeypatch):
    from verify.run_arm import _execute

    recorded = {}

    def fake_run(argv, **kwargs):
        cwd = Path(kwargs["cwd"])
        recorded["cwd"] = cwd
        recorded["files"] = sorted(p.name for p in cwd.iterdir())
        recorded["claude_md"] = (cwd / "CLAUDE.md").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"type": "result", "result": "ok", "usage": {}}), stderr="",
        )

    monkeypatch.setattr("verify.run_arm.subprocess.run", fake_run)
    q = _first_question()
    inv = build_invocation("kgc", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)

    _execute(inv, timeout=5)

    assert recorded["cwd"] != PROJECT_ROOT and PROJECT_ROOT not in recorded["cwd"].parents
    assert recorded["files"] == ["CLAUDE.md"]
    assert recorded["claude_md"] == inv.claude_md
    assert not recorded["cwd"].exists(), "temp cwd is removed after the run"


def test_isolation_ok_compares_the_init_servers_to_the_arms_expectation():
    assert isolation_ok("kgc", {"mcp_servers": [{"name": "kgc", "status": "connected"}]}) is True
    assert isolation_ok("kgc", {"mcp_servers": [{"name": "kgc"}, {"name": "neo4j"}]}) is False
    assert isolation_ok("treatment", {"mcp_servers": [{"name": "graphify"}, {"name": "neo4j"}]}) is True
    assert isolation_ok("control", {"mcp_servers": []}) is True
    assert isolation_ok("control", {}) is None


def test_isolation_ok_does_not_count_a_server_that_failed_to_connect():
    assert isolation_ok("kgc", {"mcp_servers": [{"name": "kgc", "status": "failed"}]}) is False


def test_dry_run_prints_all_three_arms_by_default(capsys):
    assert main(["--dry-run", "--questions", "1", "--runs", "1"]) == 0
    out = capsys.readouterr().out
    for arm in ARMS:
        assert f"{arm}-q01-r1" in out


def test_arm_both_means_control_and_treatment_only(capsys):
    assert main(["--dry-run", "--questions", "1", "--runs", "1", "--arm", "both"]) == 0
    out = capsys.readouterr().out
    assert "control-q01-r1" in out and "treatment-q01-r1" in out
    assert "kgc-q01-r1" not in out


def test_run_one_records_kgc_calls_and_isolation(tmp_path, monkeypatch):
    import verify.run_arm as ra
    monkeypatch.setattr(ra, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(ra, "RESULTS_CSV", tmp_path / "results.csv")
    monkeypatch.setattr(ra, "PROJECT_ROOT", tmp_path)
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "cwd": "/tmp/x",
                    "mcp_servers": [{"name": "kgc", "status": "connected"}]}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__kgc__search", "input": {"query": "x"}}]}}),
        json.dumps({"type": "result", "is_error": False, "num_turns": 2, "duration_ms": 5,
                    "total_cost_usd": 0.0, "result": "answer", "usage": {"input_tokens": 1}}),
    ])
    monkeypatch.setattr(ra, "_execute", lambda inv, *, timeout: stream)
    q = _first_question()

    ra._run_one("kgc", q, 1, model="sonnet", permission_mode="default", allowed_tools=None,
                repos=repo_path_map(), timeout=1.0)

    data = json.loads((tmp_path / "runs" / "kgc-q01-r1.json").read_text(encoding="utf-8"))
    assert data["isolation_ok"] is True
    assert data["kgc_calls"] == 1
    header, row = (tmp_path / "results.csv").read_text(encoding="utf-8").splitlines()[:2]
    assert header.split(",")[-2] == "kgc_calls"
    assert row.split(",")[header.split(",").index("kgc_calls")] == "1"


def test_results_csv_with_an_older_header_is_refused_not_corrupted(tmp_path):
    from verify.run_arm import _ensure_csv_header
    path = tmp_path / "results.csv"
    path.write_text("question,arm,run,old\n1,control,1,x\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="older header"):
        _ensure_csv_header(path)
