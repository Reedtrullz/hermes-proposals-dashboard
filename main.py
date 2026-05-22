import os, sqlite3, time, uuid, json
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
PROPOSALS_DB = HERMES_HOME / "proposals.db"
TRIGGER_FILE = HERMES_HOME / "proposals_trigger"
PROFILES_DIR = HERMES_HOME / "profiles"

PROPOSAL_STATUSES = ["processing", "review", "approved", "implemented", "rejected"]
PROPOSAL_LABELS = {
    "processing": "Analyzing...", "review": "In Review", "approved": "Approved",
    "implemented": "Done", "rejected": "Rejected",
}

AGENT_EMOJI = {"orchestrator": "🧭", "coder": "⚡", "researcher": "🔍", "reviewer": "🛡️"}
AGENT_COLORS = {"orchestrator": "#58a6ff", "coder": "#3fb950", "researcher": "#d2991d", "reviewer": "#a371f7"}

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["PROPOSAL_LABELS"] = PROPOSAL_LABELS
templates.env.globals["AGENT_EMOJI"] = AGENT_EMOJI
templates.env.globals["AGENT_COLORS"] = AGENT_COLORS


def _emoji(author): return AGENT_EMOJI.get(author, "💬")
def _color(author): return AGENT_COLORS.get(author, "var(--muted)")


def get_profiles() -> list[str]:
    if PROFILES_DIR.is_dir():
        return sorted(d.name for d in PROFILES_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists())
    return []


def _init_db():
    db = sqlite3.connect(str(PROPOSALS_DB))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'processing', author TEXT NOT NULL DEFAULT 'user', board TEXT NOT NULL DEFAULT 'default', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS proposal_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL, parent_id INTEGER, created_at INTEGER NOT NULL)")
    try: db.execute("ALTER TABLE proposal_comments ADD COLUMN parent_id INTEGER REFERENCES proposal_comments(id)")
    except: pass
    db.execute("CREATE INDEX IF NOT EXISTS idx_prop_status ON proposals(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_pc_proposal ON proposal_comments(proposal_id, created_at)")
    db.commit(); db.close()
_init_db()

AUTH_URL = os.environ.get("AUTH_URL", "https://reidar.tech")

# ── Auth middleware ───────────────────────────────────────

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
import http.client, urllib.parse

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/api/"):
            # API routes: check session token exists
            token = request.cookies.get("__Secure-next-auth.session-token") or request.cookies.get("next-auth.session-token")
            if not token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        else:
            # Page routes: redirect to signin if no token
            token = request.cookies.get("__Secure-next-auth.session-token") or request.cookies.get("next-auth.session-token")
            if not token:
                return RedirectResponse(f"{AUTH_URL}/api/auth/signin")
        return await call_next(request)

app = FastAPI(title="Hermes Proposals")
app.add_middleware(AuthMiddleware)


# ═══════════════════════════════════════════════════════════
#  PAGES
# ═══════════════════════════════════════════════════════════

@app.get("/", response_class=RedirectResponse)
async def root(): return RedirectResponse("/proposals", status_code=302)


@app.get("/proposals", response_class=HTMLResponse)
async def proposals_list(request: Request):
    db = sqlite3.connect(str(PROPOSALS_DB)); db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT p.*, (SELECT COUNT(*) FROM proposal_comments WHERE proposal_id=p.id AND parent_id IS NULL) as top_comments FROM proposals p ORDER BY p.updated_at DESC LIMIT 50").fetchall()
    finally: db.close()
    return templates.TemplateResponse(request=request, name="proposals_list.html", context={"proposals": [dict(r) for r in rows], "profiles": get_profiles()})


