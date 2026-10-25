"""Tests for the extract module: the CLI end to end on a synthetic PDF, the command backend's
markdown reader, and the shared heading / caption helpers.

The PDF is generated once per session by ``make_pdf`` (matplotlib); no binary is committed.
Each project is a fresh ``pawtrace init`` in tmp_path, made into a git repo.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")

from pawtrace import cli  # noqa: E402
from pawtrace.extract.command import parse_markdown  # noqa: E402
from pawtrace.extract.common import caption_panels, heading_parts  # noqa: E402


def make_pdf(path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    import numpy as np
    plt.rcParams["pdf.fonttype"] = 42
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(8.5, 11)); y = [0.95]
        def t(s, size=10, weight="normal", x=0.1):
            fig.text(x, y[0], s, fontsize=size, weight=weight, va="top"); y[0] -= 0.025 if size <= 10 else 0.035
        t("A Synthetic Paper on Toy Dynamics", 16, "bold"); t("Abstract", 12, "bold")
        t("We study a toy model. The decay rate is 0.5 per second.")
        t("1 Introduction", 12, "bold"); t("Toy models are useful. We set the gain to k = 2.0 throughout.")
        t("2 Methods", 12, "bold"); t("2.1 Model", 11, "bold"); t("The state evolves as")
        t("dx/dt = -a x + k u   (1)", x=0.3); t("with a = 0.5 s and time step dt = 0.01 s.")
        ax = fig.add_axes([0.15, 0.25, 0.3, 0.2]); ax.plot(np.linspace(0, 1, 50), np.exp(-np.linspace(0, 1, 50))); ax.set_xlabel("time (s)")
        fig.text(0.10, 0.46, "a", fontsize=12, weight="bold", va="bottom")
        ax2 = fig.add_axes([0.55, 0.25, 0.3, 0.2]); ax2.imshow(np.random.default_rng(0).random((8, 8)))
        fig.text(0.50, 0.46, "b", fontsize=12, weight="bold", va="bottom")
        fig.text(0.1, 0.2, "Figure 1: Toy results. (a) Decay of x over time. (b) A heat map.", fontsize=9, va="top")
        pdf.savefig(fig); plt.close(fig)
        fig = plt.figure(figsize=(8.5, 11))
        fig.text(0.1, 0.95, "3 Results", fontsize=12, weight="bold", va="top")
        fig.text(0.1, 0.89, "References", fontsize=12, weight="bold", va="top")
        pdf.savefig(fig); plt.close(fig)


# The command backend's converter output: sections, one display equation, one captioned figure.
COMMAND_MD = r"""# 1 Introduction
We set k = 3 here.
# 2 Methods
$$ \dot x = -a x \tag{1} $$
![](img1.png)
Figure 2: A panel figure. (a) left. (b) right.
"""

LATEX = r"\frac{dx}{dt} = -a x + k u"

SEED_EXPECTED = ["eq.1", "panel.1a", "panel.1b", "param.k", "param.a"]


@pytest.fixture(scope="session")
def toy_pdf(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("toy-pdf") / "toy.pdf"
    make_pdf(path)
    return path


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def jcall(capsys: pytest.CaptureFixture[str], root: Path, *argv: str) -> tuple[int, Any]:
    code, out, _ = call(capsys, *argv, "--project", str(root), "--json")
    return code, json.loads(out)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_project(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str = "proj", profile: str = "standard") -> Path:
    root = tmp_path / name
    code, _, err = call(capsys, "init", str(root), "--paper", "toy", "--profile", profile)
    assert code == 0, err
    _git(root, "init", "-q")
    return root


def source(root: Path) -> Path:
    return root / "papers" / "toy" / "source"


def read_json(root: Path, name: str) -> Any:
    return json.loads((source(root) / name).read_text())


def edit_config(root: Path, **extract: Any) -> None:
    cfg_path = root / "pawtrace.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["extract"].update(extract)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def edit_spec(root: Path, edit) -> None:
    spec_path = root / "papers" / "toy" / "spec.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    edit(spec)
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))


def write_converter(path: Path) -> Path:
    """A stand-in PDF -> markdown converter: argv[1] is the PDF, argv[2] the output folder."""
    path.write_text(
        "import sys\nfrom pathlib import Path\nfrom PIL import Image\n"
        "out = Path(sys.argv[2])\n"
        "Image.new('RGB', (10, 10), 'white').save(out / 'img1.png')\n"
        f"(out / 'paper.md').write_text({COMMAND_MD!r})\n"
    )
    return path


def seed_all(root: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    code, res = jcall(capsys, root, "extract", "seed", "toy")
    assert code == 0
    return res


def allow_ai(root: Path) -> None:
    """New specs start at ai_tools: never; tests that run a command backend open it up."""
    edit_spec(root, lambda spec: spec["rights"]["rules"].update(ai_tools="allowed"))


@pytest.fixture
def root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    return make_project(tmp_path, capsys)


@pytest.fixture
def ran(root: Path, toy_pdf: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    code, man = jcall(capsys, root, "extract", "run", "toy", str(toy_pdf))
    assert code == 0
    return man


# ---------------------------------------------------------------------------
# extract run: outputs, hashes, determinism, git
# ---------------------------------------------------------------------------


def test_run_counts_and_files(root, ran, toy_pdf):
    counts = ran["counts"]
    assert (counts["sections"], counts["equations"], counts["figures"], counts["panels"]) == (7, 1, 1, 2)
    assert ran["pdf"]["sha256"] == _sha256(toy_pdf)
    assert re.fullmatch(r"[0-9a-f]{12}", ran["extraction"])

    src = source(root)
    assert "*" in (src / ".gitignore").read_text().splitlines()
    for name in ("paper.pdf", "text.md", "sections.json", "equations.json", "figures.json", "candidates.yaml",
                 "manifest.json", "equations/eq-1.png", "figures/fig-1.png", "figures/fig-1a.png", "figures/fig-1b.png"):
        assert (src / name).is_file(), name
    assert json.loads((src / "manifest.json").read_text())["extraction"] == ran["extraction"]

    for rel, digest in ran["files"].items():
        assert (src / rel).is_file(), rel
        assert _sha256(src / rel) == digest, rel


def test_equations_sections_and_figures_read_back(root, ran):
    eq = {e["id"]: e for e in read_json(root, "equations.json")}["eq.1"]
    assert eq["text"] == "dx/dt = -a x + k u"
    assert eq["section"] == "s2.1"
    assert eq["latex"] is None

    fig = {f["id"]: f for f in read_json(root, "figures.json")}["fig.1"]
    panels = fig["panels"]
    assert [p["label"] for p in panels] == ["a", "b"]
    assert [p["caption"] for p in panels] == ["Decay of x over time", "A heat map"]

    secs = {s["id"]: s for s in read_json(root, "sections.json")}
    assert "with a = 0.5 s" in secs["s2.1"]["text"]


def test_same_pdf_gives_the_same_extraction_id(root, ran, capsys):
    # no PDF argument: reuses source/paper.pdf
    code, again = jcall(capsys, root, "extract", "run", "toy")
    assert code == 0
    assert again["extraction"] == ran["extraction"]
    assert again["pdf"]["sha256"] == ran["pdf"]["sha256"]


def test_source_folder_is_git_ignored(root, ran):
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    assert not [line for line in status.splitlines() if "papers/toy/source/" in line]
    check = subprocess.run(["git", "check-ignore", "-q", "papers/toy/source/paper.pdf"], cwd=root)
    assert check.returncode == 0


# ---------------------------------------------------------------------------
# extract seed + lint
# ---------------------------------------------------------------------------


def test_seed_dry_run_changes_nothing(root, ran, capsys):
    spec_path = root / "papers" / "toy" / "spec.yaml"
    before = spec_path.read_bytes()
    code, res = jcall(capsys, root, "extract", "seed", "toy", "--dry-run")
    assert code == 0
    assert res["dry_run"] is True
    assert res["added"] == SEED_EXPECTED
    assert spec_path.read_bytes() == before


def test_seed_adds_candidates(root, ran, capsys):
    code, res = jcall(capsys, root, "extract", "seed", "toy")
    assert code == 0
    assert res["added"] == SEED_EXPECTED

    spec_path = root / "papers" / "toy" / "spec.yaml"
    text = spec_path.read_text()
    assert "# Paper rights" in text
    spec = yaml.safe_load(text)
    items = {i["id"]: i for i in spec["items"]}
    assert "example.param" in items
    for iid in SEED_EXPECTED:
        assert items[iid]["status"] == "candidate", iid
        assert items[iid]["extracted"]["extraction"] == ran["extraction"], iid


def test_seed_is_idempotent(root, ran, capsys):
    seed_all(root, capsys)
    code, res = jcall(capsys, root, "extract", "seed", "toy")
    assert code == 0
    assert res["added"] == []
    assert sorted(res["skipped_existing"]) == sorted(SEED_EXPECTED)


def test_seed_by_kind_on_a_fresh_project(tmp_path, capsys, toy_pdf):
    fresh = make_project(tmp_path, capsys, name="fresh")
    code, _ = jcall(capsys, fresh, "extract", "run", "toy", str(toy_pdf))
    assert code == 0
    code, res = jcall(capsys, fresh, "extract", "seed", "toy", "--kind", "equation")
    assert code == 0
    assert res["added"] == ["eq.1"]


def test_seed_unknown_candidate_id_exits_2(root, ran, capsys):
    code, _, err = call(capsys, "extract", "seed", "toy", "--only", "nope", "--project", str(root))
    assert code == 2
    assert "no candidates nope" in err


def test_lint_after_seeding_warns_about_candidates_only(root, ran, capsys):
    seed_all(root, capsys)
    code, data = jcall(capsys, root, "lint", "toy")
    problems = data["problems"]
    assert any(p["level"] == "warn" and "unreviewed candidate" in p["message"] for p in problems)
    assert not [p for p in problems if "restricted paper content" in p["message"] or "would be committed" in p["message"]]


def test_stale_seed_is_flagged_when_the_extraction_changes(root, ran, capsys):
    seed_all(root, capsys)
    manifest_path = source(root) / "manifest.json"
    man = json.loads(manifest_path.read_text())
    man["extraction"] = "000000000000"
    manifest_path.write_text(json.dumps(man))
    code, data = jcall(capsys, root, "lint", "toy")
    stale = [p["message"] for p in data["problems"] if "seeded from extraction" in p["message"]]
    assert stale, data["problems"]


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def test_module_off_refuses(tmp_path, capsys, toy_pdf):
    minimal = make_project(tmp_path, capsys, name="minimal", profile="minimal")
    code, _, err = call(capsys, "extract", "run", "toy", str(toy_pdf), "--project", str(minimal))
    assert code == 2
    assert "extract module is off" in err


def test_spec_without_rights_refuses(root, toy_pdf, capsys):
    edit_spec(root, lambda spec: spec.pop("rights"))
    code, _, err = call(capsys, "extract", "run", "toy", str(toy_pdf), "--project", str(root))
    assert code == 2
    assert "no rights" in err


def test_out_outside_git_ignore_refuses(root, toy_pdf, capsys):
    code, _, err = call(capsys, "extract", "run", "toy", str(toy_pdf), "--out", str(root / "notignored"),
                        "--project", str(root))
    assert code == 2
    assert "refusing to write" in err


def test_out_in_a_gitignored_folder_is_allowed(root, toy_pdf, capsys):
    (root / ".gitignore").write_text((root / ".gitignore").read_text() + "ignored/\n")
    code, man = jcall(capsys, root, "extract", "run", "toy", str(toy_pdf), "--out", str(root / "ignored"))
    assert code == 0
    assert (root / "ignored" / "manifest.json").is_file()
    assert man["counts"]["figures"] == 1


def test_never_ai_tools_refuses_the_command_backend(root, toy_pdf, capsys):
    edit_spec(root, lambda spec: spec["rights"]["rules"].update(ai_tools="never"))
    code, _, err = call(capsys, "extract", "run", "toy", str(toy_pdf), "--backend", "command", "--project", str(root))
    assert code == 2
    assert "ai_tools is never" in err


# ---------------------------------------------------------------------------
# command backend and formula_command
# ---------------------------------------------------------------------------


def test_command_backend_reads_markdown(root, toy_pdf, tmp_path, capsys):
    allow_ai(root)
    converter = write_converter(tmp_path / "converter.py")
    edit_config(root, command=[sys.executable, str(converter), "{pdf}", "{out}"])
    code, man = jcall(capsys, root, "extract", "run", "toy", str(toy_pdf), "--backend", "command")
    assert code == 0
    assert man["backend"]["name"] == "command"
    assert (man["counts"]["equations"], man["counts"]["figures"], man["counts"]["panels"]) == (1, 1, 2)

    eq = {e["id"]: e for e in read_json(root, "equations.json")}["eq.1"]
    assert eq["latex"] == r"\dot x = -a x"
    assert eq["number"] == "1"

    fig = read_json(root, "figures.json")[0]
    assert fig["number"] == "2"
    assert fig["image"] == "figures/fig-2.png"
    assert (source(root) / fig["image"]).is_file()
    assert [p["label"] for p in fig["panels"]] == ["a", "b"]


def test_failing_command_exits_2(root, toy_pdf, tmp_path, capsys):
    allow_ai(root)
    failing = tmp_path / "fail.py"
    failing.write_text("import sys\nsys.exit(3)\n")
    edit_config(root, command=[sys.executable, str(failing), "{pdf}", "{out}"])
    code, _, err = call(capsys, "extract", "run", "toy", str(toy_pdf), "--backend", "command", "--project", str(root))
    assert code == 2
    assert "exited 3" in err


def test_formula_command_fills_latex(root, toy_pdf, tmp_path, capsys):
    allow_ai(root)
    formula = tmp_path / "formula.py"
    formula.write_text("print(" + repr(LATEX) + ")\n")
    edit_config(root, formula_command=[sys.executable, str(formula), "{image}"])
    code, man = jcall(capsys, root, "extract", "run", "toy", str(toy_pdf))
    assert code == 0
    eq = {e["id"]: e for e in read_json(root, "equations.json")}["eq.1"]
    assert eq["latex"] == LATEX
    assert man["counts"]["equations_with_latex"] == 1


# ---------------------------------------------------------------------------
# shared helpers (common.py) and the markdown reader
# ---------------------------------------------------------------------------


def test_heading_parts_roman_numbers_need_a_title():
    assert heading_parts("III. METHODS", True) == ("III", "METHODS", "roman")


def test_heading_parts_rejects_body_lines_that_start_with_a_number():
    assert heading_parts("2 s of time", True) is None


def test_caption_panels_splits_on_panel_letters():
    assert caption_panels("Fig. 3. (a) x (b) y") == {"a": "x", "b": "y"}


def test_parse_markdown_builds_roman_letter_section_numbers(tmp_path):
    md = "# II. Model\nSome text.\n## A. Plant\nMore text.\n"
    out = tmp_path / "out"
    out.mkdir()
    ex = parse_markdown(md, tmp_path, out)
    numbers = [s["number"] for s in ex.sections if s.get("number")]
    assert numbers == ["II", "II.A"]


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_gitignores_the_paper_source_folder(tmp_path, capsys):
    root = tmp_path / "fresh"
    code, _, err = call(capsys, "init", str(root), "--paper", "toy", "--profile", "standard")
    assert code == 0, err
    lines = (root / ".gitignore").read_text().splitlines()
    assert "papers/*/source/" in lines
    assert "runs/" in lines


def test_new_spec_starts_with_ai_tools_never(root):
    spec = yaml.safe_load((root / "papers" / "toy" / "spec.yaml").read_text())
    assert spec["rights"]["rules"]["ai_tools"] == "never"
