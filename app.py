import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
app.config["DB_PATH"] = os.environ.get("DB_PATH", "dashboard.db")

STATUSES = ["todo", "inprogress", "done"]

# Sample data shown when no GitHub repository is configured (clearly labelled "demo" in the UI)
DEMO_RUNS = [
    {"id": 1, "name": "Add Kanban board", "branch": "main", "status": "completed", "conclusion": "success", "duration": 74, "url": "#", "created": "2026-10-20T10:12:00Z"},
    {"id": 2, "name": "Fix task API validation", "branch": "main", "status": "completed", "conclusion": "failure", "duration": 41, "url": "#", "created": "2026-10-20T09:30:00Z"},
    {"id": 3, "name": "Add Dockerfile", "branch": "main", "status": "completed", "conclusion": "success", "duration": 88, "url": "#", "created": "2026-10-19T16:05:00Z"},
    {"id": 4, "name": "Add unit tests", "branch": "main", "status": "completed", "conclusion": "success", "duration": 63, "url": "#", "created": "2026-10-19T14:20:00Z"},
]


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'todo')"
        )
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def all_tasks():
    rows = get_db().execute("SELECT * FROM tasks ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ---------- pages ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify(status="ok")


# ---------- kanban API ----------
@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    return jsonify(all_tasks())


@app.route("/api/tasks", methods=["POST"])
def add_task():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title:
        return jsonify(error="Title is required"), 400
    owner = str(data.get("owner", "")).strip()
    db = get_db()
    cur = db.execute("INSERT INTO tasks (title, owner) VALUES (?, ?)", (title, owner))
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def move_task(task_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in STATUSES:
        return jsonify(error="Invalid status"), 400
    db = get_db()
    cur = db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Task not found"), 404
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    db = get_db()
    cur = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Task not found"), 404
    return "", 204


# ---------- pipeline status ----------
def fetch_github_runs(repo):
    """Return the latest GitHub Actions runs for owner/repo, or None if unavailable."""
    url = "https://api.github.com/repos/%s/actions/runs?per_page=10" % repo
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "devops-dashboard"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    runs = []
    for r in payload.get("workflow_runs", []):
        duration = None
        try:
            start = datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
            duration = int((end - start).total_seconds())
        except (KeyError, ValueError, AttributeError):
            pass
        runs.append({
            "id": r.get("id"),
            "name": r.get("display_title") or r.get("name"),
            "branch": r.get("head_branch"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "duration": duration,
            "url": r.get("html_url"),
            "created": r.get("created_at"),
        })
    return runs


def get_pipeline():
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if repo:
        runs = fetch_github_runs(repo)
        if runs is not None:
            return {"source": "live", "repo": repo, "runs": runs}
    return {"source": "demo", "repo": repo, "runs": DEMO_RUNS}


@app.route("/api/pipeline")
def pipeline():
    return jsonify(get_pipeline())


@app.route("/api/summary")
def summary():
    pipe = get_pipeline()
    runs = pipe["runs"]
    finished = [r for r in runs if r["status"] == "completed"]
    success = [r for r in finished if r["conclusion"] == "success"]
    failed = [r for r in finished if r["conclusion"] == "failure"]
    rate = round(100 * len(success) / len(finished)) if finished else 0
    tasks = all_tasks()
    return jsonify(
        source=pipe["source"],
        total_runs=len(runs),
        success_rate=rate,
        failed_builds=len(failed),
        tasks_todo=sum(1 for t in tasks if t["status"] == "todo"),
        tasks_inprogress=sum(1 for t in tasks if t["status"] == "inprogress"),
        tasks_done=sum(1 for t in tasks if t["status"] == "done"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