@app.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def proposal_detail(request: Request, proposal_id: str):
    db = sqlite3.connect(str(PROPOSALS_DB)); db.row_factory = sqlite3.Row
    try:
        p = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not p: return HTMLResponse("<h2>Not found</h2>", status_code=404)
        comments = db.execute("SELECT * FROM proposal_comments WHERE proposal_id=? ORDER BY created_at ASC", (proposal_id,)).fetchall()
    finally: db.close()
    return templates.TemplateResponse(request=request, name="proposal_detail.html", context={"proposal": dict(p), "comments": [dict(c) for c in comments], "profiles": get_profiles()})


@app.get("/api/proposals/{proposal_id}/fragment", response_class=HTMLResponse)
async def proposal_fragment(request: Request, proposal_id: str):
    db = sqlite3.connect(str(PROPOSALS_DB)); db.row_factory = sqlite3.Row
    try:
        p = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not p: return HTMLResponse("", status_code=404)
        comments = db.execute("SELECT * FROM proposal_comments WHERE proposal_id=? ORDER BY created_at ASC", (proposal_id,)).fetchall()
    finally: db.close()
    return templates.TemplateResponse(request=request, name="_proposal_detail_fragment.html", context={"proposal": dict(p), "comments": [dict(c) for c in comments], "profiles": get_profiles()})


# ═══════════════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════════════

@app.post("/api/proposals")
async def create_proposal(title: str = Form(...), body: str = Form(""), board: str = Form("default")):
    pid = f"p_{uuid.uuid4().hex[:10]}"; now = int(time.time())
    db = sqlite3.connect(str(PROPOSALS_DB))
    try:
        db.execute("INSERT INTO proposals (id,title,body,status,board,created_at,updated_at) VALUES (?,?,?,'processing',?,?,?)", (pid, title, body, board, now, now))
        db.commit()
    finally: db.close()
    # Write trigger file so Hermes knows to review immediately
    TRIGGER_FILE.write_text(pid)
    return {"ok": True, "id": pid}


@app.patch("/api/proposals/{proposal_id}/status")
async def update_proposal_status(proposal_id: str, status: str = Form(...)):
    if status not in PROPOSAL_STATUSES:
        return JSONResponse({"error": f"invalid status: {status}"}, status_code=400)
    now = int(time.time())
    db = sqlite3.connect(str(PROPOSALS_DB))
    try:
        db.execute("UPDATE proposals SET status=?, updated_at=? WHERE id=?", (status, now, proposal_id))
        db.commit()
    finally: db.close()
    # If approved, trigger execution via trigger file
    if status == "approved":
        TRIGGER_FILE.write_text(f"APPROVED:{proposal_id}")
    return {"ok": True}


@app.post("/api/proposals/{proposal_id}/comments")
async def add_proposal_comment(proposal_id: str, body: str = Form(...), author: str = Form("agent"), parent_id: int = Form(None)):
    now = int(time.time())
    db = sqlite3.connect(str(PROPOSALS_DB))
    try:
        db.execute("INSERT INTO proposal_comments (proposal_id,author,body,parent_id,created_at) VALUES (?,?,?,?,?)", (proposal_id, author, body, parent_id, now))
        db.execute("UPDATE proposals SET updated_at=? WHERE id=?", (now, proposal_id))
        db.commit()
    finally: db.close()
    return {"ok": True}


@app.get("/api/proposals")
async def api_proposals_list():
    db = sqlite3.connect(str(PROPOSALS_DB)); db.row_factory = sqlite3.Row
    try: return [dict(r) for r in db.execute("SELECT * FROM proposals ORDER BY updated_at DESC LIMIT 50").fetchall()]
    finally: db.close()


@app.get("/api/proposals/{proposal_id}")
async def api_proposal_detail(proposal_id: str):
    db = sqlite3.connect(str(PROPOSALS_DB)); db.row_factory = sqlite3.Row
    try:
        p = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not p: return JSONResponse({"error": "not found"}, status_code=404)
        comments = db.execute("SELECT * FROM proposal_comments WHERE proposal_id=? ORDER BY created_at ASC", (proposal_id,)).fetchall()
        result = dict(p); result["comments"] = [dict(c) for c in comments]
        return result
    finally: db.close()
