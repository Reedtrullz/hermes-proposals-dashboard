from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any


def projectos_root(project_root: Path | None = None) -> Path:
    """Return the project root used for git-state checks."""
    if project_root is not None:
        return Path(project_root).expanduser()

    configured_root = os.environ.get("PROJECTOS_ROOT")
    if configured_root:
        return Path(configured_root).expanduser()

    return Path.home() / "Projectos"


def _project_git_snapshot(name: str, project_root: Path | None = None) -> dict[str, Any]:
    """Return quick git state for a project directory. Empty dict if not a git repo."""
    proj_dir = projectos_root(project_root) / name
    if not (proj_dir / ".git").is_dir():
        return {}

    def _git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(proj_dir), *args],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except Exception:
            return ""

    return {
        "dirty": bool(_git("status", "--porcelain")),
        "last_commit_ts": _git("log", "-1", "--format=%ct"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "unpushed": bool(_git("log", "@{u}..", "--oneline")),
    }


def _git_recommendations(
    project_name: str,
    recommendations: list[dict[str, str]],
    project_root: Path | None = None,
) -> None:
    """Append git-state recommendations if any are actionable."""
    if len(recommendations) >= 3:
        return

    snap = _project_git_snapshot(project_name, project_root=project_root)
    if not snap:
        return

    if snap.get("dirty"):
        recommendations.append({
            "kind": "git_dirty",
            "title": "Uncommitted changes in ~/Projectos/" + project_name,
            "body": "Finish up and push before context-switching — uncommitted work rots fast.",
            "href": "",
            "action": "Commit & push",
        })
        return

    if snap.get("unpushed"):
        recommendations.append({
            "kind": "git_unpushed",
            "title": "Commits not pushed to remote",
            "body": f"Branch '{snap.get('branch', '?')}' has unpushed commits. Push before switching projects.",
            "href": "",
            "action": "Push now",
        })
        return

    last_ts = snap.get("last_commit_ts")
    if last_ts:
        try:
            age_days = (int(time.time()) - int(last_ts)) // 86400
            if age_days > 14:
                recommendations.append({
                    "kind": "git_stale",
                    "title": f"No commits in {age_days} days",
                    "body": "Is this project stalled? Consider archiving it or picking it back up with a small win.",
                    "href": f"/proposals/projects/project_{project_name.lower().replace('-', '_').replace(' ', '_')}#new-project-proposal",
                    "action": "Create proposal",
                })
        except (ValueError, TypeError):
            pass
