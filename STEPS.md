# Steps to push and present (read this first)

## A. Push to GitHub (about 10 minutes)
1. Unzip devops-dashboard.zip.
2. On github.com click + -> New repository. Name: devops-dashboard, Public. Create.
3. Open a terminal inside the unzipped devops-dashboard folder and run:

       git init -b main
       git add .
       git commit -m "Initial commit: DevOps dashboard"
       git remote add origin https://github.com/<your-username>/devops-dashboard.git
       git push -u origin main

   (No Git installed? On the repo page click "uploading an existing file", drag everything inside the folder, including the .github folder, then Commit.)
4. Open the Actions tab. Wait for Build, Test, Deploy to turn green. If Deploy fails at the push step:
   Settings -> Actions -> General -> Workflow permissions -> Read and write permissions -> Save, then Re-run all jobs.

## B. Run the dashboard for the demo
Option 1 - Docker (best for the presentation, Docker Desktop must be running):

    docker compose up --build

Option 2 - without Docker:

    pip install -r requirements.txt
    python app.py

Open http://localhost:5000

To show LIVE pipeline runs from your repo, set GITHUB_REPO before starting
(PowerShell: $env:GITHUB_REPO="<username>/devops-dashboard"). Without it the page shows "demo data" in an orange badge.
If the repository is private, also set GITHUB_TOKEN to a personal access token.

## C. Kanban board for the Agile part
Use the dashboard's own board, and also create a GitHub Project (Projects tab -> New project -> Board) with the same cards,
so you can show both. Suggested cards (one per member): Backend API, Dashboard UI, Dockerfile + Compose, GitHub Actions workflow + tests, Report + PPT.
Move cards To Do -> In Progress -> Done during the demo.

## D. Group of 5 - who does what (make real commits/PRs each)
1. Backend - app.py API + SQLite
2. Frontend - templates/index.html (dashboard + Kanban)
3. Docker - Dockerfile, docker-compose.yml, .dockerignore
4. CI/CD - .github/workflows/ci-cd.yml and test_app.py
5. Docs/Agile - README, report, PPT, GitHub Project board
Each member should clone the repo and push at least one commit from their own account (git log then shows 5 contributors).

## E. Presentation flow (5-7 minutes)
1. Problem: teams need pipeline status and task tracking in one place.
2. Tools: Git/GitHub, GitHub Actions, Docker, Compose, Kanban.
3. Live demo: open the dashboard -> show cards and runs table -> add a task and move it across the Kanban columns.
4. Show GitHub Actions: Build -> Test -> Deploy green, then the image under Packages.
5. Show the Dockerfile and docker-compose.yml, and run `docker compose up`.
6. Make a small change, push, and show the pipeline run again (CI/CD in action).
7. Conclusion: automation reduces manual work and catches errors early.

## F. Likely questions from ma'am
- What is CI/CD? Automatic build and test on every push (CI) and automatic release (CD).
- Why Docker? Same environment everywhere; the image runs identically on any machine.
- Why Compose? Starts the app with its volume/config using one command.
- What does `needs:` do? Makes jobs run in order, so Deploy runs only if Test passed.
- Where does the dashboard get the runs? From the GitHub REST API (api.github.com/repos/<repo>/actions/runs).
- What is the demo badge? Shown when no GITHUB_REPO is set; sample data is used instead of live data.
