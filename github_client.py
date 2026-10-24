"""Minimal GitHub Actions API client (standard library only).

Every failure is raised as :class:`GitHubError` with a stable ``code`` so the
dashboard can report *why* live data is unavailable.  The token is only ever
placed in the request header; it is never logged, cached or returned.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import config

API_ROOT = "https://api.github.com"
RUN_URL_PREFIX = "https://github.com/"
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 50


class GitHubError(Exception):
    """A GitHub API problem with a machine-readable code."""

    def __init__(self, code, message, status=None, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "retry_after": self.retry_after,
        }


class RunList(list):
    """List of normalised runs plus response metadata."""

    total_count = None
    fetched_at = None


_cache = {}
_lock = threading.Lock()


def clear_cache():
    with _lock:
        _cache.clear()


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text(value):
    return value if isinstance(value, str) and value else None


def normalize_run(raw):
    """Convert one GitHub workflow-run object, or return None if unusable."""
    if not isinstance(raw, dict):
        return None
    run_id = raw.get("id")
    if isinstance(run_id, bool) or not isinstance(run_id, int):
        return None
    status = _text(raw.get("status"))
    duration = None
    if status == "completed":
        start, end = _parse_ts(raw.get("run_started_at")), _parse_ts(raw.get("updated_at"))
        if start and end and end >= start:
            duration = int((end - start).total_seconds())
    commit = raw.get("head_commit") if isinstance(raw.get("head_commit"), dict) else {}
    message = _text(commit.get("message"))
    sha = _text(raw.get("head_sha"))
    url = _text(raw.get("html_url"))
    return {
        "id": run_id,
        "name": _text(raw.get("display_title")) or _text(raw.get("name")),
        "workflow": _text(raw.get("name")),
        "run_number": raw.get("run_number") if isinstance(raw.get("run_number"), int) else None,
        "branch": _text(raw.get("head_branch")),
        "sha": sha,
        "commit_message": message.splitlines()[0][:200] if message else None,
        "event": _text(raw.get("event")),
        "status": status,
        "conclusion": _text(raw.get("conclusion")),
        "duration": duration,
        "url": url if url and url.startswith(RUN_URL_PREFIX) else None,
        "created": _text(raw.get("created_at")),
        "started": _text(raw.get("run_started_at")),
        "updated": _text(raw.get("updated_at")),
    }


def _http_error(exc):
    status = exc.code
    message = ""
    try:
        body = json.loads(exc.read()[:4096].decode("utf-8", "replace"))
        if isinstance(body, dict) and isinstance(body.get("message"), str):
            message = body["message"]
    except (ValueError, OSError):
        pass
    headers = exc.headers or {}
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    retry = headers.get("Retry-After")
    limited = status == 429 or (
        status == 403 and (remaining == "0" or retry is not None or "rate limit" in message.lower())
    )
    if limited:
        wait = None
        if retry and retry.isdigit():
            wait = int(retry)
        elif reset and reset.isdigit():
            wait = max(0, int(reset) - int(time.time()))
        text = "GitHub API rate limit reached."
        if wait is not None:
            text += " Try again in about %d minute(s)." % max(1, round(wait / 60))
        if not config.github_token():
            text += " Setting GITHUB_TOKEN raises the limit."
        return GitHubError("rate_limited", text, status, wait)
    if status == 401:
        return GitHubError("auth", "GitHub rejected the credentials (HTTP 401). Check GITHUB_TOKEN.", status)
    if status == 403:
        return GitHubError("auth", "GitHub denied access (HTTP 403). The token may lack Actions read access.", status)
    if status == 404:
        text = "Repository or workflow runs not found (HTTP 404). Check GITHUB_REPO; "
        text += "private repositories need GITHUB_TOKEN."
        return GitHubError("not_found", text, status)
    if status >= 500:
        return GitHubError("server_error", "GitHub returned a server error (HTTP %d)." % status, status)
    return GitHubError("http_error", "GitHub returned an unexpected response (HTTP %d)." % status, status)


def _get_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "devops-dashboard",
    }
    token = config.github_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=config.github_timeout()) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise GitHubError("timeout", "GitHub did not respond in time.") from None
        raise GitHubError("network", "Could not reach GitHub (%s)." % type(exc.reason).__name__) from None
    except TimeoutError:
        raise GitHubError("timeout", "GitHub did not respond in time.") from None
    except OSError as exc:
        raise GitHubError("network", "Network error while contacting GitHub (%s)." % type(exc).__name__) from None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise GitHubError("malformed", "GitHub returned a response that is not valid JSON.") from None


def _fetch(repo, page, per_page):
    query = urllib.parse.urlencode({"per_page": per_page, "page": page})
    url = "%s/repos/%s/actions/runs?%s" % (API_ROOT, repo, query)
    payload = _get_json(url)
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise GitHubError("malformed", "GitHub response did not contain a workflow_runs list.")
    runs = RunList(r for r in (normalize_run(x) for x in payload["workflow_runs"]) if r)
    total = payload.get("total_count")
    runs.total_count = total if isinstance(total, int) and not isinstance(total, bool) else None
    runs.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return runs


def fetch_runs(repo, page=1, per_page=DEFAULT_PER_PAGE, refresh=False):
    """Return a :class:`RunList` for ``owner/name`` or raise :class:`GitHubError`.

    Successful responses are cached for ``GITHUB_CACHE_TTL`` seconds; failures
    are never cached.  ``refresh=True`` bypasses the cache.
    """
    if not config.repo_is_valid(repo):
        raise GitHubError("invalid_repo", "GITHUB_REPO must look like 'owner/name'.")
    ttl = config.cache_ttl()
    key = (repo, page, per_page)
    with _lock:  # one fetch at a time, so concurrent identical requests share a result
        if ttl and not refresh:
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        result = _fetch(repo, page, per_page)
        if ttl:
            _cache[key] = (time.monotonic(), result)
        return result
