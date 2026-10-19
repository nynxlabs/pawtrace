"""Parity: pawtrace's config gates vs rl-adaptive-dbs's hand-coded ``fig4a_gates``.

Runs both on every saved Mehregan Fig 4a series artifact in an rl-adaptive-dbs
checkout (read-only — nothing is written there; bytecode writing is off), plus
synthetic edge cases (truncated, flat, rising, NaN traces), and compares every
gate verdict and the shared metrics.

    uv run --project ../.. python parity.py [--rl-dbs PATH] [--json]

Exit 0 when every verdict matches, 1 otherwise, 2 when the checkout is missing.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np  # noqa: E402

from pawtrace.data import PanelData, Series  # noqa: E402
from pawtrace.panel import Panel  # noqa: E402
from pawtrace.project import Project  # noqa: E402

HERE = Path(__file__).resolve().parent
REF_REL = "artifacts/figures/papers/mehregan/4a/paper_digitization/curves_wpd_refined.json"
# old metrics key -> pawtrace metric name (values compared, not just verdicts)
METRIC_PAIRS = {
    "early_mean": "early", "late_mean": "late", "paper_early": "p_early", "paper_late": "p_late",
    "drop": "drop", "paper_drop": "p_drop", "mid_drop": "mid_drop", "paper_mid_drop": "p_mid_drop",
    "ep0_mean": "ep0", "paper_ep0": "p_ep0", "episode_pearson_r": "ep_r", "late_eps5_9": "late_ep",
    "episode_drop": "ep_drop", "late_rebound": "rebound", "half_drop_episode": "half_ep",
    "paper_half_drop_episode": "p_half_ep", "early_swing": "swing", "paper_early_swing": "p_swing",
    "early_swing_full_res": "swing_full_res", "episode_means": "ep", "paper_episode_means": "p_ep",
}

PAPER_EPISODE_METRICS = {"paper_episode_means", "paper_half_drop_episode"}


def synthetic_cases(base: list[float]) -> dict[str, list[float]]:
    rng = np.random.default_rng(0)
    b = np.asarray(base, dtype=float)
    ramp = np.linspace(0.5, 0.38, 300) + rng.normal(0, 0.02, 300)
    cases = {
        "synthetic/truncated_299": b[:299],
        "synthetic/truncated_270": b[:270],
        "synthetic/truncated_150": b[:150],
        "synthetic/truncated_60": b[:60],
        "synthetic/long_330": np.concatenate([b, b[-30:]]),
        "synthetic/flat": np.full(300, 0.45),
        "synthetic/rising": b[::-1],
        "synthetic/ramp_noise": ramp,
        "synthetic/scaled_x2": b * 2.0,
        "synthetic/with_nan": np.where(np.arange(300) == 77, np.nan, b),
    }
    return {k: [float(v) for v in arr] for k, arr in cases.items()}


def _num(v):
    if v is None:
        return math.nan
    if isinstance(v, (list, tuple, np.ndarray)):
        return [(_num(x)) for x in v]
    return float(v)


def _close(a, b, tol=1e-12) -> bool:
    a, b = _num(a), _num(b)
    if isinstance(a, list) or isinstance(b, list):
        if not (isinstance(a, list) and isinstance(b, list)):
            return False
        # old code truncates episode vectors to complete episodes; pawtrace pads with NaN
        n = min(len(a), len(b))
        return all(_close(x, y, tol) for x, y in zip(a[:n], b[:n])) and all(math.isnan(x) for x in (a[n:] + b[n:]))
    if math.isnan(a) and math.isnan(b):
        return True
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rl-dbs", default=os.environ.get("PAWTRACE_VAR_RL_DBS", "~/research/rl-adaptive-dbs"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rl = Path(os.path.expanduser(args.rl_dbs))
    if not (rl / REF_REL).exists():
        print(f"no rl-adaptive-dbs digitization at {rl / REF_REL}", file=sys.stderr)
        return 2
    os.environ["PAWTRACE_VAR_RL_DBS"] = str(rl)
    sys.path.insert(0, str(rl / "scripts" / "digitization"))
    import paper_gates as old  # noqa: E402  (read-only import of the old gate code)

    proj = Project.find(HERE)
    proj.vars["rl_dbs"] = str(rl)
    panel = Panel.load(proj, "mehregan/4a")
    art = rl / "artifacts/figures/papers/mehregan/4a"
    files = sorted(glob.glob(str(art / "series*.json")) + glob.glob(str(art / "candidates/*/series*.json")))
    cases: dict[str, list[float]] = {}
    for f in files:
        tr = json.loads(Path(f).read_text()).get("beta_norm_trace")
        if tr is not None:
            cases[os.path.relpath(f, rl)] = tr
    current = json.loads((art / "series.json").read_text())["beta_norm_trace"]
    cases.update(synthetic_cases(current))

    rows = []
    gate_mismatch = metric_mismatch = 0
    for name, trace in cases.items():
        o = old.fig4a_gates(trace, paper_path=rl / REF_REL)
        rep = panel.check(PanelData({"beta": Series.from_y(trace)}))
        new = rep.verdicts
        if set(new) != set(o["gates"]):
            raise SystemExit(f"gate sets differ: {sorted(set(new) ^ set(o['gates']))}")
        diffs = {g: [o["gates"][g], new[g]] for g in o["gates"] if bool(o["gates"][g]) != new[g]}
        # Known, intended difference: for a run shorter than 10 full episodes the old
        # code also truncates the *paper's* episode vector to our length; pawtrace
        # keeps the paper's metrics independent of our run. Verdicts are unaffected
        # (our side is missing either way), so those two paper metrics are skipped.
        skip = PAPER_EPISODE_METRICS if len(trace) < 300 else set()
        mdiffs = [k for k, n in METRIC_PAIRS.items() if k not in skip and not _close(o["metrics"].get(k), rep.metrics.get(n))]
        gate_mismatch += len(diffs)
        metric_mismatch += len(mdiffs)
        rows.append({
            "artifact": name, "n": len(trace), "old_pass": o["pass"], "pawtrace_pass": rep.passed,
            "old": o["gates"], "pawtrace": new, "gate_mismatches": diffs, "metric_mismatches": mdiffs,
        })
    n_saved = sum(not r["artifact"].startswith("synthetic/") for r in rows)
    summary = {
        "artifacts": len(rows), "saved_artifacts": n_saved, "synthetic_cases": len(rows) - n_saved,
        "gates_per_artifact": len(rows[0]["old"]) if rows else 0,
        "gate_mismatches": gate_mismatch, "metric_mismatches": metric_mismatch,
        "rl_dbs_commit": _git_head(rl),
    }
    if args.json:
        print(json.dumps({"summary": summary, "rows": rows}, indent=1))
    else:
        for r in rows:
            mark = "same" if not r["gate_mismatches"] else "DIFF"
            npass = sum(r["old"].values())
            print(f"{mark} {r['artifact']:<70} n={r['n']:<4} old {npass:>2}/13 {'PASS' if r['old_pass'] else 'fail'}"
                  f" | pawtrace {sum(r['pawtrace'].values()):>2}/13 {'PASS' if r['pawtrace_pass'] else 'fail'}"
                  + (f"  gates {r['gate_mismatches']}" if r["gate_mismatches"] else "")
                  + (f"  metrics {r['metric_mismatches']}" if r["metric_mismatches"] else ""))
        print(json.dumps(summary))
    return 0 if gate_mismatch == 0 else 1


def _git_head(path: Path) -> str | None:
    head = path / ".git"
    try:
        import subprocess

        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None if not head.exists() else "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
