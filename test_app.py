import os
import tempfile
import unittest
from unittest import mock

import app as app_module


class DashboardTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        app_module.app.config["DB_PATH"] = self.path
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        os.remove(self.path)

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")

    def test_dashboard_page_loads(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"DevOps Pipeline", r.data)

    def test_add_task(self):
        r = self.client.post("/api/tasks", json={"title": "Write report", "owner": "Vedant"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["status"], "todo")

    def test_add_task_requires_title(self):
        r = self.client.post("/api/tasks", json={"title": "  "})
        self.assertEqual(r.status_code, 400)

    def test_move_task(self):
        tid = self.client.post("/api/tasks", json={"title": "Build UI"}).get_json()["id"]
        r = self.client.patch("/api/tasks/%d" % tid, json={"status": "inprogress"})
        self.assertEqual(r.get_json()["status"], "inprogress")

    def test_move_task_invalid_status(self):
        tid = self.client.post("/api/tasks", json={"title": "Build UI"}).get_json()["id"]
        r = self.client.patch("/api/tasks/%d" % tid, json={"status": "bogus"})
        self.assertEqual(r.status_code, 400)

    def test_move_missing_task(self):
        r = self.client.patch("/api/tasks/999", json={"status": "done"})
        self.assertEqual(r.status_code, 404)

    def test_delete_task(self):
        tid = self.client.post("/api/tasks", json={"title": "Temp"}).get_json()["id"]
        self.assertEqual(self.client.delete("/api/tasks/%d" % tid).status_code, 204)
        self.assertEqual(self.client.get("/api/tasks").get_json(), [])

    def test_pipeline_demo_when_no_repo(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPO": ""}):
            data = self.client.get("/api/pipeline").get_json()
        self.assertEqual(data["source"], "demo")
        self.assertTrue(len(data["runs"]) > 0)

    def test_pipeline_live_when_github_returns_runs(self):
        fake = [{"id": 1, "name": "x", "branch": "main", "status": "completed",
                 "conclusion": "success", "duration": 10, "url": "#", "created": None}]
        with mock.patch.dict(os.environ, {"GITHUB_REPO": "a/b"}), \
             mock.patch.object(app_module, "fetch_github_runs", return_value=fake):
            data = self.client.get("/api/pipeline").get_json()
        self.assertEqual(data["source"], "live")

    def test_pipeline_falls_back_if_github_fails(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPO": "a/b"}), \
             mock.patch.object(app_module, "fetch_github_runs", return_value=None):
            data = self.client.get("/api/pipeline").get_json()
        self.assertEqual(data["source"], "demo")

    def test_summary_counts(self):
        self.client.post("/api/tasks", json={"title": "A"})
        tid = self.client.post("/api/tasks", json={"title": "B"}).get_json()["id"]
        self.client.patch("/api/tasks/%d" % tid, json={"status": "done"})
        with mock.patch.dict(os.environ, {"GITHUB_REPO": ""}):
            s = self.client.get("/api/summary").get_json()
        self.assertEqual(s["tasks_todo"], 1)
        self.assertEqual(s["tasks_done"], 1)
        self.assertEqual(s["success_rate"], 75)

    def test_summary_failed_builds_from_pipeline_data(self):
        fake = [
            {"id": 1, "name": "a", "branch": "main", "status": "completed", "conclusion": "failure", "duration": 5, "url": None, "created": None},
            {"id": 2, "name": "b", "branch": "main", "status": "completed", "conclusion": "success", "duration": 5, "url": None, "created": None},
            {"id": 3, "name": "c", "branch": None, "status": None, "conclusion": None, "duration": None, "url": None, "created": None},
        ]
        with mock.patch.dict(os.environ, {"GITHUB_REPO": "a/b"}), \
             mock.patch.object(app_module, "fetch_github_runs", return_value=fake):
            s = self.client.get("/api/summary").get_json()
        self.assertEqual(s["failed_builds"], 1)
        self.assertEqual(s["total_runs"], 3)
        self.assertEqual(s["success_rate"], 50)


if __name__ == "__main__":
    unittest.main()
