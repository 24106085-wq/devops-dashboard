"""GitHub Actions client tests. All HTTP is mocked; no internet access is needed."""

import io
import json
import os
import socket
import unittest
import urllib.error
from http.client import HTTPMessage
from unittest import mock

import github_client
from github_client import GitHubError

TOKEN = "ghp_SECRET_TOKEN_VALUE"


def raw_run(run_id=1, **overrides):
    run = {
        "id": run_id,
        "name": "DevOps Dashboard CI/CD",
        "display_title": "Add pipeline monitor",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "head_commit": {"message": "Add pipeline monitor\n\nlong body"},
        "event": "push",
        "run_number": 7,
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/o/r/actions/runs/%d" % run_id,
        "created_at": "2026-10-20T10:00:00Z",
        "run_started_at": "2026-10-20T10:00:05Z",
        "updated_at": "2026-10-20T10:01:35Z",
    }
    run.update(overrides)
    return run


class FakeResponse:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, headers=None, body=None):
    msg = HTTPMessage()
    for key, value in (headers or {}).items():
        msg[key] = value
    data = json.dumps(body if body is not None else {}).encode()
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", msg, io.BytesIO(data))


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        github_client.clear_cache()
        env = {"GITHUB_TOKEN": "", "GITHUB_CACHE_TTL": "0", "GITHUB_TIMEOUT": ""}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, side_effect_or_payload, **kwargs):
        target = "github_client.urllib.request.urlopen"
        if isinstance(side_effect_or_payload, (Exception, list)) and not isinstance(side_effect_or_payload, dict):
            with mock.patch(target, side_effect=side_effect_or_payload) as m:
                return github_client.fetch_runs("o/r", **kwargs), m
        with mock.patch(target, return_value=FakeResponse(side_effect_or_payload)) as m:
            return github_client.fetch_runs("o/r", **kwargs), m

    def assert_error(self, side_effect, code, status=None):
        with mock.patch("github_client.urllib.request.urlopen", side_effect=side_effect):
            with self.assertRaises(GitHubError) as ctx:
                github_client.fetch_runs("o/r")
        self.assertEqual(ctx.exception.code, code)
        if status is not None:
            self.assertEqual(ctx.exception.status, status)
        return ctx.exception


class NormalisationTests(ClientTestCase):
    def test_success_maps_real_fields(self):
        runs, _ = self.fetch({"total_count": 42, "workflow_runs": [raw_run()]})
        run = runs[0]
        self.assertEqual(run["id"], 1)
        self.assertEqual(run["name"], "Add pipeline monitor")
        self.assertEqual(run["workflow"], "DevOps Dashboard CI/CD")
        self.assertEqual(run["branch"], "main")
        self.assertEqual(run["sha"], "a" * 40)
        self.assertEqual(run["commit_message"], "Add pipeline monitor")
        self.assertEqual(run["event"], "push")
        self.assertEqual(run["conclusion"], "success")
        self.assertEqual(run["url"], "https://github.com/o/r/actions/runs/1")
        self.assertEqual(run["duration"], 90)
        self.assertEqual(runs.total_count, 42)
        self.assertTrue(runs.fetched_at.endswith("Z"))

    def test_in_progress_run_has_no_duration_or_conclusion(self):
        item = raw_run(2, status="in_progress", conclusion=None)
        runs, _ = self.fetch({"workflow_runs": [item]})
        self.assertIsNone(runs[0]["duration"])
        self.assertIsNone(runs[0]["conclusion"])
        self.assertEqual(runs[0]["status"], "in_progress")

    def test_missing_timestamps_do_not_invent_duration(self):
        item = raw_run(3, run_started_at=None, updated_at="not a date")
        runs, _ = self.fetch({"workflow_runs": [item]})
        self.assertIsNone(runs[0]["duration"])

    def test_non_github_url_is_dropped(self):
        runs, _ = self.fetch({"workflow_runs": [raw_run(html_url="https://evil.example/x")]})
        self.assertIsNone(runs[0]["url"])

    def test_unusable_entries_are_skipped_not_invented(self):
        payload = {"workflow_runs": ["junk", None, {"name": "no id"}, {"id": "7"}, raw_run(9)]}
        runs, _ = self.fetch(payload)
        self.assertEqual([r["id"] for r in runs], [9])

    def test_empty_list(self):
        runs, _ = self.fetch({"total_count": 0, "workflow_runs": []})
        self.assertEqual(list(runs), [])
        self.assertEqual(runs.total_count, 0)

    def test_missing_optional_fields_are_none(self):
        runs, _ = self.fetch({"workflow_runs": [{"id": 5}]})
        self.assertEqual(runs[0]["id"], 5)
        self.assertIsNone(runs[0]["branch"])
        self.assertIsNone(runs[0]["commit_message"])

    def test_request_url_has_pagination(self):
        _, mocked = self.fetch({"workflow_runs": []}, page=3, per_page=10)
        request = mocked.call_args[0][0]
        self.assertIn("/repos/o/r/actions/runs?", request.full_url)
        self.assertIn("per_page=10", request.full_url)
        self.assertIn("page=3", request.full_url)


