import os
import subprocess

import project_git_state


def _git_env(extra=None):
    """Return a deterministic git environment that ignores user/global config."""
    git_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_CONFIG_")
    }
    git_env["GIT_CONFIG_NOSYSTEM"] = "1"
    git_env["GIT_CONFIG_GLOBAL"] = os.devnull
    git_env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        git_env.update(extra)
    return git_env


def _run_git(repo, *args, env=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(env),
    )


def _init_repo(project_root, name, commit_ts=1_700_000_000):
    repo = project_root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True, env=_git_env())
    (repo / "README.md").write_text("initial\n")
    commit_env = {
        "GIT_AUTHOR_DATE": f"{commit_ts} +0000",
        "GIT_COMMITTER_DATE": f"{commit_ts} +0000",
    }
    _run_git(repo, "add", "README.md")
    _run_git(
        repo,
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "initial",
        env=commit_env,
    )
    return repo


def test_project_git_snapshot_uses_projectos_root_env_without_importing_main(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTOS_ROOT", str(tmp_path))
    _init_repo(tmp_path, "env-project")

    snapshot = project_git_state._project_git_snapshot("env-project")

    assert snapshot["dirty"] is False
    assert snapshot["last_commit_ts"].isdigit()
    assert snapshot["branch"]
    assert snapshot["unpushed"] is False


def test_project_git_snapshot_returns_empty_for_non_git_project(tmp_path):
    (tmp_path / "plain-project").mkdir()

    assert project_git_state._project_git_snapshot("plain-project", project_root=tmp_path) == {}


def test_git_recommendations_prefers_dirty_worktree(tmp_path):
    repo = _init_repo(tmp_path, "dirty-project")
    (repo / "README.md").write_text("changed\n")
    recommendations = []

    project_git_state._git_recommendations("dirty-project", recommendations, project_root=tmp_path)

    assert recommendations == [
        {
            "kind": "git_dirty",
            "title": "Uncommitted changes in ~/Projectos/dirty-project",
            "body": "Finish up and push before context-switching — uncommitted work rots fast.",
            "href": "",
            "action": "Commit & push",
        }
    ]


def test_git_recommendations_flags_unpushed_commits(tmp_path):
    repo = _init_repo(tmp_path, "unpushed-project")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True, env=_git_env())
    _run_git(repo, "remote", "add", "origin", str(remote))
    _run_git(repo, "push", "-u", "origin", "HEAD")
    (repo / "README.md").write_text("local commit\n")
    _run_git(repo, "add", "README.md")
    _run_git(
        repo,
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "local commit",
    )
    branch = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    recommendations = []

    project_git_state._git_recommendations("unpushed-project", recommendations, project_root=tmp_path)

    assert recommendations == [
        {
            "kind": "git_unpushed",
            "title": "Commits not pushed to remote",
            "body": f"Branch '{branch}' has unpushed commits. Push before switching projects.",
            "href": "",
            "action": "Push now",
        }
    ]


def test_git_recommendations_flags_stale_clean_project(tmp_path, monkeypatch):
    commit_ts = 1_700_000_000
    _init_repo(tmp_path, "stale-project", commit_ts=commit_ts)
    monkeypatch.setattr(project_git_state.time, "time", lambda: commit_ts + 15 * 86400)
    recommendations = []

    project_git_state._git_recommendations("stale-project", recommendations, project_root=tmp_path)

    assert recommendations == [
        {
            "kind": "git_stale",
            "title": "No commits in 15 days",
            "body": "Is this project stalled? Consider archiving it or picking it back up with a small win.",
            "href": "/proposals/projects/project_stale_project#new-project-proposal",
            "action": "Create proposal",
        }
    ]


def test_git_recommendations_does_not_probe_when_recommendation_slots_are_full(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called when recommendations are full")

    monkeypatch.setattr(project_git_state.subprocess, "run", fail_if_called)
    recommendations = [{"kind": "one"}, {"kind": "two"}, {"kind": "three"}]

    project_git_state._git_recommendations("any-project", recommendations, project_root=tmp_path)

    assert recommendations == [{"kind": "one"}, {"kind": "two"}, {"kind": "three"}]
