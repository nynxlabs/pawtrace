"""Shared fixtures for the whole suite."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _git_stops_at_tmp(tmp_path_factory, monkeypatch):
    """Keep git from finding a repo around pytest's temp folder.

    Tests that build projects under ``tmp_path`` assume git sees only the repos they create
    there. When the temp folder itself sits inside some other repo (``TMPDIR`` under a
    checkout, a home folder kept in git), git would walk up into it and the "no git" cases
    would see that repo instead. A ceiling at the base temp folder stops discovery there,
    and the variables a git hook exports (``GIT_DIR`` and friends) are cleared so running the
    suite from a hook doesn't point every git call at the outer repo.
    """
    base = str(tmp_path_factory.getbasetemp().resolve())
    prev = os.environ.get("GIT_CEILING_DIRECTORIES")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", os.pathsep.join([base, prev]) if prev else base)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        monkeypatch.delenv(var, raising=False)
