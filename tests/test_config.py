import pytest
from pathlib import Path
from kgc.config import load_settings, load_repos, Repo, Settings


def test_load_settings_reads_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "NEO4J_URI=neo4j+s://abc.databases.neo4j.io\n"
        "NEO4J_USERNAME=abc\n"
        "NEO4J_PASSWORD=secret-value\n"
        "NEO4J_DATABASE=abc\n"
    )
    s = load_settings(env)
    assert s.neo4j_uri == "neo4j+s://abc.databases.neo4j.io"
    assert s.neo4j_username == "abc"
    assert s.neo4j_password == "secret-value"
    assert s.neo4j_database == "abc"


def test_load_settings_missing_key_raises_without_leaking_password(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=x\nNEO4J_PASSWORD=super-secret\n")
    with pytest.raises(ValueError) as exc:
        load_settings(env)
    msg = str(exc.value)
    assert "NEO4J_USERNAME" in msg
    assert "super-secret" not in msg


def test_load_settings_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.env")


def test_load_repos_returns_enabled_only_in_order(tmp_path):
    y = tmp_path / "repos.yaml"
    y.write_text(
        "repos:\n"
        "  - path: ~/Projects/alpha\n"
        "    tag: alpha\n"
        "    enabled: true\n"
        "  - path: ~/Projects/beta\n"
        "    tag: beta\n"
        "    enabled: false\n"
        "  - path: ~/Projects/gamma\n"
        "    tag: gamma\n"
        "    enabled: true\n"
    )
    repos = load_repos(y)
    assert [r.tag for r in repos] == ["alpha", "gamma"]
    assert repos[0].path == Path("~/Projects/alpha").expanduser()
    assert repos[0].enabled is True


def test_load_repos_rejects_duplicate_tags(tmp_path):
    y = tmp_path / "repos.yaml"
    y.write_text(
        "repos:\n"
        "  - path: ~/a\n    tag: same\n    enabled: true\n"
        "  - path: ~/b\n    tag: same\n    enabled: true\n"
    )
    with pytest.raises(ValueError, match="duplicate tag"):
        load_repos(y)


def test_load_repos_rejects_tag_with_separator(tmp_path):
    # '::' is graphify's node-ID namespace separator; a tag containing it
    # would corrupt ID prefixing and break the Neo4j reap predicate.
    y = tmp_path / "repos.yaml"
    y.write_text("repos:\n  - path: ~/a\n    tag: 'bad::tag'\n    enabled: true\n")
    with pytest.raises(ValueError, match="must not contain"):
        load_repos(y)


def test_load_repos_names_the_malformed_entry(tmp_path):
    # A raw KeyError from entry["tag"] says nothing about which of N entries
    # is broken; the error must name the entry and the missing key.
    y = tmp_path / "repos.yaml"
    y.write_text(
        "repos:\n"
        "  - path: ~/a\n    tag: ok\n"
        "  - path: ~/b\n"
    )
    with pytest.raises(ValueError, match=r"repos.yaml entry 2 .*'tag'"):
        load_repos(y)


def test_load_repos_rejects_a_non_mapping_entry(tmp_path):
    y = tmp_path / "repos.yaml"
    y.write_text("repos:\n  - just-a-string\n")
    with pytest.raises(ValueError, match=r"repos.yaml entry 1 "):
        load_repos(y)
