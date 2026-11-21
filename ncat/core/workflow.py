"""Project board aggregation for categories and lanes.

Lanes are user-defined workflow columns. There is intentionally no artifact
based stage derivation: cards move by manual drag or project automation rules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .activity import log_activity
from .project import now_iso, read_cues, read_segments
from .workspace import (
    Subproject, WorkspaceProject, category_of, lane_of, save_workspace,
    subproject_state,
)


def card(project: WorkspaceProject, sub: Subproject, state=None) -> dict[str, Any]:
    if state is None:
        state = subproject_state(project, sub)
    paths = state.paths
    segments = read_segments(paths) if paths.segments.is_file() else []
    cues = read_cues(paths) if paths.cues.is_file() else []
    subtasks = sub.subtasks or []
    completed = sum(1 for item in subtasks if item.done)
    return {
        "project_id": project.id,
        "project_name": project.name,
        "subproject_id": sub.id,
        "name": sub.name,
        "kind": sub.kind,
        "category_id": sub.category_id,
        "lane_id": sub.lane_id,
        "description": sub.description,
        "due": sub.due,
        "done": sub.done,
        "completed_at": sub.completed_at,
        "subtasks": {
            "total": len(subtasks),
            "done": completed,
        },
        "counts": {"segments": len(segments), "cues": len(cues)},
        "updated_at": sub.updated_at,
        "created_at": sub.created_at,
        "root": str(project.subproject_root(sub)),
    }


def board(project: WorkspaceProject, category_id: str | None = None) -> dict[str, Any]:
    categories = []
    selected = [item for item in project.categories
                if not category_id or item.id == category_id]
    selected.sort(key=lambda item: item.order)
    for category in selected:
        lanes = []
        lane_items = sorted(category.lanes, key=lambda item: item.order)
        for lane in lane_items:
            cards = []
            for sub in project.subprojects:
                if sub.category_id != category.id or sub.lane_id != lane.id:
                    continue
                try:
                    state = subproject_state(project, sub)
                except Exception:
                    state = None
                cards.append(card(project, sub, state))
            lanes.append({
                "id": lane.id,
                "name": lane.name,
                "order": lane.order,
                "cards": cards,
            })
        categories.append({
            "id": category.id,
            "name": category.name,
            "order": category.order,
            "lanes": lanes,
        })
    return {
        "project_id": project.id,
        "project_name": project.name,
        "generated_at": now_iso(),
        "categories": categories,
        "subproject_count": len(project.subprojects),
    }


def move_card(project: WorkspaceProject, sub: Subproject,
              category_id: str, lane_id: str) -> dict[str, Any]:
    category = category_of(project, category_id)
    lane = lane_of(project, category, lane_id)
    previous = (sub.category_id, sub.lane_id)
    sub.category_id = category.id
    sub.lane_id = lane.id
    sub.updated_at = now_iso()
    save_workspace(project)
    if previous != (category.id, lane.id):
        log_activity(
            project, "subproject_moved",
            subproject_id=sub.id, name=sub.name,
            from_category_id=previous[0], from_lane_id=previous[1],
            to_category_id=category.id, to_lane_id=lane.id,
            category_id=category.id, lane_id=lane.id,
        )
    return card(project, sub)


def apply_subtask_automation(project: WorkspaceProject,
                             sub: Subproject, subtask_title: str) -> list[dict]:
    """Move a subproject for matching enabled rules and return applied rules."""
    applied = []
    for rule in sorted(project.automation, key=lambda item: item.order):
        if not rule.enabled or rule.subtask_title.strip().lower() != subtask_title.strip().lower():
            continue
        if rule.category_scope_id and rule.category_scope_id != sub.category_id:
            continue
        try:
            category = category_of(project, rule.target_category_id)
            lane = lane_of(project, category, rule.target_lane_id)
        except FileNotFoundError:
            continue
        if sub.category_id == category.id and sub.lane_id == lane.id:
            continue
        previous = (sub.category_id, sub.lane_id)
        sub.category_id = category.id
        sub.lane_id = lane.id
        sub.updated_at = now_iso()
        log_activity(
            project, "subproject_moved",
            subproject_id=sub.id, name=sub.name,
            from_category_id=previous[0], from_lane_id=previous[1],
            to_category_id=category.id, to_lane_id=lane.id,
            category_id=category.id, lane_id=lane.id,
            automation_rule_id=rule.id,
        )
        applied.append({"rule_id": rule.id, "category_id": category.id,
                        "lane_id": lane.id})
    if applied:
        save_workspace(project)
    return applied
