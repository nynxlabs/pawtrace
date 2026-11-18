// The committed front-end build carries the licenses of everything compiled into it: densk-core's
// LICENSE and NOTICE, and the license text of each npm package whose code ends up in a chunk, written
// to THIRD-PARTY-NOTICES.txt beside index.html. The build fails on a package whose license isn't on the
// list below (permissive, fine to ship inside an Apache-2.0 package) until someone reviews it and adds
// it to `reviewed` with the reason.

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Plugin } from "vite";

const PERMISSIVE = new Set([
  "0BSD", "Apache-2.0", "BlueOak-1.0.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "ISC", "MIT",
  "MIT-0", "Python-2.0", "Unlicense", "Zlib",
]);

/** Packages whose license isn't on the permissive list, each with the review that allows it. */
const reviewed: Record<string, string> = {
  khroma: "MIT per its license file; its package.json has no license field",
};

/** Files pdfjsAssets() (densk-core/vite) copies from pdfjs-dist into pdfjs/; their licenses are copied beside them. */
// (Not standard_fonts/LICENSE_LIBERATION: vite.config.ts leaves the Liberation fonts out.)
const PDFJS_ASSET_LICENSES = ["cmaps/LICENSE", "iccs/LICENSE", "standard_fonts/LICENSE_FOXIT",
  "wasm/LICENSE_JBIG2", "wasm/LICENSE_PDFJS_JBIG2",
  "wasm/LICENSE_OPENJPEG", "wasm/LICENSE_PDFJS_OPENJPEG", "wasm/LICENSE_QCMS", "wasm/LICENSE_PDFJS_QCMS"];

type Pkg = { name: string; version: string; license: string; dir: string };

/**
 * Copyright lines for MIT packages that publish no license file, from the project's own LICENSE in
 * its repository (checked by hand); other file-less packages fall back to package.json's author.
 */
