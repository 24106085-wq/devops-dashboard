"""/api/pipeline and /api/summary behaviour: live / demo / unavailable states and statistics."""

import json
import logging
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import app as app_module
import github_client
import pipeline
from test_github_client import FakeResponse, http_error, raw_run

TOKEN = "ghp_SECRET_TOKEN_VALUE"


def runs_payload(*items, total=None):
    return {"total_count": total if total is not None else len(items), "workflow_runs": list(items)}


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        app_module.app.config["DB_PATH"] = self.path
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()
        github_client.clear_cache()
        env = {"GITHUB_REPO": "o/r", "DEMO_MODE": "", "GITHUB_TOKEN": "", "GITHUB_CACHE_TTL": "0"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.remove, self.path)

    def github_returns(self, payload):
        return mock.patch("github_client.urllib.request.urlopen", return_value=FakeResponse(payload))

    def github_fails(self, exc):
        return mock.patch("github_client.urllib.request.urlopen", side_effect=exc)

    def pipeline(self, query=""):
        return self.client.get("/api/pipeline" + query).get_json()

    def summary(self):
        return self.client.get("/api/summary").get_json()


class LiveStateTests(ApiTestCase):
    def test_live_data_from_github_api(self):
        with self.github_returns(runs_payload(raw_run(1), raw_run(2, head_branch="dev"), total=57)):
            data = self.pipeline()
        self.assertEqual(data["source"], "live")
        self.assertIsNone(data["error"])
        self.assertEqual([r["id"] for r in data["runs"]], [1, 2])
        self.assertEqual(data["total_count"], 57)
        self.assertEqual(data["actions_url"], "https://github.com/o/r/actions")
        self.assertEqual(data["runs"][0]["url"], "https://github.com/o/r/actions/runs/1")
        self.assertTrue(data["fetched_at"])
        self.assertFalse(any(r.get("demo") for r in data["runs"]))

    def test_mixed_statuses_statistics(self):
        items = [
            raw_run(1, status="completed", conclusion="success"),
            raw_run(2, status="completed", conclusion="success"),
            raw_run(3, status="completed", conclusion="failure"),
            raw_run(4, status="completed", conclusion="timed_out"),
            raw_run(5, status="completed", conclusion="cancelled"),
            raw_run(6, status="completed", conclusion="skipped"),
            raw_run(7, status="in_progress", conclusion=None),
            raw_run(8, status="queued", conclusion=None),
            raw_run(9, status="completed", conclusion="neutral"),
        ]
        with self.github_returns(runs_payload(*items)):
            stats = self.pipeline()["stats"]
        self.assertEqual(stats["total_runs"], 9)
        self.assertEqual(stats["successful"], 2)
        self.assertEqual(stats["failed"], 2)
        self.assertEqual(stats["cancelled"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["in_progress"], 1)
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(stats["other"], 1)
        self.assertEqual(stats["completed"], 7)
        # only success/failure runs are eligible: 2 of 4
        self.assertEqual(stats["eligible_runs"], 4)
        self.assertEqual(stats["success_rate"], 50)
        self.assertEqual(stats["latest_run"], 1)
        self.assertEqual(stats["latest_run_at"], "2026-10-20T10:00:00Z")

    def test_in_progress_runs_are_counted_separately_and_do_not_affect_rate(self):
        items = [raw_run(1, status="in_progress", conclusion=None), raw_run(2, conclusion="success")]
        with self.github_returns(runs_payload(*items)):
            data = self.pipeline()
        self.assertEqual(data["stats"]["in_progress"], 1)
        self.assertEqual(data["stats"]["success_rate"], 100)
        self.assertEqual(data["stats"]["current_status"], "in_progress")
        self.assertIsNone(data["runs"][0]["duration"])

    def test_only_in_progress_runs_have_no_success_rate(self):
        with self.github_returns(runs_payload(raw_run(1, status="in_progress", conclusion=None))):
            data = self.pipeline()
        self.assertEqual(data["source"], "live")
        self.assertIsNone(data["stats"]["success_rate"])
        self.assertEqual(data["stats"]["eligible_runs"], 0)

    def test_empty_workflow_run_list_is_live_with_no_stats(self):
        with self.github_returns(runs_payload()):
            data = self.pipeline()
        self.assertEqual(data["source"], "live")
        self.assertEqual(data["runs"], [])
        self.assertEqual(data["stats"]["total_runs"], 0)
        self.assertIsNone(data["stats"]["success_rate"])
        self.assertIsNone(data["stats"]["latest_run"])

    def test_summary_reflects_live_runs(self):
        items = [raw_run(1), raw_run(2, conclusion="failure"), raw_run(3, status="in_progress", conclusion=None)]
        with self.github_returns(runs_payload(*items)):
            s = self.summary()
        self.assertEqual(s["source"], "live")
        self.assertEqual(s["total_runs"], 3)
        self.assertEqual(s["successful_runs"], 1)
        self.assertEqual(s["failed_builds"], 1)
        self.assertEqual(s["in_progress_runs"], 1)
        self.assertEqual(s["success_rate"], 50)
        self.assertIsNone(s["pipeline_error"])

    def test_task_counts_are_independent_of_pipeline_state(self):
        self.client.post("/api/tasks", json={"title": "A"})
        with self.github_fails(http_error(500)):
            s = self.summary()
        self.assertEqual(s["tasks_todo"], 1)
        self.assertEqual(s["source"], "unavailable")

    def test_pagination_parameters_reach_github_and_are_clamped(self):
        with self.github_returns(runs_payload(raw_run(1), total=120)) as m:
            data = self.pipeline("?page=2&per_page=10")
        url = m.call_args[0][0].full_url
        self.assertIn("page=2", url)
        self.assertIn("per_page=10", url)
        self.assertEqual((data["page"], data["per_page"]), (2, 10))
        self.assertTrue(data["has_more"])
        with self.github_returns(runs_payload()) as m:
            data = self.pipeline("?page=abc&per_page=9999")
        self.assertEqual((data["page"], data["per_page"]), (1, github_client.MAX_PER_PAGE))
        self.assertFalse(data["has_more"])

    def test_refresh_bypasses_backend_cache(self):
        with mock.patch.dict(os.environ, {"GITHUB_CACHE_TTL": "60"}):
            with self.github_returns(runs_payload(raw_run(1))) as m:
                self.pipeline()
                self.pipeline()
                self.assertEqual(m.call_count, 1)
                self.pipeline("?refresh=1")
                self.assertEqual(m.call_count, 2)

    def test_responses_are_not_cacheable_by_browsers(self):
        with self.github_returns(runs_payload()):
            r = self.client.get("/api/pipeline")
        self.assertEqual(r.headers["Cache-Control"], "no-store")


class UnavailableStateTests(ApiTestCase):
    FAILURES = {
        "auth": http_error(401),
        "rate_limited": http_error(403, {"X-RateLimit-Remaining": "0"}),
        "not_found": http_error(404),
        "server_error": http_error(502),
        "timeout": TimeoutError("slow"),
        "network": urllib.error.URLError(ConnectionRefusedError()),
    }

    def test_each_failure_is_unavailable_with_a_reason_and_no_runs(self):
        for code, exc in self.FAILURES.items():
            with self.subTest(code=code), self.github_fails(exc):
                data = self.pipeline()
                self.assertEqual(data["source"], "unavailable")
                self.assertEqual(data["error"]["code"], code)
                self.assertTrue(data["error"]["message"])
                self.assertEqual(data["runs"], [])
                self.assertEqual(data["stats"]["total_runs"], 0)
                self.assertIsNone(data["stats"]["success_rate"])

    def test_malformed_response_is_unavailable(self):
        with self.github_returns({"unexpected": True}):
            data = self.pipeline()
        self.assertEqual(data["source"], "unavailable")
        self.assertEqual(data["error"]["code"], "malformed")

    def test_unavailable_never_returns_demo_runs(self):
        with self.github_fails(http_error(500)):
            data = self.pipeline()
        self.assertEqual(data["runs"], [])
        self.assertNotIn(pipeline.DEMO_RUNS[0]["name"], json.dumps(data))

    def test_unavailable_summary_has_no_misleading_statistics(self):
        with self.github_fails(http_error(500)):
            s = self.summary()
        self.assertEqual(s["source"], "unavailable")
        self.assertEqual(s["total_runs"], 0)
        self.assertIsNone(s["success_rate"])
        self.assertIsNone(s["failed_builds"])
        self.assertIsNone(s["successful_runs"])
        self.assertEqual(s["pipeline_error"]["code"], "server_error")

    def test_failure_does_not_become_a_cached_success(self):
        with mock.patch.dict(os.environ, {"GITHUB_CACHE_TTL": "60"}):
            with self.github_fails(http_error(500)):
                self.assertEqual(self.pipeline()["source"], "unavailable")
            with self.github_returns(runs_payload(raw_run(1))):
                self.assertEqual(self.pipeline()["source"], "live")

    def test_missing_repo_is_unavailable_not_demo(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPO": ""}), mock.patch("github_client.urllib.request.urlopen") as m:
            data = self.pipeline()
        self.assertEqual(data["source"], "unavailable")
        self.assertEqual(data["error"]["code"], "not_configured")
        m.assert_not_called()

    def test_invalid_repo_is_reported(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPO": "not a repo"}):
            data = self.pipeline()
        self.assertEqual(data["source"], "unavailable")
        self.assertEqual(data["error"]["code"], "invalid_repo")
        self.assertTrue(any("GITHUB_REPO" in w for w in data["config_warnings"]))

    def test_rest_of_dashboard_still_works_when_github_is_down(self):
        with self.github_fails(http_error(500)):
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertEqual(self.client.post("/api/tasks", json={"title": "still works"}).status_code, 201)
            self.assertEqual(len(self.client.get("/api/tasks").get_json()), 1)


class DemoStateTests(ApiTestCase):
    def test_demo_requires_explicit_opt_in_and_never_calls_github(self):
        with (
            mock.patch.dict(os.environ, {"DEMO_MODE": "true"}),
            mock.patch("github_client.urllib.request.urlopen") as m,
        ):
            data = self.pipeline()
        m.assert_not_called()
        self.assertEqual(data["source"], "demo")
        self.assertTrue(all(r["demo"] for r in data["runs"]))
        self.assertIsNone(data["actions_url"])

    def test_demo_mode_accepts_common_truthy_values_only(self):
        cases = (("1", "demo"), ("YES", "demo"), ("on", "demo"), ("false", "unavailable"), ("", "unavailable"))
        for value, expected in cases:
            with self.subTest(value=value), mock.patch.dict(os.environ, {"DEMO_MODE": value, "GITHUB_REPO": ""}):
                self.assertEqual(self.pipeline()["source"], expected)

    def test_unrecognised_demo_value_is_off_and_warned(self):
        with mock.patch.dict(os.environ, {"DEMO_MODE": "maybe", "GITHUB_REPO": ""}):
            data = self.pipeline()
        self.assertEqual(data["source"], "unavailable")
        self.assertTrue(any("DEMO_MODE" in w for w in data["config_warnings"]))

    def test_demo_runs_never_make_a_live_test_pass(self):
        with self.github_fails(http_error(500)):
            self.assertNotEqual(self.pipeline()["source"], "live")
            self.assertNotEqual(self.pipeline()["source"], "demo")


class SecretHandlingTests(ApiTestCase):
    def test_token_never_appears_in_api_responses_or_logs(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}):
            with self.assertLogs(app_module.app.logger, level=logging.DEBUG) as logs:
                app_module.app.logger.debug("capture")  # assertLogs needs at least one record
                for exc in (http_error(401), http_error(500), TimeoutError(), urllib.error.URLError("x")):
                    with self.github_fails(exc):
                        bodies = [self.client.get("/api/pipeline").get_data(as_text=True),
                                  self.client.get("/api/summary").get_data(as_text=True)]
                        for body in bodies:
                            self.assertNotIn(TOKEN, body)
                with self.github_returns(runs_payload(raw_run(1))):
                    self.assertNotIn(TOKEN, self.client.get("/api/pipeline").get_data(as_text=True))
            self.assertNotIn(TOKEN, "\n".join(logs.output))

    def test_dashboard_page_does_not_contain_token(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}):
            self.assertNotIn(TOKEN.encode(), self.client.get("/").data)


class ComputeStatsUnitTests(unittest.TestCase):
    def test_run_state_classification(self):
        cases = [
            ({"status": "completed", "conclusion": "success"}, "success"),
            ({"status": "completed", "conclusion": "failure"}, "failure"),
            ({"status": "completed", "conclusion": "startup_failure"}, "failure"),
            ({"status": "completed", "conclusion": "cancelled"}, "cancelled"),
            ({"status": "completed", "conclusion": "skipped"}, "skipped"),
            ({"status": "completed", "conclusion": "action_required"}, "other"),
            ({"status": "completed"}, "other"),
            ({"status": "in_progress"}, "in_progress"),
            ({"status": "queued"}, "queued"),
            ({"status": "waiting"}, "queued"),
            ({}, "unknown"),
            ({"status": None, "conclusion": None}, "unknown"),
        ]
        for run, expected in cases:
            with self.subTest(run=run):
                self.assertEqual(pipeline.run_state(run), expected)

    def test_compute_stats_handles_missing_fields_and_empty_input(self):
        self.assertEqual(pipeline.compute_stats([]), pipeline.empty_stats())
        stats = pipeline.compute_stats([{}, {"id": 1}])
        self.assertEqual(stats["total_runs"], 2)
        self.assertEqual(stats["other"], 2)
        self.assertIsNone(stats["success_rate"])


if __name__ == "__main__":
    unittest.main()
