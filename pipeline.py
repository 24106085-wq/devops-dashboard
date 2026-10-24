"""Run classification, statistics and demo data (no I/O)."""

FAILURE_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
QUEUED_STATUSES = {"queued", "waiting", "pending", "requested"}

# Sample runs shown ONLY when DEMO_MODE is explicitly enabled.
DEMO_RUNS = [
    {
        "id": 1, "name": "Add Kanban board", "workflow": "Demo workflow", "branch": "main",
        "status": "completed", "conclusion": "success", "duration": 74, "url": None,
        "created": "2026-10-20T10:12:00Z", "demo": True,
    },
    {
        "id": 2, "name": "Fix task API validation", "workflow": "Demo workflow", "branch": "main",
        "status": "completed", "conclusion": "failure", "duration": 41, "url": None,
        "created": "2026-10-20T09:30:00Z", "demo": True,
    },
    {
        "id": 3, "name": "Add Dockerfile", "workflow": "Demo workflow", "branch": "main",
        "status": "completed", "conclusion": "success", "duration": 88, "url": None,
        "created": "2026-10-19T16:05:00Z", "demo": True,
    },
    {
        "id": 4, "name": "Add unit tests", "workflow": "Demo workflow", "branch": "main",
        "status": "completed", "conclusion": "success", "duration": 63, "url": None,
        "created": "2026-10-19T14:20:00Z", "demo": True,
    },
]


def run_state(run):
    """Classify a run: success, failure, cancelled, skipped, in_progress, queued, other, unknown."""
    status, conclusion = run.get("status"), run.get("conclusion")
    if status == "completed":
        if conclusion == "success":
            return "success"
        if conclusion in FAILURE_CONCLUSIONS:
            return "failure"
        if conclusion in ("cancelled", "skipped"):
            return conclusion
        return "other"
    if status == "in_progress":
        return "in_progress"
    if status in QUEUED_STATUSES:
        return "queued"
    return "unknown"


def empty_stats():
    return {
        "total_runs": 0,
        "completed": 0,
        "successful": 0,
        "failed": 0,
        "cancelled": 0,
        "skipped": 0,
        "in_progress": 0,
        "queued": 0,
        "other": 0,
        "eligible_runs": 0,
        "success_rate": None,
        "latest_run": None,
        "latest_run_at": None,
        "current_status": None,
    }


def compute_stats(runs):
    """Statistics over the retrieved window.

    ``success_rate`` uses only completed runs that ended in success or failure
    (cancelled/skipped/neutral runs are not eligible) and is None when there
    are no eligible runs.
    """
    stats = empty_stats()
    stats["total_runs"] = len(runs)
    counts = {}
    for run in runs:
        counts[run_state(run)] = counts.get(run_state(run), 0) + 1
        if run.get("status") == "completed":
            stats["completed"] += 1
    stats["successful"] = counts.get("success", 0)
    stats["failed"] = counts.get("failure", 0)
    stats["cancelled"] = counts.get("cancelled", 0)
    stats["skipped"] = counts.get("skipped", 0)
    stats["in_progress"] = counts.get("in_progress", 0)
    stats["queued"] = counts.get("queued", 0)
    stats["other"] = counts.get("other", 0) + counts.get("unknown", 0)
    stats["eligible_runs"] = stats["successful"] + stats["failed"]
    if stats["eligible_runs"]:
        stats["success_rate"] = round(100 * stats["successful"] / stats["eligible_runs"])
    if runs:
        latest = runs[0]  # GitHub returns the newest run first
        stats["latest_run"] = latest.get("id")
        stats["latest_run_at"] = latest.get("created")
        stats["current_status"] = run_state(latest)
    return stats
