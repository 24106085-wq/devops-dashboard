import os
import sqlite3

import config
import github_client
from flask import Flask, g, jsonify, render_template, request
from github_client import DEFAULT_PER_PAGE, MAX_PER_PAGE, GitHubError
from pipeline import DEMO_RUNS, compute_stats, empty_stats, run_state

app = Flask(__name__)
app.config["DB_PATH"] = os.environ.get("DB_PATH", "dashboard.db")

STATUSES = ["todo", "inprogress", "done"]

for _problem in config.problems():
    app.logger.warning("Configuration problem: %s", _problem)


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DB_PATH"], timeout=10)
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
    try:
        get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        app.logger.exception("Health check: database unavailable")
        return jsonify(status="degraded", database="error"), 503
    return jsonify(status="ok", database="ok")


@app.after_request
def no_store_for_api(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
def fetch_github_runs(repo, page=1, per_page=DEFAULT_PER_PAGE, refresh=False):
    """Return normalised GitHub Actions runs for owner/repo; raises GitHubError."""
    return github_client.fetch_runs(repo, page=page, per_page=per_page, refresh=refresh)


def _int_arg(name, default, low, high):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def get_pipeline(page=1, per_page=DEFAULT_PER_PAGE, refresh=False):
    """Pipeline data with an explicit source: live, demo or unavailable."""
    repo = config.github_repo()
    result = {
        "source": "unavailable",
        "repo": repo,
        "runs": [],
        "error": None,
        "page": page,
        "per_page": per_page,
        "total_count": None,
        "fetched_at": None,
        "actions_url": None,
        "config_warnings": config.problems(),
    }
    if config.demo_mode():
        result.update(source="demo", runs=list(DEMO_RUNS))
    elif not repo:
        result["error"] = {
            "code": "not_configured",
            "message": "GITHUB_REPO is not set, so there is no repository to monitor.",
        }
    else:
        try:
            runs = fetch_github_runs(repo, page=page, per_page=per_page, refresh=refresh)
        except GitHubError as exc:
            app.logger.warning("GitHub fetch failed: %s", exc.code)
            result["error"] = exc.as_dict()
        else:
            if runs is None:
                result["error"] = {"code": "unavailable", "message": "GitHub data could not be retrieved."}
            else:
                result.update(
                    source="live",
                    runs=list(runs),
                    total_count=getattr(runs, "total_count", None),
                    fetched_at=getattr(runs, "fetched_at", None),
                    actions_url="https://github.com/%s/actions" % repo,
                )
    result["runs"] = [dict(run, state=run_state(run)) for run in result["runs"]]
    result["stats"] = compute_stats(result["runs"]) if result["source"] != "unavailable" else empty_stats()
    result["has_more"] = (
        result["source"] == "live"
        and result["total_count"] is not None
        and page * per_page < result["total_count"]
    )
    return result


@app.route("/api/pipeline")
def pipeline():
    page = _int_arg("page", 1, 1, 1000)
    per_page = _int_arg("per_page", DEFAULT_PER_PAGE, 1, MAX_PER_PAGE)
    refresh = request.args.get("refresh") == "1"
    return jsonify(get_pipeline(page=page, per_page=per_page, refresh=refresh))


@app.route("/api/summary")
def summary():
    per_page = _int_arg("per_page", DEFAULT_PER_PAGE, 1, MAX_PER_PAGE)
    pipe = get_pipeline(per_page=per_page)
    stats = pipe["stats"]
    available = pipe["source"] != "unavailable"
    tasks = all_tasks()
    return jsonify(
        source=pipe["source"],
        repo=pipe["repo"],
        pipeline_error=pipe["error"],
        total_runs=stats["total_runs"],
        success_rate=stats["success_rate"],
        failed_builds=stats["failed"] if available else None,
        successful_runs=stats["successful"] if available else None,
        in_progress_runs=stats["in_progress"] if available else None,
        eligible_runs=stats["eligible_runs"],
        tasks_todo=sum(1 for t in tasks if t["status"] == "todo"),
        tasks_inprogress=sum(1 for t in tasks if t["status"] == "inprogress"),
        tasks_done=sum(1 for t in tasks if t["status"] == "done"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)  # nosec - development server only; containers use gunicorn
