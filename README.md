# DevOps Pipeline & Kanban Dashboard

ASSD Mini Project (Experiment 11) - a Flask dashboard that shows:
- CI/CD pipeline runs from GitHub Actions (live via the GitHub API, or demo data if no repo is set)
- A Kanban board (To Do / In Progress / Done) stored in SQLite
- Two views: Command Center (KPI cards, pipeline table, analytics, system status) and Kanban Board (search, add, move, delete)
- Summary cards: pipeline runs, success rate, failed builds, open tasks

**Tools used:** Git & GitHub, GitHub Actions (Build -> Test -> Deploy), Docker, Docker Compose, GitHub Container Registry, Kanban board (Agile).

## Run locally (Python)
    pip install -r requirements.txt
    python app.py            # open http://localhost:5000

## Run with Docker Compose
    docker compose up --build
    # live pipeline data: set GITHUB_REPO=owner/repo first
    #   Windows PowerShell:  $env:GITHUB_REPO="24106085-wq/devops-dashboard"; docker compose up --build

## Run tests
    pip install pytest
    pytest -v

## API
| Endpoint | Purpose |
|---|---|
| GET /health | Health check used by the pipeline |
| GET/POST /api/tasks | List / add Kanban tasks |
| PATCH/DELETE /api/tasks/<id> | Move / delete a task |
| GET /api/pipeline | Latest GitHub Actions runs |
| GET /api/summary | Numbers for the summary cards |

## Windows troubleshooting

**Python version:** use Python 3.12 (matches CI). Python 3.14 may fail to build PyYAML 6.0.2.

```powershell
cd "C:\Users\Vedant Mayekar\devops-dashboard\devops-dashboard"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -v
python -m ruff check .
python -m compileall -q .
```

**Bash tests:** `bash` on PATH may be the broken WSL launcher (`execvpe(/bin/bash) failed`).
The tests locate Git Bash automatically (`C:\Program Files\Git\bin\bash.exe`), verify it starts,
and skip only the Bash tests, with a clear reason, if none is usable.
