"""Environment-driven configuration helpers.

Nothing in this module logs or returns secret values; ``problems()`` only
describes *which* setting is wrong.
"""

import os
import re

# owner: letters/digits/hyphens; name: letters/digits/._- but never "." or ".." (no path traversal)
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/(?!\.{1,2}$)[A-Za-z0-9_.-]+$")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"", "0", "false", "no", "off"}

DEFAULT_TIMEOUT = 5.0
DEFAULT_CACHE_TTL = 60.0


def github_repo():
    return os.environ.get("GITHUB_REPO", "").strip()


def repo_is_valid(repo):
    return bool(REPO_RE.fullmatch(repo or ""))


def demo_mode():
    """Demo data is shown only when DEMO_MODE is explicitly switched on."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in TRUE_VALUES


def github_token():
    """Return the configured token, or None if unset or malformed."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or any(ch.isspace() for ch in token):
        return None
    return token


def _number(name, default, low, high):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value != value:  # NaN
        return default
    return min(max(value, low), high)


def github_timeout():
    return _number("GITHUB_TIMEOUT", DEFAULT_TIMEOUT, 1.0, 30.0)


def cache_ttl():
    return _number("GITHUB_CACHE_TTL", DEFAULT_CACHE_TTL, 0.0, 3600.0)


def problems():
    """Return human-readable descriptions of invalid environment settings."""
    found = []
    repo = github_repo()
    if repo and not repo_is_valid(repo):
        found.append("GITHUB_REPO must look like 'owner/name'.")
    raw_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if raw_token and github_token() is None:
        found.append("GITHUB_TOKEN contains whitespace and was ignored.")
    mode = os.environ.get("DEMO_MODE", "").strip().lower()
    if mode not in TRUE_VALUES | FALSE_VALUES:
        found.append("DEMO_MODE must be true or false; treated as false.")
    for name in ("GITHUB_TIMEOUT", "GITHUB_CACHE_TTL"):
        raw = os.environ.get(name, "").strip()
        if raw:
            try:
                float(raw)
            except ValueError:
                found.append("%s must be a number; the default is used." % name)
    return found
