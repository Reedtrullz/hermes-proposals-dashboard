import os
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

KANBAN_DB = Path(os.environ.get("HERMES_KANBAN_DB", Path.home() / ".hermes" / "kanban.db"))
COLUMNS = ["todo", "ready", "in_progress", "blocked", "done", "archived"]
STATUS_LABELS = {
    "todo": "Todo", "ready": "Ready", "in_progress": "In Progress",
    "blocked": "Blocked", "done": "Done", "archived": "Archived",
}

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["COLUMNS"] = COLUMNS


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(KANBAN_DB))
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


app = FastAPI(title="Hermes Kanban Dashboard")


# ── Page routes ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    db = get_db()
    try:
        tasks_by_status = {}
        for col in COLUMNS:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at DESC",
                (col,),
            ).fetchall()
            tasks_by_status[col] = [task_row(r) for r in rows]
        assignees = [
            r[0] for r in db.execute(
                "SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL AND assignee != ''"
            ).fetchall()
        ]
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"tasks_by_status": tasks_by_status, "assignees": assignees},
    )


@app.get("/api/board", response_class=HTMLResponse)
async def board_fragment(request: Request):
    db = get_db()
    try:
        tasks_by_status = {}
        for col in COLUMNS:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at DESC",
                (col,),
            ).fetchall()
            tasks_by_status[col] = [task_row(r) for r in rows]
        assignees = [
            r[0] for r in db.execute(
                "SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL AND assignee != ''"
            ).fetchall()
        ]
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"tasks_by_status": tasks_by_status, "assignees": assignees},
    )


# ── REST API ─────────────────────────────────────────────────

@app.get("/api/tasks")
async def list_tasks(status: str | None = None):
    db = get_db()
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
async def get_task(task_id: str):
    db = get_db()
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
):
    task_id = f"t_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    db = get_db()
    try:
        db.execute(
            """INSERT INTO tasks (id, title, body, assignee, status, priority, created_at)
               VALUES (?, ?, ?, ?, 'todo', ?, ?)""",
            (task_id, title, body, assignee, priority, now),
        )
        db.commit()
    finally:
        db.close()
    return await _task_card_html(task_id)


@app.patch("/api/tasks/{task_id}/status")
async def update_status(task_id: str, status: str = Form(...)):
    if status not in COLUMNS:
        return JSONResponse({"error": f"invalid status: {status}"}, status_code=400)
    db = get_db()
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
    return await _task_card_html(task_id)


@app.patch("/api/tasks/{task_id}")
async def update_task(
    task_id: str,
    title: str = Form(None),
    body: str = Form(None),
    assignee: str = Form(None),
    priority: int = Form(None),
):
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return JSONResponse({"error": "not found"}, status_code=404)
        updates = {}
        if title is not None: updates["title"] = title
        if body is not None: updates["body"] = body
        if assignee is not None: updates["assignee"] = assignee
        if priority is not None: updates["priority"] = priority
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*updates.values(), task_id))
            db.commit()
    finally:
        db.close()
    return await _task_card_html(task_id)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    db = get_db()
    try:
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        db.execute("DELETE FROM task_links WHERE parent_id = ? OR child_id = ?", (task_id, task_id))
        db.execute("DELETE FROM task_comments WHERE task_id = ?", (task_id,))
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/comments")
async def add_comment(task_id: str, body: str = Form(...), author: str = Form("web")):
    now = int(time.time())
    db = get_db()
    try:
        db.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (task_id, author, body, now),
        )
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# ── HTML fragments ───────────────────────────────────────────

@app.get("/api/tasks/{task_id}/html", response_class=HTMLResponse)
async def task_html(task_id: str, request: Request):
    return await _task_card_html(task_id)


async def _task_card_html(task_id: str):
    db = get_db()
    try:
        task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return HTMLResponse("", status_code=404)
        t = task_row(task)
        # Build a minimal request-like scope for the template
        from fastapi import Request as _Request
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        req = _Request(scope)
        return templates.TemplateResponse(
            request=req, name="_task_card.html",
            context={"task": t},
        )
    finally:
        db.close()
