"""Tests for pawtrace.localweb: what :func:`announce` prints (and where the session token goes),
and how :func:`run_open_cmd` builds the --open-cmd argv and environment.

No server here; the HTTP guards are in tests/test_webui.py.
"""
from __future__ import annotations

import io
import os
import stat

from pawtrace.localweb import announce, run_open_cmd

URL = "http://127.0.0.1:8123/?t=SYNTHETIC-TOKEN-123"
TOKEN = "SYNTHETIC-TOKEN-123"


class TtyStream(io.StringIO):
    """A stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def test_announce_non_tty_prints_base_url_without_token():
    out = io.StringIO()
    announce("pawtrace ui", URL, stream=out)
    text = out.getvalue()
    assert "127.0.0.1:8123" in text
    assert TOKEN not in text
    assert "?t=" not in text
    # no url_file given, so the hint points at the two ways to get the full URL
    assert "--url-file" in text


def test_announce_url_file_gets_full_url_with_mode_0600(tmp_path):
    out = io.StringIO()
    target = tmp_path / "run" / "url.txt"  # parent dir doesn't exist yet
    announce("pawtrace ui", URL, url_file=target, stream=out)
    assert target.read_text() == URL + "\n"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    text = out.getvalue()
    assert TOKEN not in text
    assert str(target) in text


def test_announce_tty_prints_full_url():
    out = TtyStream()
    announce("pawtrace ui", URL, stream=out)
    assert URL in out.getvalue()


def test_run_open_cmd_fills_placeholders_and_sets_env():
    calls: list[tuple[list[str], dict]] = []

    def fake_popen(argv, **kw):
        calls.append((argv, kw))
        return None

    run_open_cmd("opener {url} --port {port}", URL, 1234, name="x", popen=fake_popen)

    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv == ["opener", URL, "--port", "1234"]
    assert kw["env"]["PAWTRACE_URL"] == URL
