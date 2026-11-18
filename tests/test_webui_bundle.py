"""The committed front-end build (src/pawtrace/webui/app/, from web/): complete, licensed, current."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import pawtrace.webui.server as ws

APP = Path(ws.__file__).with_name("app")
REPO = Path(__file__).resolve().parents[1]


def test_serve_uses_the_committed_build():
    assert (APP / "index.html").is_file()
    assert ws.default_frontend() == APP


def test_every_asset_index_html_names_is_there():
    html = (APP / "index.html").read_text()
    refs = re.findall(r'(?:src|href)="/([^"?#]+)"', html)
    assert any(r.startswith("assets/") and r.endswith(".js") for r in refs)
    missing = [r for r in refs if not (APP / r).is_file()]
    assert not missing, missing


def test_build_carries_its_licenses():
    notices = (APP / "THIRD-PARTY-NOTICES.txt").read_text()
    lic = json.loads((APP / "licenses.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{40}", lic["densk_core"])
    assert f"densk-core {lic['densk_core']} (Apache-2.0)" in notices and "nynxlabs LLC" in notices
    assert lic["packages"] and all(f"{p['name']}@{p['version']}" in notices for p in lic["packages"])
    for f in ("cmaps/LICENSE", "standard_fonts/LICENSE_FOXIT", "wasm/LICENSE_OPENJPEG"):
        assert (APP / "pdfjs" / f).is_file(), f


# Licenses that are fine inside an Apache-2.0 package (web/licenses.ts has the same list), and the
# packages reviewed by hand there.
PERMISSIVE = {"0BSD", "Apache-2.0", "BlueOak-1.0.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "ISC", "MIT",
              "MIT-0", "Python-2.0", "Unlicense", "Zlib"}
REVIEWED = {"khroma"}  # MIT per its license file; package.json has no license field


def _permissive(expr: str) -> bool:
    terms = expr.replace("(", " ").replace(")", " ").split(" AND ")
    return all(any(alt.strip() in PERMISSIVE for alt in t.split(" OR ")) for t in terms)


def test_build_is_permissive_only():
    lic = json.loads((APP / "licenses.json").read_text())
    odd = [f"{p['name']} ({p['license']})" for p in lic["packages"]
           if not _permissive(p["license"]) and p["name"] not in REVIEWED]
    assert not odd, odd
    # pdf.js's Liberation Sans (GPL-2.0 with a font exception) stays out: pdfjsAssets({ liberationFonts: false })
    fonts = APP / "pdfjs" / "standard_fonts"
    assert not list(fonts.glob("Liberation*")) and not (fonts / "LICENSE_LIBERATION").exists()
    assert (fonts / "FoxitSymbol.pfb").is_file() and (fonts / "FoxitDingbats.pfb").is_file()


def test_build_names_no_machine_paths():
    leaks = []
    for f in APP.rglob("*"):
        if f.suffix in {".js", ".css", ".html", ".json"}:
            if re.search(r"/home/[a-z]|/Users/[A-Za-z]|[A-Z]:\\\\Users\\\\", f.read_text(errors="replace")):
                leaks.append(str(f.relative_to(APP)))
    assert not leaks, leaks


@pytest.mark.skipif(not shutil.which("git") or not (REPO / ".git").exists(), reason="not a git checkout")
def test_build_matches_the_pinned_densk_core():
    """A densk-core bump needs a rebuild (web/README.md): the build records the commit it was made with."""
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-s", "web/densk-core"],
                         capture_output=True, text=True, check=True).stdout.split()
    if not out:
        pytest.skip("no densk-core gitlink")
    assert json.loads((APP / "licenses.json").read_text())["densk_core"] == out[1]