class FailureTests(ClientTestCase):
    def test_unauthorized(self):
        self.assert_error(http_error(401), "auth", 401)

    def test_forbidden_without_rate_limit_is_auth(self):
        self.assert_error(http_error(403, body={"message": "Resource not accessible"}), "auth", 403)

    def test_rate_limit_403_with_remaining_zero(self):
        reset = str(int(__import__("time").time()) + 600)
        err = self.assert_error(
            http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}), "rate_limited", 403
        )
        self.assertIn("rate limit", err.message.lower())
        self.assertIn("GITHUB_TOKEN", err.message)
        self.assertGreater(err.retry_after, 0)

    def test_rate_limit_403_detected_from_message(self):
        self.assert_error(http_error(403, body={"message": "API rate limit exceeded for 1.2.3.4"}), "rate_limited")

    def test_rate_limit_429_with_retry_after(self):
        err = self.assert_error(http_error(429, {"Retry-After": "120"}), "rate_limited", 429)
        self.assertEqual(err.retry_after, 120)

    def test_not_found(self):
        self.assert_error(http_error(404), "not_found", 404)

    def test_server_error(self):
        self.assert_error(http_error(503), "server_error", 503)

    def test_other_http_status(self):
        self.assert_error(http_error(418), "http_error", 418)

    def test_timeout_wrapped_in_urlerror(self):
        self.assert_error(urllib.error.URLError(socket.timeout("timed out")), "timeout")

    def test_raw_timeout(self):
        self.assert_error(TimeoutError("read timed out"), "timeout")

    def test_network_failure(self):
        self.assert_error(urllib.error.URLError(ConnectionRefusedError()), "network")
        self.assert_error(ConnectionResetError(), "network")

    def test_malformed_responses(self):
        cases = [b"<html>not json</html>", b"\xff\xfe", [], "text", {"workflow_runs": "nope"}, {"total_count": 1}]
        for payload in cases:
            with self.subTest(payload=payload):
                with mock.patch("github_client.urllib.request.urlopen", return_value=FakeResponse(payload)):
                    with self.assertRaises(GitHubError) as ctx:
                        github_client.fetch_runs("o/r")
                self.assertEqual(ctx.exception.code, "malformed")

    def test_invalid_repo_never_calls_github(self):
        for repo in ("", "no-slash", "a/b/c", "a/b; rm -rf", "../x", "a/..", "a/.", "-/x/y", "a b/c", "a/b\n"):
            with self.subTest(repo=repo), mock.patch("github_client.urllib.request.urlopen") as m:
                with self.assertRaises(GitHubError) as ctx:
                    github_client.fetch_runs(repo)
                self.assertEqual(ctx.exception.code, "invalid_repo")
                m.assert_not_called()


class TokenTests(ClientTestCase):
    def test_token_is_sent_only_as_header(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}):
            _, mocked = self.fetch({"workflow_runs": []})
        request = mocked.call_args[0][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, request.full_url)

    def test_no_authorization_header_without_token(self):
        _, mocked = self.fetch({"workflow_runs": []})
        self.assertIsNone(mocked.call_args[0][0].get_header("Authorization"))

    def test_token_not_in_error_messages(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}):
            for exc in (http_error(401), http_error(500), urllib.error.URLError("boom"), TimeoutError()):
                with mock.patch("github_client.urllib.request.urlopen", side_effect=exc):
                    with self.assertRaises(GitHubError) as ctx:
                        github_client.fetch_runs("o/r")
                self.assertNotIn(TOKEN, json.dumps(ctx.exception.as_dict()))

    def test_token_with_whitespace_is_ignored(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "abc def\nxyz"}):
            _, mocked = self.fetch({"workflow_runs": []})
        self.assertIsNone(mocked.call_args[0][0].get_header("Authorization"))


class CacheTests(ClientTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"GITHUB_CACHE_TTL": "60"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_second_call_uses_cache_and_refresh_bypasses_it(self):
        with mock.patch("github_client.urllib.request.urlopen", return_value=FakeResponse({"workflow_runs": []})) as m:
            github_client.fetch_runs("o/r")
            github_client.fetch_runs("o/r")
            self.assertEqual(m.call_count, 1)
            github_client.fetch_runs("o/r", refresh=True)
            self.assertEqual(m.call_count, 2)

    def test_cache_expires(self):
        with mock.patch("github_client.urllib.request.urlopen", return_value=FakeResponse({"workflow_runs": []})) as m:
            with mock.patch("github_client.time.monotonic", side_effect=[0, 1000, 1000]):
                github_client.fetch_runs("o/r")
                github_client.fetch_runs("o/r")
            self.assertEqual(m.call_count, 2)

    def test_failures_are_not_cached(self):
        good = FakeResponse({"workflow_runs": [raw_run()]})
        with mock.patch("github_client.urllib.request.urlopen", side_effect=[http_error(500), good]):
            with self.assertRaises(GitHubError):
                github_client.fetch_runs("o/r")
            self.assertEqual(len(github_client.fetch_runs("o/r")), 1)

    def test_different_pages_are_cached_separately(self):
        with mock.patch("github_client.urllib.request.urlopen", return_value=FakeResponse({"workflow_runs": []})) as m:
            github_client.fetch_runs("o/r", page=1)
            github_client.fetch_runs("o/r", page=2)
            self.assertEqual(m.call_count, 2)


if __name__ == "__main__":
    unittest.main()
