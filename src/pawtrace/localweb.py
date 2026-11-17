"""Shared guards for pawtrace's local web pages (``pawtrace digitize``, ``pawtrace serve``).

Stdlib only (``http.server``). Every server binds to loopback and checks two things on
every request:

- the ``Host`` header is loopback, so a page on another site can't reach the server
  through DNS rebinding;
- the session token (``?t=`` on page loads, the ``X-Pawtrace-Token`` header on API calls, or
  the HttpOnly SameSite=Strict session cookie a ``?t=`` page load sets) matches, compared in
  constant time.

POSTs take JSON only (``Content-Type: application/json``), which a cross-site form can't
send without a CORS preflight that these servers never answer. Every response carries
``no-store``, ``nosniff`` and ``no-referrer``, so the token in a page URL never leaves in
a Referer header.

The token is a secret for the length of one session. Nothing here logs it: the request
log is off, and callers print the URL only to an interactive terminal (see
:func:`announce`).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_BODY = 32 * 1024 * 1024
TOKEN_HEADER = "X-Pawtrace-Token"


def new_token() -> str:
    return secrets.token_urlsafe(18)


class BadRequest(ValueError):
    """A request the handler refuses with a 4xx and a short JSON error."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class GuardedHandler(BaseHTTPRequestHandler):
    """Base handler: host + token guards, safe headers, JSON helpers. Subclasses route."""

    protocol_version = "HTTP/1.1"

    # The server sets ``token`` (str). Kept as a server attribute so tests can swap it.
    def _session_token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet: the request line carries the token
        pass

    # -- guards -----------------------------------------------------------------
    def host_ok(self) -> bool:
        host = (self.headers.get("Host") or "")
        # "[::1]:8080" -> "::1", "127.0.0.1:8080" -> "127.0.0.1"
        if host.startswith("["):
            host = host[1:].split("]", 1)[0]
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host in LOOPBACK_HOSTS

    def allowed(self, token: str | None) -> bool:
        if not self.host_ok():
            self.send_bytes(HTTPStatus.FORBIDDEN, b"bad host", "text/plain")
            return False
        good = self._session_token()
        candidates = [token, self.cookie_token()]
        if not any(c and secrets.compare_digest(c, good) for c in candidates):
            self.send_bytes(HTTPStatus.FORBIDDEN, b"bad token", "text/plain")
            return False
        return True

    def cookie_name(self) -> str:
        """Per port: cookies are shared by every port on one host, so two servers mustn't clash."""
        return f"pawtrace_{self.server.server_address[1]}"  # type: ignore[attr-defined]

    def cookie_token(self) -> str | None:
        """The token from the session cookie a page load with ``?t=`` set (see :meth:`set_token_cookie`)."""
        raw = self.headers.get("Cookie") or ""
        name = self.cookie_name()
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name and v:
                return v
        return None

    def token_cookie_header(self) -> str:
        """``Set-Cookie`` for the session token: HttpOnly (page scripts can't read it), SameSite=Strict,
        no expiry (gone when the browser closes). Lets a front end's own requests (fetch, EventSource,
        pdf.js, images) carry the token without putting it in URLs."""
        return f"{self.cookie_name()}={self._session_token()}; Path=/; HttpOnly; SameSite=Strict"

    def get_token(self) -> str | None:
        """GET: the ``t`` query parameter (page loads, images) or the header (API calls); the cookie is checked in :meth:`allowed`."""
        q = parse_qs(urlparse(self.path).query)
        return (q.get("t") or [None])[0] or self.headers.get(TOKEN_HEADER)

    def query(self) -> dict[str, str]:
        return {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}

    # -- responses --------------------------------------------------------------
    def send_bytes(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj: Any, status: int = 200) -> None:
        self.send_bytes(status, json.dumps(obj, default=str).encode(), "application/json")

    def read_json(self) -> dict[str, Any] | None:
        """The POST body as a JSON object, or None after sending the error response."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self.send_bytes(415, b"json only", "text/plain")
            return None
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_bytes(400, b"bad length", "text/plain")
            return None
        if n < 0 or n > MAX_BODY:
            self.send_bytes(413, b"too large", "text/plain")
            return None
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"error": "bad json"}, 400)
            return None
        if not isinstance(body, dict):
            self.send_json({"error": "body must be a JSON object"}, 400)
            return None
        return body


def announce(name: str, url: str, *, url_file: str | Path | None = None, stream=None) -> None:
    """Tell the person where the page is, without putting the token in a log.

    The URL goes to stderr only when stderr is a terminal. Under systemd, cron, or any
    pipe, stderr ends up in a journal or a file, so we print the port without the token
    and, when asked, write the full URL to ``url_file`` (mode 0600).
    """
    stream = stream or sys.stderr
    if url_file:
        p = Path(url_file).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(url + "\n")
        os.chmod(p, 0o600)
    if stream.isatty():
        print(f"{name}: {url}", file=stream, flush=True)
    else:
        base = url.split("?", 1)[0]
        where = f"; full URL in {url_file}" if url_file else "; the URL's token isn't logged (use --open-cmd or --url-file)"
        print(f"{name}: listening on {base}{where}", file=stream, flush=True)


def run_open_cmd(cmd: str, url: str, port: int, *, name: str, popen: Callable[..., Any] | None = None) -> None:
    """Run ``--open-cmd`` with ``{url}`` / ``{port}`` filled in (argv, no shell).

    The URL also goes in ``PAWTRACE_URL`` for commands that would rather not take it as
    an argument (process lists show arguments to other users of the same machine).
    """
    import shlex
    import subprocess

    popen = popen or subprocess.Popen
    argv = [a.replace("{url}", url).replace("{port}", str(port)) for a in shlex.split(cmd)]
    env = {**os.environ, "PAWTRACE_URL": url}
    try:
        popen(argv, stdin=subprocess.DEVNULL, env=env)
    except OSError as exc:
        print(f"{name}: --open-cmd failed: {exc}", file=sys.stderr)


def stop_on_sigterm() -> None:
    """Treat SIGTERM (systemd stop, kill) like Ctrl+C, so the server closes cleanly and exits 0."""
    import signal

    def handler(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread (tests)
        pass
