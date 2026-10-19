"""Mehregan Fig 4a: pawtrace's config gates give the same verdicts as rl-adaptive-dbs's
hand-coded gates on every saved artifact (needs an rl-adaptive-dbs checkout; skipped otherwise)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mehregan-4a"
RL = Path(os.path.expanduser(os.environ.get("PAWTRACE_VAR_RL_DBS", "~/research/rl-adaptive-dbs")))
REF = RL / "artifacts/figures/papers/mehregan/4a/paper_digitization/curves_wpd_refined.json"


@pytest.mark.skipif(not REF.exists(), reason="no rl-adaptive-dbs checkout with Mehregan 4a digitization")
def test_mehregan_4a_parity():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run(
        [sys.executable, str(EXAMPLE / "parity.py"), "--rl-dbs", str(RL), "--json"],
        cwd=EXAMPLE, capture_output=True, text=True, timeout=600, env=env,
    )
    assert out.returncode in (0, 1), out.stderr
    summary = json.loads(out.stdout)["summary"]
    assert summary["saved_artifacts"] > 0
    assert summary["gate_mismatches"] == 0
    assert summary["metric_mismatches"] == 0
