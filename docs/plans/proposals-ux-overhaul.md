# Proposals UX Overhaul — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make proposals system actually work — instant agent feedback, real-time UI, reliable execution loop.

**Architecture:** Replace cron-job polling with live session detection. Add SSE/auto-refresh to UI. Add relative timestamps and visual polish.

**Tech Stack:** FastAPI, htmx, SQLite, Jinja2

---

## PHASE 1: Close the loop — instant agent feedback

### Task 1: Add `processing` status + API endpoint

**Objective:** New proposals start as `processing` with a visual indicator while agents work.

**Files:**
- Modify: `main.py` — add `processing` to PROPOSAL_STATUSES
- Modify: `proposals_list.html` — badge style for processing
- Modify: `proposal_detail.html` — processing indicator

**Step 1:** Add status to backend

In `main.py`, change:
```python
PROPOSAL_STATUSES = ["processing", "draft", "review", "approved", "implemented", "rejected"]
PROPOSAL_LABELS = {
    "processing": "Analyzing...", "draft": "Draft", ...
}
```

New proposals start as `processing` instead of `draft`. The `draft` status is removed — proposals go straight to processing.

**Step 2:** Add processing badge style

In `proposals_list.html` and `proposal_detail.html` CSS:
```css
.badge-processing { background:#1a2332; color:var(--accent); animation:pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
```

**Step 3:** Show processing state on detail page

In `proposal_detail.html`, when status is processing, show spinner above comments:
```html
{% if proposal.status == 'processing' %}
<div class="processing-indicator">
  <span class="spinner"></span> Agents are analyzing your proposal...
</div>
{% endif %}
```

**Verification:** Create proposal → see "Analyzing..." badge with pulse animation. curl API to confirm status is `processing`.

---

### Task 2: Add auto-refresh polling to proposal detail page

**Objective:** Proposal detail page auto-refreshes comments every 5 seconds so agent responses appear without manual reload.

**Files:**
- Modify: `proposal_detail.html`

**Step 1:** Add htmx poll trigger to the comments section

Change the comments section to auto-refresh. Wrap it in a div that polls:
```html
<div hx-get="/api/proposals/{{ proposal.id }}/fragment" hx-trigger="every 5s" hx-swap="outerHTML">
  <!-- comments content -->
</div>
```

**Step 2:** Create fragment endpoint

In `main.py`, add:
```python
@app.get("/api/proposals/{proposal_id}/fragment", response_class=HTMLResponse)
async def proposal_fragment(request: Request, proposal_id: str):
    db = sqlite3.connect(str(PROPOSALS_DB)); db.row_factory = sqlite3.Row
    try:
        p = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not p: return HTMLResponse("", status_code=404)
        comments = db.execute("SELECT * FROM proposal_comments WHERE proposal_id=? ORDER BY created_at ASC", (proposal_id,)).fetchall()
    finally: db.close()
    return templates.TemplateResponse(request=request, name="_proposal_comments.html", context={"proposal": dict(p), "comments": [dict(c) for c in comments]})
```

**Step 3:** Create `_proposal_comments.html` partial

Extract the comments section HTML into a partial template that's returned by the fragment endpoint.

**Verification:** Open proposal detail, post a comment from another terminal via curl, see it appear within 5 seconds without refresh.

---

### Task 3: Add notification endpoint that triggers agent review

**Objective:** When a proposal is created, the API calls back to Hermes to trigger immediate agent review instead of waiting for cron.

**Files:**
- Modify: `main.py` — add webhook-style notification on proposal creation

**Step 1:** After creating a proposal, write a trigger file

In `create_proposal()`:
```python
# After inserting proposal, write a trigger file
trigger_file = HERMES_HOME / "proposals_trigger"
trigger_file.write_text(pid)
```

**Step 2:** When I (Hermes) see the trigger file, I immediately review

This is a convention: after creating a proposal, the dashboard writes to `~/.hermes/proposals_trigger`. I check for this file at the start of each response. If present, I read the proposal ID, review it with agents, and delete the trigger file.

