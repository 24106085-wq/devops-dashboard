"""Kanban CRUD flow, SQLite persistence/initialisation and the /health endpoint."""

import os
import shutil
import sqlite3
import tempfile
import unittest

import app as app_module


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "data", "dashboard.db")
        os.makedirs(os.path.dirname(self.path))
        app_module.app.config["DB_PATH"] = self.path
        app_module.app.config["TESTING"] = True

    def new_client(self):
        """A fresh test client, i.e. what a restarted process would see."""
        return app_module.app.test_client()

    def test_database_is_created_on_first_use(self):
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.new_client().get("/api/tasks").get_json(), [])
        self.assertTrue(os.path.exists(self.path))

    def test_full_kanban_flow_persists_across_restarts(self):
        client = self.new_client()
        task = client.post("/api/tasks", json={"title": "Persist me", "owner": "Vedant"}).get_json()
        self.assertEqual(task["status"], "todo")
        keep = client.post("/api/tasks", json={"title": "Move me"}).get_json()
        drop = client.post("/api/tasks", json={"title": "Delete me"}).get_json()
        client.patch("/api/tasks/%d" % keep["id"], json={"status": "inprogress"})
        client.patch("/api/tasks/%d" % keep["id"], json={"status": "done"})
        self.assertEqual(client.delete("/api/tasks/%d" % drop["id"]).status_code, 204)

        restarted = self.new_client()  # new client = new request contexts and new connections
        tasks = {t["title"]: t for t in restarted.get("/api/tasks").get_json()}
        self.assertEqual(set(tasks), {"Persist me", "Move me"})
        self.assertEqual(tasks["Persist me"]["owner"], "Vedant")
        self.assertEqual(tasks["Move me"]["status"], "done")

    def test_data_is_stored_in_the_configured_file(self):
        self.new_client().post("/api/tasks", json={"title": "On disk"})
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT title FROM tasks").fetchall()
        self.assertEqual(rows, [("On disk",)])

    def test_existing_database_is_not_reset_on_startup(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'todo')"
            )
            conn.execute("INSERT INTO tasks (title) VALUES ('legacy row')")
        titles = [t["title"] for t in self.new_client().get("/api/tasks").get_json()]
        self.assertEqual(titles, ["legacy row"])

    def test_ids_keep_increasing_after_delete(self):
        client = self.new_client()
        first = client.post("/api/tasks", json={"title": "a"}).get_json()["id"]
        client.delete("/api/tasks/%d" % first)
        second = client.post("/api/tasks", json={"title": "b"}).get_json()["id"]
        self.assertGreater(second, first)

    def test_delete_missing_task_is_404(self):
        self.assertEqual(self.new_client().delete("/api/tasks/12345").status_code, 404)


class HealthTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True

    def test_health_reports_database_ok(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, path)
        app_module.app.config["DB_PATH"] = path
        body = app_module.app.test_client().get("/health").get_json()
        self.assertEqual(body, {"status": "ok", "database": "ok"})

    def test_health_is_503_when_database_cannot_be_opened(self):
        app_module.app.config["DB_PATH"] = os.path.join(tempfile.gettempdir(), "missing-dir-xyz", "x.db")
        response = app_module.app.test_client().get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
