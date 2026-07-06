"""Second Brain REST API and Web Server.

Run:
    uv run uvicorn server:app --reload --port 8000
"""

from datetime import date, datetime, timezone
import json
import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse

import db
import tasks
from assistant import MODEL, make_run_config, new_session_id, router_agent
from agents import Agent, Runner
from openai.types.responses import ResponseTextDeltaEvent

summary_agent = Agent(
    name="Second Brain Summariser",
    model=MODEL,
    instructions=(
        "You are the analytical brain of a Second Brain productivity system. "
        "Your task is to analyze raw memories (notes, links, expenses) and task activity "
        "for a given period (daily/weekly/monthly) and compile them into a set of high-signal, "
        "engaging, and beautifully formatted insights.\n\n"
        "Guiding Principles for Great Insights:\n"
        "1. **Never Just List**: Do not translate 'Expense: 649 INR' into 'You spent 649 INR'. "
        "Instead, synthesize: 'You spent 649 INR on entertainment (Netflix subscription)'. "
        "Connect related dots (e.g. vaccine details, travel changes).\n"
        "2. **Group by Theme**: Organize the summary into thematic sections using bold emojis "
        "and subheaders (starting with '### '), such as:\n"
        "   - '### 🚀 Project Momentum': Completed/created tasks and milestones across workspaces (e.g. Toddle, Personal).\n"
        "   - '### 💸 Finances & Subscriptions': Expense summaries, category totals, and financial events.\n"
        "   - '### 🌱 Health, Life & Memories': Notes about physical well-being, meetings, bookmarks, or notable experiences.\n"
        "   Only output sections that have relevant data. Do not output empty sections.\n"
        "3. **Tone**: Direct, professional, insightful, and supportive. Skip all preambles, greetings, "
        "or postscripts. Do not say 'Here is your summary' or 'According to your logs'. Start directly with the headings.\n"
        "4. **Formatting**: Use clean Markdown. Use bullet points under headings. Bold key phrases, project names, "
        "and monetary figures to make it highly skimmable."
    )
)

