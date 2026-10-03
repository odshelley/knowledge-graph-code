"""Load and validate the two configuration inputs: .env and repos.yaml.

Nothing here prints or raises with a password in the message - the whole file
is written on the assumption that its error strings may end up in a log.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_ENV_KEYS = (
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
)

# graphify namespaces global-graph node IDs as "{tag}::{local_id}", so a tag
# containing the separator would corrupt prefixing and the reap predicate.
FORBIDDEN_IN_TAG = "::"


@dataclass(frozen=True)
class Repo:
    path: Path
    tag: str
    enabled: bool


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str

    def redacted(self) -> dict[str, str]:
        """Safe to log."""
        return {
            "uri": self.neo4j_uri,
            "username": self.neo4j_username,
            "database": self.neo4j_database,
            "password": "***",
        }


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def load_settings(env_path: Path) -> Settings:
    if not env_path.exists():
        raise FileNotFoundError(f"no .env at {env_path}")
    values = _parse_env(env_path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_ENV_KEYS if not values.get(k)]
    if missing:
        # Name only the missing keys. Never echo any value.
        raise ValueError(f"{env_path} is missing required key(s): {', '.join(missing)}")
    return Settings(
        neo4j_uri=values["NEO4J_URI"],
        neo4j_username=values["NEO4J_USERNAME"],
        neo4j_password=values["NEO4J_PASSWORD"],
        neo4j_database=values["NEO4J_DATABASE"],
    )


def load_repos(yaml_path: Path) -> list[Repo]:
    if not yaml_path.exists():
        raise FileNotFoundError(f"no repos.yaml at {yaml_path}")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    entries = data.get("repos") or []

    repos: list[Repo] = []
    seen: set[str] = set()
    for entry in entries:
        tag = str(entry["tag"])
        if FORBIDDEN_IN_TAG in tag:
            raise ValueError(f"tag {tag!r} must not contain {FORBIDDEN_IN_TAG!r}")
        if tag in seen:
            raise ValueError(f"duplicate tag {tag!r} in {yaml_path}")
        seen.add(tag)
        repos.append(
            Repo(
                path=Path(str(entry["path"])).expanduser(),
                tag=tag,
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return [r for r in repos if r.enabled]