const COPYRIGHT: [RegExp, string][] = [
  [/^@excalidraw\//, "Copyright (c) 2020 Excalidraw"],
  [/^@radix-ui\//, "Copyright (c) 2022 WorkOS"],
];

const MIT_TEXT = `Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
USE OR OTHER DEALINGS IN THE SOFTWARE.`;

/** The MIT text for a package that ships none, with the best copyright line we have. */
function mitFor(p: Pkg): string {
  const known = COPYRIGHT.find(([re]) => re.test(p.name))?.[1];
  const meta = JSON.parse(readFileSync(join(p.dir, "package.json"), "utf8"));
  const author = typeof meta.author === "string" ? meta.author.replace(/\s*<[^>]*>|\s*\([^)]*\)/g, "") : meta.author?.name;
  const line = known ?? (author ? `Copyright (c) ${author}` : `Copyright (c) the ${p.name} authors`);
  return `MIT License\n\n${line}\n\n${MIT_TEXT}`;
}

const LICENSE_FILE = /^(licen[cs]e|copying|notice)([._-].*)?$/i;

/** The package a module file belongs to: the nearest package.json with a name, walking up. */
function packageOf(file: string, cache: Map<string, Pkg | null>): Pkg | null {
  let dir = dirname(file);
  const seen: string[] = [];
  for (;;) {
    if (cache.has(dir)) {
      const hit = cache.get(dir)!;
      for (const d of seen) cache.set(d, hit);
      return hit;
    }
    seen.push(dir);
    const pj = join(dir, "package.json");
    if (existsSync(pj)) {
      const meta = JSON.parse(readFileSync(pj, "utf8"));
      if (meta.name) {
        const lic = typeof meta.license === "string" ? meta.license : meta.license?.type ?? (meta.licenses ?? []).map((l: { type: string }) => l.type).join(" OR ");
        const pkg = { name: meta.name, version: meta.version ?? "", license: lic || "UNKNOWN", dir };
        for (const d of seen) cache.set(d, pkg);
        return pkg;
      }
    }
    const up = dirname(dir);
    if (up === dir) {
      for (const d of seen) cache.set(d, null);
      return null;
    }
    dir = up;
  }
}

/** An SPDX expression is fine when every AND term has an OR alternative on the list. */
function permissive(expr: string): boolean {
  const e = expr.replace(/[()]/g, " ").trim();
  return e.split(/\s+AND\s+/).every((term) => term.split(/\s+OR\s+/).some((id) => PERMISSIVE.has(id.trim())));
}

function licenseFiles(dir: string): string[] {
  return readdirSync(dir).filter((f) => LICENSE_FILE.test(f)).sort();
}

export function thirdPartyLicenses(opts: { root: string; core: string }): Plugin {
  return {
    name: "third-party-licenses",
    apply: "build",
    generateBundle(_options, bundle) {
      const cache = new Map<string, Pkg | null>();
      const pkgs = new Map<string, Pkg>();
      let runtime = false;
      for (const out of Object.values(bundle)) {
        if (out.type !== "chunk") continue;
        for (const raw of out.moduleIds) {
          if (raw.startsWith("\0")) { runtime = true; continue; } // vite's/rolldown's own helpers
          const file = raw.split("?")[0]!;
          if (!existsSync(file)) continue;
          const pkg = packageOf(file, cache);
          if (pkg) pkgs.set(`${pkg.name}@${pkg.version}`, pkg);
        }
      }
      const own = JSON.parse(readFileSync(join(opts.root, "package.json"), "utf8")).name;
      const core = JSON.parse(readFileSync(join(opts.core, "package.json"), "utf8")).name;
      const third = [...pkgs.values()].filter((p) => p.name !== own && p.name !== core);
      if (runtime) {
        for (const name of ["vite", "rolldown"]) {
          const pj = join(opts.root, "node_modules", name, "package.json");
          if (existsSync(pj)) third.push(packageOf(pj, cache)!);
        }
      }
      third.sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));

      const bad = third.filter((p) => !permissive(p.license) && !reviewed[p.name]);
      if (bad.length) this.error(`licenses to review before shipping: ${bad.map((p) => `${p.name}@${p.version} (${p.license})`).join(", ")} — add each to \`reviewed\` in web/licenses.ts with the reason, or drop it`);
      const noFile = third.filter((p) => !licenseFiles(p.dir).length);
      const orphan = noFile.filter((p) => p.license !== "MIT");
      if (orphan.length) this.error(`no license file, and not MIT: ${orphan.map((p) => `${p.name} (${p.license})`).join(", ")} — add its license text to web/licenses.ts`);

      let coreRev = "unknown";
      try { coreRev = execFileSync("git", ["-C", opts.core, "rev-parse", "HEAD"], { encoding: "utf8" }).trim(); } catch {}

      const rule = "=".repeat(78);
      const parts: string[] = [
        "pawtrace front end — third-party notices",
        "",
        "This folder is the built front end of pawtrace (web/ in the pawtrace repo), compiled with",
        `densk-core ${coreRev}. pawtrace is Apache-2.0, Copyright 2026 nynxlabs LLC (LICENSE and`,
        "NOTICE at the root of the repo and in the wheel). The build also contains the code below,",
        "each under its own license.",
        "",
        `Packages: densk-core, ${third.length} npm packages, and pdf.js's run-time assets (pdfjs/).`,
        "",
        ...third.map((p) => `  ${p.name}@${p.version}  ${p.license}${reviewed[p.name] ? "  (reviewed: " + reviewed[p.name] + ")" : ""}`),
      ];
      const section = (title: string, dir: string, files: string[], note = "") => {
        parts.push("", rule, title, rule);
        if (note) parts.push(note);
        for (const f of files) parts.push("", `--- ${f}`, "", readFileSync(join(dir, f), "utf8").trimEnd());
      };
      section(`densk-core ${coreRev} (Apache-2.0)`, opts.core, licenseFiles(opts.core));
      for (const p of third) {
        const files = licenseFiles(p.dir);
        section(`${p.name}@${p.version} (${p.license})`, p.dir, files);
        if (!files.length) parts.push("The package ships no license file; the MIT License its package.json names:", "", mitFor(p));
      }
      const pdfjs = third.find((p) => p.name === "pdfjs-dist");
      if (pdfjs) section(`pdf.js run-time assets in pdfjs/ (from pdfjs-dist@${pdfjs.version})`, pdfjs.dir,
        PDFJS_ASSET_LICENSES.filter((f) => existsSync(join(pdfjs.dir, f))),
        "Character maps, standard fonts, image decoders (wasm) and a colour profile, each folder with its license.");
      this.emitFile({ type: "asset", fileName: "THIRD-PARTY-NOTICES.txt", source: parts.join("\n") + "\n" });
      this.emitFile({ type: "asset", fileName: "licenses.json", source: JSON.stringify({
        densk_core: coreRev,
        packages: third.map((p) => ({ name: p.name, version: p.version, license: p.license, license_files: licenseFiles(p.dir) })),
      }, null, 1) + "\n" });
    },
  };
}