app = FastAPI(title="Second Brain API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure the database is initialized on startup
@app.on_event("startup")
def startup_event():
    conn = db.connect()
    try:
        db.init_db(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_summaries (
                filter TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()

# --- API Endpoints ---

@app.get("/api/dashboard/summary")
def get_dashboard_summary(filter: str = "weekly", refresh: bool = False):
    conn = db.connect()
    try:
        db.init_db(conn)
        
        # 1. Determine date range filter
        if filter == "daily":
            date_filter = "occurred_at = DATE('now', 'localtime')"
        elif filter == "weekly":
            date_filter = "occurred_at >= DATE('now', 'localtime', '-6 days')"
        elif filter == "monthly":
            date_filter = "occurred_at >= DATE('now', 'localtime', '-29 days')"
        else:
            date_filter = "occurred_at >= DATE('now', 'localtime', '-6 days')"
            
        # 2. Expense Summary
        expense_query = f"""
            SELECT amount, currency, category, occurred_at 
            FROM entries 
            WHERE type = 'expense' AND {date_filter}
        """
        expenses = conn.execute(expense_query).fetchall()
        
        expense_by_category = {}
        total_expense = 0
        currency = "INR"  # default
        for exp in expenses:
            amt = exp["amount"] or 0
            cat = exp["category"] or "other"
            total_expense += amt
            expense_by_category[cat] = expense_by_category.get(cat, 0) + amt
            if exp["currency"]:
                currency = exp["currency"]
                
        # 3. Tasks summary
        task_query = "SELECT status, due_at FROM tasks"
        all_tasks = conn.execute(task_query).fetchall()
        task_counts = {"open": 0, "in-progress": 0, "done": 0, "cancelled": 0, "overdue": 0}
        today_str = date.today().isoformat()
        
        for t in all_tasks:
            status = t["status"]
            if status in task_counts:
                task_counts[status] += 1
            if status in ("open", "in-progress") and t["due_at"] and t["due_at"] < today_str:
                task_counts["overdue"] += 1
                
        # 4. Recent memories (limit to 10)
        entries = db.get_entries(conn, limit=10)
        
        # 5. Projects summary
        project_query = """
            SELECT p.id, p.name as project, o.name as org, 
                   COUNT(CASE WHEN t.status = 'open' OR t.status = 'in-progress' THEN 1 END) as open_tasks
            FROM projects p
            JOIN organizations o ON o.id = p.org_id
            LEFT JOIN tasks t ON t.project_id = p.id
            GROUP BY p.id
        """
        projects_summary = [dict(r) for r in conn.execute(project_query).fetchall()]
        
        # 6. Expense history grouped by date
        chart_query = f"""
            SELECT occurred_at, SUM(amount) as total
            FROM entries
            WHERE type = 'expense' AND {date_filter}
            GROUP BY occurred_at
            ORDER BY occurred_at ASC
        """
        expense_history = [dict(r) for r in conn.execute(chart_query).fetchall()]
        
        # 7. Check Cache for AI Summary
        cached_row = conn.execute(
            "SELECT summary, updated_at FROM dashboard_summaries WHERE filter = ?",
            (filter,)
        ).fetchone()

        if refresh or not cached_row:
            # Collect all entries and tasks within active filter range for AI summariser
            all_entries_query = f"""
                SELECT type, raw_text, occurred_at, amount, currency, category
                FROM entries
                WHERE {date_filter}
                ORDER BY occurred_at DESC
            """
            duration_entries = conn.execute(all_entries_query).fetchall()

            if filter == "daily":
                task_filter = """
                    DATE(completed_at, 'localtime') = DATE('now', 'localtime')
                    OR due_at = DATE('now', 'localtime')
                    OR DATE(created_at, 'localtime') = DATE('now', 'localtime')
                    OR status = 'in-progress'
                """
            elif filter == "weekly":
                task_filter = """
                    DATE(completed_at, 'localtime') >= DATE('now', 'localtime', '-6 days')
                    OR due_at >= DATE('now', 'localtime', '-6 days')
                    OR DATE(created_at, 'localtime') >= DATE('now', 'localtime', '-6 days')
                    OR status = 'in-progress'
                """
            else:
                task_filter = """
                    DATE(completed_at, 'localtime') >= DATE('now', 'localtime', '-29 days')
                    OR due_at >= DATE('now', 'localtime', '-29 days')
                    OR DATE(created_at, 'localtime') >= DATE('now', 'localtime', '-29 days')
                    OR status = 'in-progress'
                """

            tasks_query = f"""
                SELECT title, status, priority, due_at, completed_at
                FROM tasks
                WHERE {task_filter}
            """
            duration_tasks = conn.execute(tasks_query).fetchall()

            context_parts = [f"Period: {filter.upper()} (Today is {date.today().isoformat()})\n"]
            context_parts.append("## Logged Memories & Events:")
            if not duration_entries:
                context_parts.append("- No memories logged in this period.")
            for idx, entry in enumerate(duration_entries):
                type_str = entry["type"].upper()
                text = entry["raw_text"]
                occurred = entry["occurred_at"]
                amount_str = f" ({entry['amount']} {entry['currency']} in category '{entry['category']}')" if entry["amount"] else ""
                context_parts.append(f"{idx+1}. [{type_str} | Date: {occurred}] {text}{amount_str}")

            context_parts.append("\n## Tasks Activity:")
            if not duration_tasks:
                context_parts.append("- No task activity in this period.")
            for idx, task in enumerate(duration_tasks):
                due = f", Due: {task['due_at']}" if task["due_at"] else ""
                completed = f", Completed: {task['completed_at'][:10]}" if task["completed_at"] else ""
                priority = f", Priority: {task['priority']}" if task["priority"] else ""
                context_parts.append(f"{idx+1}. {task['title']} [Status: {task['status']}{priority}{due}{completed}]")

            context_text = "\n".join(context_parts)

            ai_summary = "No insights available."
            if duration_entries or duration_tasks:
                try:
                    result = Runner.run_sync(
                        summary_agent,
                        [{"role": "user", "content": context_text}],
                        run_config=make_run_config()
                    )
                    ai_summary = result.final_output or "No insights compiled."
                except Exception as e:
                    ai_summary = f"Could not generate AI insights: {e}"
            else:
                ai_summary = f"No activities or tasks found for this {filter} period to summarize. Try logging some memories or tasks!"
                
            updated_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO dashboard_summaries (filter, summary, updated_at) VALUES (?, ?, ?)",
                (filter, ai_summary, updated_at)
            )
            conn.commit()
        else:
            ai_summary = cached_row["summary"]
            updated_at = cached_row["updated_at"]

        return {
            "expenses": {
                "total": total_expense,
                "currency": currency,
                "by_category": expense_by_category,
                "history": expense_history
            },
            "tasks": task_counts,
            "projects": projects_summary,
            "recent_activities": entries,
            "ai_summary": ai_summary,
            "updated_at": updated_at
        }
    finally:
        conn.close()

@app.get("/api/projects")
def get_projects():
    conn = db.connect()
    try:
        db.init_db(conn)
        query = """
            SELECT p.id, p.name as project, o.name as org,
                   COUNT(t.id) as total_tasks,
                   COUNT(CASE WHEN t.status = 'open' THEN 1 END) as open_tasks,
                   COUNT(CASE WHEN t.status = 'in-progress' THEN 1 END) as in_progress_tasks,
                   COUNT(CASE WHEN t.status = 'done' THEN 1 END) as done_tasks
            FROM projects p
            JOIN organizations o ON o.id = p.org_id
            LEFT JOIN tasks t ON t.project_id = p.id
            GROUP BY p.id
            ORDER BY o.name, p.name
        """
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@app.post("/api/projects")
def create_project_route(payload: dict):
    project = payload.get("project", "").strip()
    org = payload.get("org", "").strip()
    
    if not project and not org:
        raise HTTPException(status_code=400, detail="Must specify project name or org name")
        
    conn = db.connect()
    try:
        db.init_db(conn)
        proj_name = project or tasks.DEFAULT_PROJECT
        org_name = org or tasks.DEFAULT_ORG
        proj_id = tasks.ensure_project(conn, proj_name, org_name)
        conn.commit()
        return {"id": proj_id, "project": proj_name, "org": org_name}
    finally:
        conn.close()

@app.get("/api/projects/{project_id}")
def get_project_details(project_id: int):
    conn = db.connect()
    try:
        db.init_db(conn)
        
        proj_row = conn.execute(
            """
            SELECT p.id, p.name as project, o.name as org
            FROM projects p
            JOIN organizations o ON o.id = p.org_id
            WHERE p.id = ?
            """, (project_id,)
        ).fetchone()
        
        if not proj_row:
            raise HTTPException(status_code=404, detail="Project not found")
            
        tasks_rows = conn.execute(
            """
            SELECT t.*, p.name as project, o.name as org
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            JOIN organizations o ON o.id = p.org_id
            WHERE t.project_id = ?
            ORDER BY t.id DESC
            """, (project_id,)
        ).fetchall()
        
        project_tasks = []
        for r in tasks_rows:
            t = dict(r)
            if t.get("payload") is not None:
                try:
                    t["payload"] = json.loads(t["payload"])
                except Exception:
                    pass
            t["attachments"] = tasks.get_attachments(conn, t["id"])
            t["progress"] = tasks.subtask_progress(conn, t["id"])
            project_tasks.append(t)
            
        return {
            "project": dict(proj_row),
            "tasks": project_tasks
        }
    finally:
        conn.close()

@app.post("/api/tasks")
def create_task_endpoint(payload: dict):
    title = payload.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
        
    conn = db.connect()
    try:
        db.init_db(conn)
        task_id = tasks.create_task(
            conn,
            title=title,
            description=payload.get("description"),
            project=payload.get("project"),
            org=payload.get("org"),
            due_at=payload.get("due_at"),
            priority=payload.get("priority"),
            parent_id=payload.get("parent_task_id"),
            recur_freq=payload.get("recur_freq"),
            recur_interval=payload.get("recur_interval", 1)
        )
        return {"ok": True, "task": tasks.get_task(conn, task_id)}
    finally:
        conn.close()

@app.patch("/api/tasks/{task_id}")
def update_task_endpoint(task_id: int, payload: dict):
    conn = db.connect()
    try:
        db.init_db(conn)
        if tasks.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
            
        fields = {}
        for key in ["title", "description", "status", "due_at", "priority", "parent_id", "project", "org", "recur_freq", "recur_interval"]:
            if key in payload:
                fields[key] = payload[key]
                
        tasks.update_task(conn, task_id, **fields)
        return {"ok": True, "task": tasks.get_task(conn, task_id)}
    finally:
        conn.close()

@app.post("/api/tasks/{task_id}/complete")
def complete_task_endpoint(task_id: int):
    conn = db.connect()
    try:
        db.init_db(conn)
        if tasks.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
            
        next_id = tasks.complete_task(conn, task_id)
        result = {"ok": True, "task": tasks.get_task(conn, task_id)}
        if next_id is not None:
            result["next_occurrence"] = tasks.get_task(conn, next_id)
        return result
    finally:
        conn.close()

@app.post("/api/tasks/{task_id}/attachments")
def add_task_attachment_endpoint(task_id: int, payload: dict):
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    conn = db.connect()
    try:
        db.init_db(conn)
        if tasks.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
            
        att_id = tasks.add_attachment(
            conn,
            task_id,
            url=url,
            type=payload.get("type", "link"),
            description=payload.get("description")
        )
        return {"ok": True, "attachment": {
            "id": att_id,
            "task_id": task_id,
            "url": url,
            "type": payload.get("type", "link"),
            "description": payload.get("description")
        }}
    finally:
        conn.close()

@app.get("/api/memories")
def get_memories(
    type: str = None,
    tag: str = None,
    query: str = None,
    limit: int = 100
):
    conn = db.connect()
    try:
        db.init_db(conn)
        sql = "SELECT * FROM entries"
        where = []
        params = []
        if type:
            where.append("type = ?")
            params.append(type)
        if tag:
            where.append(
                "id IN (SELECT et.entry_id FROM entry_tags et "
                "JOIN tags t ON t.id = et.tag_id WHERE t.name = ?)"
            )
            params.append(tag.strip().lower())
        if query:
            where.append("(raw_text LIKE ? OR category LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
            
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
            
        entries = [db._row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        db._attach_tags(conn, entries)
        return entries
    finally:
        conn.close()

@app.post("/api/memories")
def create_memory_endpoint(payload: dict):
    type = payload.get("type", "note").strip()
    raw_text = payload.get("raw_text", "").strip()
    
    if not raw_text:
        raise HTTPException(status_code=400, detail="raw_text is required")
        
    conn = db.connect()
    try:
        db.init_db(conn)
        entry_id = db.add_entry(
            conn,
            type=type,
            raw_text=raw_text,
            occurred_at=payload.get("occurred_at"),
            amount=payload.get("amount"),
            currency=payload.get("currency"),
            category=payload.get("category"),
            payload=payload.get("payload"),
            tags=payload.get("tags")
        )
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        entry = db._row_to_dict(row)
        db._attach_tags(conn, [entry])
        return {"ok": True, "entry": entry}
    finally:
        conn.close()

@app.get("/api/tags")
def get_tags_list():
    conn = db.connect()
    try:
        db.init_db(conn)
        return db.get_tags(conn)
    finally:
        conn.close()

@app.get("/api/types")
def get_types_list():
    conn = db.connect()
    try:
        db.init_db(conn)
        return db.get_types(conn)
    finally:
        conn.close()

# --- AI Chat agent Endpoint ---

@app.post("/api/chat")
async def chat_endpoint(payload: dict):
    message = payload.get("message", "").strip()
    history = payload.get("history", [])
    group_id = payload.get("group_id")
    
    if not group_id:
        group_id = new_session_id()
        
    sdk_input = []
    for msg in history:
        sdk_input.append({"role": msg["role"], "content": msg["content"]})
    sdk_input.append({"role": "user", "content": message})
    
    async def event_generator():
        try:
            result = Runner.run_streamed(
                router_agent, sdk_input, max_turns=12, run_config=make_run_config(group_id)
            )
            text = ""
            async for event in result.stream_events():
                if event.type == "raw_response_event" and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    text += event.data.delta
                    yield f"data: {json.dumps({'type': 'delta', 'text': event.data.delta})}\n\n"
            
            if not text:
                text = result.final_output or "(no response)"
                yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
                
            updated_history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": text}
            ]
            yield f"data: {json.dumps({'type': 'done', 'history': updated_history, 'group_id': group_id})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# --- Serve Static Web UI Assets ---
try:
    os.makedirs("static", exist_ok=True)
except Exception:
    pass

app.mount("/", StaticFiles(directory="static", html=True), name="static")