**Verification:** Create proposal → trigger file appears → I detect it → agents comment → file deleted.

---

## PHASE 2: UI/UX polish

### Task 4: Relative timestamps

**Objective:** Show "2 minutes ago" instead of "1779409851".

**Files:**
- Modify: `proposals_list.html` — replace `{{ p.created_at|int }}` 
- Modify: `proposal_detail.html` — replace `{{ c.created_at|int }}`

**Step 1:** Add JavaScript relative time formatter

Add to both templates:
```html
<script>
function relativeTime(ts) {
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}
document.querySelectorAll('.reltime').forEach(el => {
  el.textContent = relativeTime(parseInt(el.dataset.ts));
});
</script>
```

**Step 2:** Replace timestamp display

Change `<span>{{ p.created_at|int }}</span>` to:
```html
<span class="reltime" data-ts="{{ p.created_at }}">{{ p.created_at }}</span>
```

**Verification:** Timestamps show "just now", "5m ago", "2h ago" etc.

---

### Task 5: Visual agent identity

**Objective:** Agent comments are visually distinct with colored borders and emoji avatars.

**Files:**
- Modify: `proposal_detail.html` and `_proposal_comments.html`

**Step 1:** Add agent color mapping

```html
<script>
const AGENT_COLORS = {orchestrator:'#58a6ff', coder:'#3fb950', researcher:'#d2991d', reviewer:'#a371f7'};
document.querySelectorAll('.comment').forEach(c => {
  const author = c.querySelector('.author').textContent.trim();
  if (AGENT_COLORS[author]) c.style.borderLeft = '3px solid ' + AGENT_COLORS[author];
});
</script>
```

**Step 2:** Add emoji to agent names

In the comment display, add emoji prefix based on author:
```html
<div class="author">
  {% if c.author == 'orchestrator' %}🧭{% elif c.author == 'coder' %}⚡{% elif c.author == 'researcher' %}🔍{% elif c.author == 'reviewer' %}🛡️{% endif %}
  {{ c.author }}
</div>
```

**Verification:** Each agent's comments have a distinct left-border color and emoji.

---

### Task 6: Markdown rendering in comments

**Objective:** Agent comments support basic formatting (bold, lists, code).

**Files:**
- Modify: `proposal_detail.html` — add markdown renderer

**Step 1:** Add simple markdown-to-HTML converter in JavaScript

```html
<script>
function mdToHtml(text) {
  return text
    .replace(/`([^`]+)`/g, '<code style="background:var(--bg);padding:1px 5px;border-radius:3px;font-size:11px">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n- (.+)/g, '\n<li style="margin-left:16px">$1</li>')
    .replace(/\n\n/g, '<br><br>');
}
</script>
```

**Step 2:** Apply to comment bodies

Change `<div class="body">{{ c.body }}</div>` to use innerHTML with rendered markdown.

**Verification:** Comments with `**bold**`, `- lists`, and `` `code` `` render correctly.

---

## PHASE 3: Reliability

### Task 7: Remove cron jobs, use in-session detection

**Objective:** Stop relying on cron jobs. I (Hermes) detect new proposals during our conversation.

**Files:**
- Delete: cron jobs `0d4226e9ff7a` and `128a96f56ad0`
- Add: Convention — at start of each response, check `~/.hermes/proposals_trigger`

**Step 1:** Delete cron jobs

```bash
hermes cron remove 0d4226e9ff7a
hermes cron remove 128a96f56ad0
```

**Step 2:** I adopt a behavior: before responding to the user, check if `~/.hermes/proposals_trigger` exists. If yes, read the proposal ID, dispatch agents to review, post comments, delete trigger.

**Verification:** User creates proposal → in their next message or on next refresh, agents have already commented.

---

### Task 8: Commit and deploy all changes

**Verification:**
- `git diff --stat` shows all changed files
- Service restarted: `launchctl stop/start com.reedtrullz.kanban-dashboard`
- Create test proposal → see "Analyzing..." → agents comment within seconds → timestamps relative → comments colorful
