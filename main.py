import os
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
KANBAN_DIR = HERMES_HOME / "kanban" / "boards"
DEFAULT_DB = HERMES_HOME / "kanban.db"
PROFILES_DIR = HERMES_HOME / "profiles"

COLUMNS = ["todo", "ready", "in_progress", "blocked", "done", "archived"]
STATUS_LABELS = {
    "todo": "Todo", "ready": "Ready", "in_progress": "In Progress",
    "blocked": "Blocked", "done": "Done", "archived": "Archived",
}

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["COLUMNS"] = COLUMNS


def get_db_path(board: str) -> Path:
    """Resolve the kanban.db path for a given board slug."""
    if board == "default":
        return DEFAULT_DB
    return KANBAN_DIR / board / "kanban.db"


def list_boards() -> list[dict]:
    """List all available boards with their DB paths."""
    boards = [{"slug": "default", "name": "Default", "db": str(DEFAULT_DB)}]
    if KANBAN_DIR.is_dir():
        for d in sorted(KANBAN_DIR.iterdir()):
            if d.is_dir() and (d / "kanban.db").exists():
                slug = d.name
                # Try to get display name from hermes CLI, fall back to slug
                boards.append({"slug": slug, "name": slug.replace("-", " ").title(), "db": str(d / "kanban.db")})
    return boards


def get_assignees() -> list[str]:
    """Get available assignees: Hermes profiles + existing task assignees."""
    assignees = []
    if PROFILES_DIR.is_dir():
        assignees = sorted(
            d.name for d in PROFILES_DIR.iterdir()
            if d.is_dir() and (d / "config.yaml").exists()
        )
    return assignees


def get_db(board: str = "default") -> sqlite3.Connection:
    db_path = str(get_db_path(board))
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def task_row(task) -> dict:
    return {
        "id": task["id"],
        "title": task["title"],
        "body": task["body"] or "",
        "assignee": task["assignee"] or "",
        "status": task["status"],
        "priority": task["priority"] or 0,
        "created_at": task["created_at"],
        "started_at": task["started_at"],
        "completed_at": task["completed_at"],
        "tenant": task["tenant"] or "",
        "consecutive_failures": task["consecutive_failures"] or 0,
        "current_run_id": task["current_run_id"],
        "skills": task["skills"],
    }


def get_board_context(db, board: str):
    """Fetch tasks and assignees for a board."""
    tasks_by_status = {}
    for col in COLUMNS:
        rows = db.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at DESC",
            (col,),
        ).fetchall()
        tasks_by_status[col] = [task_row(r) for r in rows]
    # Also include assignees from existing tasks
    profile_assignees = get_assignees()
    db_assignees = [
        r[0] for r in db.execute(
            "SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL AND assignee != ''"
        ).fetchall()
    ]
    all_assignees = sorted(set(profile_assignees + db_assignees))
    return tasks_by_status, all_assignees


app = FastAPI(title="Hermes Kanban Dashboard")

# ── Redirect root to default board ─────────────────────────

@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse("/board/default", status_code=302)


# ── Board routes ───────────────────────────────────────────

@app.get("/board/{board}", response_class=HTMLResponse)
async def board_view(request: Request, board: str, fragment: bool = False):
    db_path = get_db_path(board)
    if not db_path.exists():
        return HTMLResponse(f"<h2>Board '{board}' not found</h2>", status_code=404)
    db = get_db(board)
    try:
        tasks_by_status, assignees = get_board_context(db, board)
    finally:
        db.close()
    boards = list_boards()
    if fragment:
        return templates.TemplateResponse(
            request=request, name="_board.html",
            context={"board": board, "boards": boards, "tasks_by_status": tasks_by_status, "assignees": assignees},
        )
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"board": board, "boards": boards, "tasks_by_status": tasks_by_status, "assignees": assignees},
    )


# ── REST API ───────────────────────────────────────────────

@app.get("/api/boards")
async def api_list_boards():
    return list_boards()


@app.get("/api/tasks")
async def list_tasks(status: str | None = None, board: str = "default"):
    db = get_db(board)
    try:
        if status:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM tasks ORDER BY priority DESC, created_at DESC"
            ).fetchall()
        return [task_row(r) for r in rows]
    finally:
        db.close()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, board: str = "default"):
    db = get_db(board)
    try:
        task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return JSONResponse({"error": "not found"}, status_code=404)
        comments = db.execute(
            "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        runs = db.execute(
            "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at DESC",
            (task_id,),
        ).fetchall()
        parents = [
            r[0] for r in db.execute(
                "SELECT parent_id FROM task_links WHERE child_id = ?", (task_id,)
            ).fetchall()
        ]
        children = [
            r[0] for r in db.execute(
                "SELECT child_id FROM task_links WHERE parent_id = ?", (task_id,)
            ).fetchall()
        ]
        result = task_row(task)
        result["comments"] = [dict(c) for c in comments]
        result["runs"] = [dict(r) for r in runs]
        result["parents"] = parents
        result["children"] = children
        return result
    finally:
        db.close()


@app.post("/api/tasks")
async def create_task(
    title: str = Form(...),
    body: str = Form(""),
    assignee: str = Form(""),
    priority: int = Form(0),
    board: str = Form("default"),
):
    task_id = f"t_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    db = get_db(board)
    try:
        db.execute(
            """INSERT INTO tasks (id, title, body, assignee, status, priority, created_at)
               VALUES (?, ?, ?, ?, 'todo', ?, ?)""",
            (task_id, title, body, assignee, priority, now),
        )
        db.commit()
    finally:
        db.close()
    return await _task_card_html(task_id, board)


@app.patch("/api/tasks/{task_id}/status")
async def update_status(task_id: str, status: str = Form(...), board: str = Form("default")):
    if status not in COLUMNS:
        return JSONResponse({"error": f"invalid status: {status}"}, status_code=400)
    db = get_db(board)
    try:
        now = int(time.time())
        db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        if status == "in_progress":
            db.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (now, task_id))
        elif status == "done":
            db.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (now, task_id))
        db.commit()
    finally:
        db.close()
    return await _task_card_html(task_id, board)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, board: str = "default"):
    db = get_db(board)
    try:
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        db.execute("DELETE FROM task_links WHERE parent_id = ? OR child_id = ?", (task_id, task_id))
        db.execute("DELETE FROM task_comments WHERE task_id = ?", (task_id,))
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/comments")
async def add_comment(task_id: str, body: str = Form(...), author: str = Form("web"), board: str = Form("default")):
    now = int(time.time())
    db = get_db(board)
    try:
        db.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (task_id, author, body, now),
        )
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# ── HTML fragments ─────────────────────────────────────────

async def _task_card_html(task_id: str, board: str):
    db = get_db(board)
    try:
        task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return HTMLResponse("", status_code=404)
        t = task_row(task)
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        from fastapi import Request as _Request
        req = _Request(scope)
        return templates.TemplateResponse(
            request=req, name="_task_card.html",
            context={"task": t, "board": board},
        )
    finally:
        db.close()
