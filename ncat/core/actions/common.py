"""Shared helpers for core use-case actions.

Actions are orchestration-level operations shared by the CLI and HTTP API.
They may write project files and spawn media processes, but they must not
format terminal output or import ``argparse``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..assets.termbase import read_terms
from ..assets.tm import read_units
from ..project import ProjectState
from ..runs import fail_run, new_run, save_run
from ..workspace import effective_terms, effective_tm, find_workspace_for


class ActionError(Exception):
    """A use-case failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *,
                 fields: dict[str, Any] | None = None,
                 exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields
        self.exit_code = exit_code


def parse_options(values: list[str] | None) -> dict[str, Any]:
    """Parse repeatable ``KEY=VALUE`` backend options, JSON-coercing values."""
    options: dict[str, Any] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ActionError(
                "invalid_argument",
                f"--option expects KEY=VALUE, got {raw!r}",
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ActionError(
                "invalid_argument", f"--option has an empty key: {raw!r}")
        try:
            options[key] = json.loads(value)
        except json.JSONDecodeError:
            options[key] = value
    return options


def begin_run(state: ProjectState, recipe: str):
    run = new_run(state.project.id, recipe=recipe)
    save_run(state.paths, run)
    return run


def fail_run_action(state: ProjectState, run, error: Exception) -> None:
    fail_run(run, str(error))
    save_run(state.paths, run)


def local_assets(state: ProjectState) -> tuple[list, list]:
    terms_path = state.paths.assets / "terms.jsonl"
    tm_path = state.paths.assets / "tm.jsonl"
    return read_terms(terms_path), read_units(tm_path)


def project_terms_tm(state: ProjectState) -> tuple[list, list]:
    """Resolve effective terms/TM from the workspace, falling back to local."""
    workspace = find_workspace_for(state.paths.root)
    if workspace is not None:
        for subproject in workspace.subprojects:
            if workspace.subproject_root(subproject) == state.paths.root:
                return (effective_terms(workspace, subproject),
                        effective_tm(workspace, subproject))
    return local_assets(state)


def slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "-"
                   for char in value).strip("-") or "subtitle"
