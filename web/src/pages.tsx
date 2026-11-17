// pawtrace's pages, each a tab in the workbench (paths in App.tsx). They read pawtrace serve's JSON
// views (docs/api.md) and open files in the core's viewers through `onOpen(treePath, true)`.

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, post, setCheckout, useApi, useCheckout, useMeta, type Meta } from "./api";
import { newTabButtonMode, setNewTabButtonMode, setSidebarButtonMode, sidebarButtonMode } from "densk-core";

type OpenFile = (path: string, inNewTab?: boolean) => void;
type FileRef = { space: string; path: string; tree: string | null } | null;
type Stage = { name: string; state: string; note: string };
type Rights = { license: string; access: string; ai_tools: string; redistribute: boolean; open_version: string | null };

const SCHEME = "pawtrace:";

export function pageTitle(path: string): string {
  const [route, query] = path.slice(SCHEME.length).split("?");
  const [kind, ...rest] = (route ?? "").split("/");
  if (kind === "overview") return "Overview";
  if (kind === "paper") return rest[0] ?? "Paper";
  if (kind === "panel") return rest.slice(1).join("/") || "Panel";
  if (kind === "reviews") return "Reviews";
  if (kind === "figures") return "Figures";
  if (kind === "digitize") return rest.length ? "Digitize" : query ? "Digitize (new)" : "Digitize";
  if (kind === "settings") return "Settings";
  if (kind === "pages") return "pawtrace";
  return "pawtrace";
}

export function PawtracePage({ path, onOpen }: { path: string; onOpen: OpenFile }) {
  const [route, query] = path.slice(SCHEME.length).split("?");
  const [kind, ...rest] = (route ?? "").split("/");
  const params = useMemo(() => new URLSearchParams(query ?? ""), [query]);
  let page: ReactNode;
  if (kind === "overview" || kind === "pages") page = <Overview onOpen={onOpen} />;
  else if (kind === "paper" && rest[0]) page = <Paper id={rest[0]} onOpen={onOpen} />;
  else if (kind === "panel" && rest.length >= 2) page = <PanelPage paper={rest[0]!} panel={rest.slice(1).join("/")} onOpen={onOpen} />;
  else if (kind === "settings") page = <Settings onOpen={onOpen} />;
  else if (kind === "reviews") page = <Reviews onOpen={onOpen} />;
  else if (kind === "figures") page = <Figures onOpen={onOpen} prefill={null} />;
  else if (kind === "digitize" && rest[0]) return <DigitizeSession id={rest[0]} />;
  else if (kind === "digitize") page = <Figures onOpen={onOpen} prefill={params} />;
  else page = <p className="pt-muted">No pawtrace page at {path}.</p>;
  return <div className="pt-page">{page}</div>;
}

// -- shared bits ---------------------------------------------------------------------------------

function useProject(): { m: Meta | null; co: string } {
  const m = useMeta();
  return { m, co: useCheckout(m).id };
}

function CheckoutPicker({ m }: { m: Meta | null }) {
  const { label } = useCheckout(m);
  if (!m || m.checkouts.length < 2) return null;
  return (
    <select className="pt-select" value={label} onChange={(e) => setCheckout(e.target.value)} title="Which checkout to read">
      {m.checkouts.map((c) => (
        <option key={c.id} value={c.label}>
          {c.label}
        </option>
      ))}
    </select>
  );
}

function Header({ title, sub, m, children }: { title: ReactNode; sub?: ReactNode; m: Meta | null; children?: ReactNode }) {
  return (
    <header className="pt-header">
      <div className="pt-row">
        <h1>{title}</h1>
        <span className="pt-grow" />
        {children}
        <CheckoutPicker m={m} />
      </div>
      {sub && <div className="pt-sub">{sub}</div>}
    </header>
  );
}

function Problem({ error }: { error: string | null }) {
  return error ? <p className="pt-error">{error}</p> : null;
}

function Stages({ stages }: { stages: Stage[] }) {
  return (
    <span className="pt-stages">
      {stages.map((s) => (
        <span key={s.name} className={`pt-stage pt-stage-${s.state}`} title={`${s.name}: ${s.state} — ${s.note}`} />
      ))}
    </span>
  );
}

const AI_CLASS: Record<string, string> = { never: "pt-bad", "local-only": "pt-warn", allowed: "pt-ok" };
function RightsChips({ r }: { r: Rights | null }) {
  if (!r) return <span className="pt-muted">no rights block</span>;
  return (
    <span className="pt-chips">
      <span className={`pt-chip ${AI_CLASS[r.ai_tools] ?? ""}`} title="rights.rules.ai_tools">
        AI tools: {r.ai_tools}
      </span>
      <span className={`pt-chip ${r.redistribute ? "pt-ok" : "pt-mute"}`}>{r.redistribute ? "may share" : "don't share"}</span>
      <span className="pt-muted">{r.license}</span>
    </span>
  );
}

const fmt = (v: unknown) => (v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));

function FileLink({ file, onOpen, children }: { file: FileRef | undefined; onOpen: OpenFile; children: ReactNode }) {
  if (!file?.tree) return <span>{children}</span>;
  return (
    <a href="#" className="pt-link" onClick={(e) => (e.preventDefault(), onOpen(file.tree!, true))} title={file.tree}>
      {children}
    </a>
  );
}

function PageLink({ to, onOpen, children }: { to: string; onOpen: OpenFile; children: ReactNode }) {
  return (
    <a href="#" className="pt-link" onClick={(e) => (e.preventDefault(), onOpen(SCHEME + to, true))}>
      {children}
    </a>
  );
}

// -- overview ------------------------------------------------------------------------------------

type HomePaper = {
  id: string;
  title: string;
  year: unknown;
  stages: Stage[];
  items: number;
  candidates: number;
  open_items: number;
  open_questions: number;
  panels: { n: number; pass: number; fail: number; unchecked: number };
  rights: Rights | null;
};
type Home = { project: string; profile: string; modules_on: string[]; papers: HomePaper[] };
type NoteRow = { root: number; rel: string; title: string; kind: string; counts: Record<string, number>; total: number; mtime: number; file: FileRef };

function Overview({ onOpen }: { onOpen: OpenFile }) {
  const { m, co } = useProject();
  const home = useApi<Home>(m ? `/api/home?co=${co}` : null);
  const notes = useApi<{ notes: NoteRow[] }>("/api/notes");
  const h = home.data;
  const sum = (f: (p: HomePaper) => number) => (h ? h.papers.reduce((a, p) => a + f(p), 0) : 0);
  const panels = sum((p) => p.panels.n);
  const forms = (notes.data?.notes ?? []).filter((n) => n.kind !== "note");
  return (
    <>
      <Header title={m?.project ?? "pawtrace"} m={m} sub={h && `profile ${h.profile} · modules: ${h.modules_on.join(", ") || "none"}`} />
      <Problem error={home.error} />
      {h && (
        <>
          <div className="pt-tiles">
            <Tile v={h.papers.length} k="papers" />
            <Tile v={sum((p) => p.items)} k="spec items" />
            <Tile v={sum((p) => p.open_items)} k="gaps without a fill" />
            <Tile v={sum((p) => p.candidates)} k="candidates to review" />
            <Tile v={sum((p) => p.open_questions)} k="open questions" />
            <Tile v={panels ? `${sum((p) => p.panels.pass)}/${panels}` : "—"} k="panels passing" />
          </div>
          <h2>Papers</h2>
          <div className="pt-scroll">
            <table className="pt-table">
              <thead>
                <tr>
                  <th>Paper</th>
                  <th title="intake · foundations · gaps · build · run · check · audit">Stages</th>
                  <th className="pt-num">Items</th>
                  <th className="pt-num">Gaps</th>
                  <th className="pt-num">Questions</th>
                  <th>Gates</th>
                  <th>Rights</th>
                </tr>
              </thead>
              <tbody>
                {h.papers.map((p) => (
                  <tr key={p.id} className="pt-click" onClick={() => onOpen(`${SCHEME}paper/${p.id}`, true)}>
                    <td>
                      <strong>{p.id}</strong>
                      <div className="pt-muted">
                        {p.title}
                        {p.year ? ` · ${fmt(p.year)}` : ""}
                      </div>
                    </td>
                    <td>
                      <Stages stages={p.stages} />
                    </td>
                    <td className="pt-num">{p.items}</td>
                    <td className="pt-num">{p.open_items}</td>
                    <td className="pt-num">{p.open_questions}</td>
                    <td>
                      <GateBar n={p.panels.n} pass={p.panels.pass} fail={p.panels.fail} />
                    </td>
                    <td>
                      <RightsChips r={p.rights} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="pt-muted pt-small">Stages: intake · foundations · gaps · build · run · check · audit. Filled = done, half = under way, outline = not started, dashed = module off.</p>
        </>
      )}
      {forms.length > 0 && (
        <>
          <h2>Forms to mark</h2>
          <FormsTable rows={forms} onOpen={onOpen} />
        </>
      )}
    </>
  );
}

function Tile({ v, k }: { v: ReactNode; k: string }) {
  return (
    <div className="pt-tile">
      <div className="pt-tile-v">{v}</div>
      <div className="pt-tile-k">{k}</div>
    </div>
  );
}

function GateBar({ n, pass, fail }: { n: number; pass: number; fail: number }) {
  if (!n) return <span className="pt-muted">no panels</span>;
  return (
    <span title={`${pass} pass · ${fail} fail · ${n - pass - fail} not checked`}>
      <span className="pt-bar">
        <span style={{ width: `${(100 * pass) / n}%`, background: "var(--success)" }} />
        <span style={{ width: `${(100 * fail) / n}%`, background: "var(--danger)" }} />
      </span>{" "}
      <span className="pt-muted">
        {pass}/{n}
      </span>
    </span>
  );
}

// -- reviews -------------------------------------------------------------------------------------

const CHOICE_COLOR: Record<string, string> = {
  agree: "var(--success)",
  answered: "var(--success)",
  "nothing to add": "var(--success)",
  disagree: "var(--danger)",
  several: "var(--danger)",
  unsure: "var(--warning)",
  "still open": "var(--warning)",
};
const unset = (k: string) => k === "unmarked" || k === "unanswered";

function CountBar({ counts, total }: { counts: Record<string, number>; total: number }) {
  if (!total) return null;
  return (
    <span className="pt-bar pt-bar-wide" title={Object.entries(counts).map(([k, n]) => `${k} ${n}`).join(" · ")}>
      {Object.entries(counts)
        .filter(([k]) => !unset(k))
        .map(([k, n]) => (
          <span key={k} style={{ width: `${(100 * n) / total}%`, background: CHOICE_COLOR[k] ?? "var(--link)" }} />
        ))}
    </span>
  );
}

function countText(c: Record<string, number>) {
  return Object.entries(c)
    .sort((a, b) => Number(unset(a[0])) - Number(unset(b[0])))
    .map(([k, n]) => `${n} ${k}`)
    .join(" · ");
}

function FormsTable({ rows, onOpen }: { rows: NoteRow[]; onOpen: OpenFile }) {
  return (
    <div className="pt-scroll">
      <table className="pt-table">
        <tbody>
          {rows.map((n) => (
            <tr key={`${n.root}/${n.rel}`} className="pt-click" onClick={() => n.file?.tree && onOpen(n.file.tree, true)}>
              <td>
                {n.title}
                <div className="pt-muted pt-mono">{n.rel}</div>
              </td>
              <td className="pt-muted">{countText(n.counts)}</td>
              <td>
                <CountBar counts={n.counts} total={n.total} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reviews({ onOpen }: { onOpen: OpenFile }) {
  const m = useMeta();
  const notes = useApi<{ notes: NoteRow[] }>("/api/notes");
  const rows = notes.data?.notes ?? [];
  const group = (k: string) => rows.filter((n) => n.kind === k);
  const writable = m?.spaces.some((s) => s.kind === "notes" && s.writable);
  return (
    <>
      <Header
        title="Reviews and intakes"
        m={null}
        sub={
          writable
            ? "Open a form to mark it: tick a box ([x]) and write the note after note:. It saves as you type, and if the file changes elsewhere you'll be asked which version to keep. These counts follow along."
            : "These notes folders are read-only on this server."
        }
      />
      <Problem error={notes.error} />
      {!rows.length && !notes.error && <p className="pt-muted">No notes folder. Start pawtrace serve with --notes DIR to show forms kept beside the project.</p>}
      {group("review").length > 0 && (
        <>
          <h2>Review forms</h2>
          <FormsTable rows={group("review")} onOpen={onOpen} />
        </>
      )}
      {group("interview").length > 0 && (
        <>
          <h2>Interviews</h2>
          <FormsTable rows={group("interview")} onOpen={onOpen} />
        </>
      )}
      {group("note").length > 0 && (
        <>
          <h2>Other notes</h2>
          <FormsTable rows={group("note")} onOpen={onOpen} />
        </>
      )}
    </>
  );
}

// -- paper ---------------------------------------------------------------------------------------

type Item = { id: string; kind?: string; status: string; section?: string; where?: string; text?: string; plain?: string; value?: unknown; units?: string; unit?: string; entries: string[] };
type Entry = { id: string; type?: string; item?: string; source?: string; status?: string; value?: unknown; reason?: unknown; why?: unknown };
type Question = { id: string; status: string; to?: string; question?: string; why?: string; items?: string[] | string; answer?: unknown; answer_source?: string };
type Gate = { id: string; pass: boolean; required: boolean; check?: unknown; why?: string; error?: string };
type PanelView = {
  id: string;
  title: string;
  runs: number;
  latest: { id?: string; source?: string } | null;
  report: { pass: boolean; n_passed: number; n_required: number; gates: Gate[] } | null;
  overlay: string | null;
  overlay_name: string | null;
  overlay_file: FileRef;
  paper_figure: string | null;
  paper_figure_note: string;
  references: string[];
};
type PaperView = {
  id: string;
  title: string;
  year: unknown;
  authors: unknown;
  stages: Stage[];
  rights: Rights | null;
  rights_lines: string[];
  ai_never: boolean;
  items: Item[];
  statuses: Record<string, number>;
  entries: Entry[];
  questions: Question[];
  panels: PanelView[];
  lint: { level: string; message?: string; msg?: string }[];
};

const STATUS_CLASS: Record<string, string> = { stated: "pt-ok", ambiguous: "pt-warn", contradicted: "pt-bad", missing: "pt-mute", candidate: "pt-info" };
const ENTRY_CLASS: Record<string, string> = { adopted: "pt-ok", testing: "pt-warn", proposed: "pt-info", rejected: "pt-bad", superseded: "pt-mute" };
const Q_CLASS: Record<string, string> = { open: "pt-warn", asked: "pt-info", "answered-by-us": "pt-ok", answered: "pt-ok" };

function Paper({ id, onOpen }: { id: string; onOpen: OpenFile }) {
  const { m, co } = useProject();
  const res = useApi<PaperView>(m ? `/api/paper?co=${co}&id=${encodeURIComponent(id)}` : null);
  const [status, setStatus] = useState("all");
  const [find, setFind] = useState("");
  const p = res.data;
  if (!p) return <>{<Header title={id} m={m} />}<Problem error={res.error} /></>;
  const words = find.toLowerCase().split(/\s+/).filter(Boolean);
  const items = p.items.filter((i) => (status === "all" || i.status === status) && words.every((w) => JSON.stringify(i).toLowerCase().includes(w)));
  const authors = Array.isArray(p.authors) ? p.authors.join(", ") : fmt(p.authors);
  return (
    <>
      <Header title={p.id} m={m} sub={[p.title, fmt(p.year), authors].filter(Boolean).join(" · ")}>
        <RightsChips r={p.rights} />
      </Header>
      <Problem error={res.error} />
      <h2>Stages</h2>
      <div className="pt-stage-list">
        {p.stages.map((s) => (
          <div key={s.name} className="pt-row">
            <span className={`pt-stage pt-stage-${s.state}`} />
            <strong className="pt-stage-name">{s.name}</strong>
            <span className="pt-muted">
              {s.state} — {s.note}
            </span>
          </div>
        ))}
      </div>
      {p.rights && (
        <>
          <h2>Rights</h2>
          <div className={`pt-card ${p.ai_never ? "pt-card-bad" : ""}`}>
            <div className="pt-kv">
              <span>license</span>
              <span>{p.rights.license}</span>
              <span>access</span>
              <span>{p.rights.access}</span>
              <span>AI tools</span>
              <span>
                {p.rights.ai_tools}
                {p.ai_never ? " — keep agents away from this paper's files (pawtrace makes no model calls)" : ""}
              </span>
              <span>share copies</span>
              <span>{p.rights.redistribute ? "yes" : "no"}</span>
              {p.rights.open_version && (
                <>
                  <span>open version</span>
                  <span>{p.rights.open_version}</span>
                </>
              )}
            </div>
          </div>
        </>
      )}
      <h2>Spec items</h2>
      <div className="pt-row pt-gap">
        {["all", "stated", "ambiguous", "contradicted", "missing", "candidate"].map((s) => (
          <button key={s} className={`pt-chip pt-chip-button ${s === "all" ? "" : STATUS_CLASS[s]} ${status === s ? "pt-on" : ""}`} onClick={() => setStatus(s)}>
            {s} {s === "all" ? p.items.length : p.statuses[s] ?? 0}
          </button>
        ))}
        <span className="pt-grow" />
        <input className="pt-input" type="search" placeholder="Filter items…" value={find} onChange={(e) => setFind(e.target.value)} />
      </div>
      <div className="pt-scroll">
        <table className="pt-table pt-items">
          <thead>
            <tr>
              <th>Item</th>
              <th>Kind</th>
              <th>Status</th>
              <th>Where</th>
              <th>What the spec says</th>
              <th>Value</th>
              <th>Ledger</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id}>
                <td className="pt-mono">{i.id}</td>
                <td className="pt-muted">{i.kind}</td>
                <td>
                  <span className={`pt-chip ${STATUS_CLASS[i.status] ?? ""}`}>{i.status}</span>
                </td>
                <td className="pt-muted">{i.section ?? i.where}</td>
                <td className="pt-wrap">{i.plain ?? i.text}</td>
                <td className="pt-mono pt-wrap">
                  {fmt(i.value)}
                  {i.units || i.unit ? ` ${i.units ?? i.unit}` : ""}
                </td>
                <td className="pt-mono">{i.entries.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!items.length && <p className="pt-muted">No items match.</p>}
      <h2>Ledger</h2>
      {p.entries.length ? (
        <div className="pt-scroll">
          <table className="pt-table">
            <thead>
              <tr>
                <th>Entry</th>
                <th>Type</th>
                <th>Item</th>
                <th>Source</th>
                <th>Status</th>
                <th>Value</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {p.entries.map((e) => (
                <tr key={e.id}>
                  <td className="pt-mono">{e.id}</td>
                  <td className="pt-muted">{e.type}</td>
                  <td className="pt-mono">{e.item}</td>
                  <td className="pt-muted">{e.source}</td>
                  <td>
                    <span className={`pt-chip ${ENTRY_CLASS[e.status ?? ""] ?? ""}`}>{e.status}</span>
                  </td>
                  <td className="pt-mono pt-wrap">{fmt(e.value)}</td>
                  <td className="pt-wrap">{fmt(e.reason ?? e.why)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="pt-muted">No ledger entries.</p>
      )}
      <h2>Author questions</h2>
      {p.questions.length ? (
        p.questions.map((q) => (
          <div key={q.id} className="pt-card">
            <div className="pt-row pt-gap">
              <span className="pt-mono">{q.id}</span>
              <span className={`pt-chip ${Q_CLASS[q.status] ?? ""}`}>{q.status}</span>
              {q.to && <span className="pt-muted">to {q.to}</span>}
            </div>
            <div className="pt-wrap">{q.question}</div>
            {q.why && <div className="pt-muted pt-wrap">why: {q.why}</div>}
            {q.answer != null && (
              <div className="pt-answer pt-wrap">
                {fmt(q.answer)}
                {q.answer_source && <span className="pt-muted"> — {q.answer_source}</span>}
              </div>
            )}
          </div>
        ))
      ) : (
        <p className="pt-muted">No questions in the ledger.</p>
      )}
      <h2>Panels</h2>
      {p.panels.length ? p.panels.map((x) => <PanelCard key={x.id} p={p} x={x} onOpen={onOpen} co={co} reload={res.reload} />) : <p className="pt-muted">No panels yet.</p>}
      {p.lint.length > 0 && (
        <>
          <h2>Lint</h2>
          <div className="pt-scroll">
            <table className="pt-table">
              <tbody>
                {p.lint.map((l, i) => (
                  <tr key={i}>
                    <td>
                      <span className={`pt-chip ${l.level === "error" ? "pt-bad" : "pt-warn"}`}>{l.level}</span>
                    </td>
                    <td className="pt-wrap">{l.message ?? l.msg}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function PanelCard({ p, x, onOpen, co, reload }: { p: PaperView; x: PanelView; onOpen: OpenFile; co: string; reload: () => void }) {
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const rep = x.report;
  const draw = async () => {
    setBusy("Drawing…");
    try {
      await post("/api/overlay", { co, panel: x.id });
      setBusy(null);
      reload();
    } catch (err) {
      setBusy((err as Error).message);
    }
  };
  const short = x.id.split("/").slice(1).join("/");
  return (
    <div className="pt-card">
      <div className="pt-row pt-gap">
        <PageLink to={`panel/${p.id}/${short}`} onOpen={onOpen}>
          <strong>{x.title}</strong>
        </PageLink>
        <span className="pt-mono pt-muted">{x.id}</span>
        {rep ? (
          <span className={`pt-chip ${rep.pass ? "pt-ok" : "pt-bad"}`}>
            {rep.pass ? "pass" : "fail"} {rep.n_passed}/{rep.n_required}
          </span>
        ) : (
          <span className="pt-chip pt-mute">not checked</span>
        )}
        <span className="pt-grow" />
        <span className="pt-muted">
          {x.runs} run{x.runs === 1 ? "" : "s"}
          {x.latest?.id ? ` · latest ${x.latest.id}` : ""}
        </span>
        {x.runs > 0 && (
          <button className="pt-button" onClick={draw} disabled={busy === "Drawing…"}>
            Draw overlay
          </button>
        )}
      </div>
      {busy && busy !== "Drawing…" && <p className="pt-error">{busy}</p>}
      {rep && rep.gates.length > 0 && (
        <div className="pt-scroll">
          <table className="pt-table">
            <thead>
              <tr>
                <th>Gate</th>
                <th>Result</th>
                <th>Check</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {rep.gates.map((g) => (
                <tr key={g.id}>
                  <td className="pt-mono">
                    {g.id}
                    {g.required ? "" : " (optional)"}
                  </td>
                  <td>
                    <span className={`pt-chip ${g.pass ? "pt-ok" : "pt-bad"}`}>{g.pass ? "pass" : "fail"}</span>
                  </td>
                  <td className="pt-mono pt-wrap">{fmt(g.check)}</td>
                  <td className="pt-muted pt-wrap">{g.error ?? g.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="pt-two">
        <div>
          <h3>Ours (overlay on the references)</h3>
          {x.overlay ? (
            <>
              <img className="pt-figure" src={x.overlay} alt="overlay" onClick={() => x.overlay_file?.tree && onOpen(x.overlay_file.tree, true)} />
              <div className="pt-muted">{x.overlay_name}</div>
            </>
          ) : (
            <div className="pt-placeholder">No overlay yet{x.runs ? "" : " (no runs)"}</div>
          )}
        </div>
        <div>
          <h3>The paper's figure</h3>
          {!x.paper_figure ? (
            <div className="pt-placeholder">{x.paper_figure_note || "no paper figure"}</div>
          ) : p.ai_never && !reveal ? (
            <div className="pt-placeholder">
              <button className="pt-button" onClick={() => setReveal(true)}>
                Show the paper's figure
              </button>
              <div className="pt-muted">{x.paper_figure_note}</div>
            </div>
          ) : (
            <>
              <img className="pt-figure" src={x.paper_figure} alt="the paper's figure" />
              <div className="pt-muted">{x.paper_figure_note}</div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function PanelPage({ paper, panel, onOpen }: { paper: string; panel: string; onOpen: OpenFile }) {
  const { m, co } = useProject();
  const res = useApi<PaperView>(m ? `/api/paper?co=${co}&id=${encodeURIComponent(paper)}` : null);
  const x = res.data?.panels.find((q) => q.id === `${paper}/${panel}`);
  return (
    <>
      <Header title={x?.title ?? `${paper}/${panel}`} m={m} sub={<PageLink to={`paper/${paper}`} onOpen={onOpen}>{paper}</PageLink>} />
      <Problem error={res.error} />
      {res.data && !x && <p className="pt-muted">No panel {panel} in this checkout.</p>}
      {res.data && x && <PanelCard p={res.data} x={x} onOpen={onOpen} co={co} reload={res.reload} />}
    </>
  );
}

// -- figures and digitize ------------------------------------------------------------------------

type Provenance = {
  exists: boolean;
  method?: string;
  tool?: string;
  by?: string;
  date?: string;
  assist_used?: boolean;
  image?: string;
  reviewed_by?: string;
  review_date?: string;
  review_complete?: boolean;
  residual?: Record<string, Record<string, number>>;
  readme?: string;
};
type Ref = { panel: string; paper?: string; reference?: string; ai_never?: boolean; path?: string; format?: string; series?: string; digitized_from?: string; provenance?: Provenance; file: FileRef; error?: string };
type Fig = { root: number; rel: string; name: string; ext: string; size: number; url?: string; file: FileRef };
type FiguresView = { references: Ref[]; figures: Fig[]; readmes: { root: number; rel: string; title: string; file: FileRef }[]; sessions: { id: string; title: string; out: string; saves: number }[] };

function Provenance({ r }: { r: Ref }) {
  const p = r.provenance;
  if (r.error) return <span className="pt-error">{r.error}</span>;
  if (!p?.exists) return <span className="pt-error">file missing</span>;
  return (
    <div>
      {p.method ? (
        <div>
          {p.method} · {p.tool}
          {p.by ? ` · by ${p.by}` : ""}
          {p.date ? ` · ${String(p.date).slice(0, 10)}` : ""}
          {p.assist_used ? " · colour snap used" : ""}
        </div>
      ) : (
        <div className="pt-muted">no digitization record{r.digitized_from ? ` — ${r.digitized_from}` : ""}</div>
      )}
      {p.reviewed_by ? (
        <span className={`pt-chip ${p.review_complete === false ? "pt-warn" : "pt-ok"}`}>
          {p.review_complete === false ? "review in progress" : "reviewed"} by {p.reviewed_by}
          {p.review_date ? ` · ${String(p.review_date).slice(0, 10)}` : ""}
        </span>
      ) : (
        <span className="pt-chip pt-warn">not reviewed</span>
      )}
      {p.residual &&
        Object.entries(p.residual).map(([s, o]) => (
          <div key={s} className="pt-muted pt-mono pt-small">
            {s}: {Object.entries(o).map(([k, n]) => `${k} ${typeof n === "number" ? +n.toPrecision(3) : n}`).join(", ")}
          </div>
        ))}
    </div>
  );
}

function Figures({ onOpen, prefill }: { onOpen: OpenFile; prefill: URLSearchParams | null }) {
  const { m, co } = useProject();
  const res = useApi<FiguresView>(m ? `/api/figures?co=${co}` : null);
  const f = res.data;
  return (
    <>
      <Header
        title={prefill ? "Digitize" : "Figures"}
        m={m}
        sub="The references each panel checks against and how each was made, the figure images in the notes folders, and digitize. Nothing leaves this machine and no model is called."
      />
      <Problem error={res.error} />
      {f && (
        <>
          {f.sessions.length > 0 && (
            <>
              <h2>Open digitize sessions</h2>
              <div className="pt-scroll">
                <table className="pt-table">
                  <tbody>
                    {f.sessions.map((s) => (
                      <tr key={s.id} className="pt-click" onClick={() => onOpen(`${SCHEME}digitize/${s.id}`, true)}>
                        <td>{s.title}</td>
                        <td className="pt-muted pt-mono">{s.out}</td>
                        <td className="pt-muted">
                          {s.saves} save{s.saves === 1 ? "" : "s"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <DigitizeForm f={f} co={co} m={m} onOpen={onOpen} prefill={prefill} />
          {!prefill && (
            <>
              <h2>References</h2>
              {f.references.length ? (
                <div className="pt-scroll">
                  <table className="pt-table">
                    <thead>
                      <tr>
                        <th>Panel · reference</th>
                        <th>File</th>
                        <th>Provenance</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {f.references.map((r, i) => (
                        <tr key={i}>
                          <td>
                            <div className="pt-mono">{r.panel}</div>
                            <div>
                              {r.reference} {r.ai_never && <span className="pt-chip pt-bad">AI tools: never</span>}
                            </div>
                          </td>
                          <td className="pt-mono pt-wrap">
                            <FileLink file={r.file} onOpen={onOpen}>
                              {r.path}
                            </FileLink>
                            <div className="pt-muted">
                              {r.format}
                              {r.series ? ` · ${r.series}` : ""}
                            </div>
                          </td>
                          <td>
                            <Provenance r={r} />
                          </td>
                          <td>
                            {!r.error && (
                              <button
                                className="pt-button"
                                onClick={() => {
                                  const img = r.provenance?.image ? f.figures.find((x) => x.name === r.provenance!.image) : undefined;
                                  const q = new URLSearchParams({ mode: "review", panel: r.panel, reference: r.reference ?? "" });
                                  if (img?.file?.tree) q.set("image", img.file.tree);
                                  onOpen(`${SCHEME}digitize?${q}`, true);
                                }}
                              >
                                Review…
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="pt-muted">No panel references in this checkout.</p>
              )}
              {f.readmes.length > 0 && (
                <p>
                  Notes:{" "}
                  {f.readmes.map((r) => (
                    <FileLink key={r.rel} file={r.file} onOpen={onOpen}>
                      {r.title}
                    </FileLink>
                  ))}
                </p>
              )}
              <h2>Figure images</h2>
              <div className="pt-thumbs">
                {f.figures.map((x) => (
                  <div key={`${x.root}/${x.rel}`} className="pt-thumb" onClick={() => x.file?.tree && onOpen(x.file.tree, true)} title={x.rel}>
                    <div className="pt-thumb-img">{x.url && x.ext !== ".pdf" ? <img loading="lazy" src={x.url} alt="" /> : <span className="pt-muted">{x.ext.slice(1).toUpperCase()}</span>}</div>
                    <div className="pt-thumb-cap pt-mono">{x.rel}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}

function DigitizeForm({ f, co, m, onOpen, prefill }: { f: FiguresView; co: string; m: Meta | null; onOpen: OpenFile; prefill: URLSearchParams | null }) {
  const panels = [...new Set(f.references.filter((r) => !r.error).map((r) => r.panel))];
  const initialImage = prefill?.get("image") ?? "";
  const [mode, setMode] = useState(prefill?.get("mode") === "review" ? "review" : "new");
  const [image, setImage] = useState(() => Math.max(0, f.figures.findIndex((x) => x.file?.tree === initialImage || x.rel === initialImage)));
  const [panel, setPanel] = useState(prefill?.get("panel") ?? "");
  const [reference, setReference] = useState(prefill?.get("reference") ?? "");
  const [out, setOut] = useState(prefill?.get("out") ?? "");
  const [page, setPage] = useState(prefill?.get("page") ?? "");
  const [crop, setCrop] = useState(prefill?.get("crop") ?? "");
  const [options, setOptions] = useState(prefill?.get("options") ?? "");
  const [by, setBy] = useState(prefill?.get("by") ?? m?.by ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  if (!f.figures.length) return <p className="pt-muted">No figure images in the notes folders to digitize.</p>;
  const refs = f.references.filter((r) => r.panel === panel && !r.error).map((r) => r.reference ?? "");
  const fig = f.figures[image];
  const start = async () => {
    if (!fig) return;
    if (mode === "review" && (!panel || !reference)) return setMsg("A review needs a panel and a reference.");
    setMsg(null);
    try {
      const r = await post<{ id: string; title: string; note: string | null }>("/api/digitize/start", {
        co,
        mode,
        image: { root: fig.root, rel: fig.rel },
        panel: panel || null,
        reference: reference || null,
        out: out.trim() || null,
        by: by.trim() || null,
        page: page || null,
        crop: crop.trim() ? crop.split(",").map(Number) : null,
        options: options.trim() || null,
      });
      onOpen(`${SCHEME}digitize/${r.id}`, true);
    } catch (err) {
      setMsg((err as Error).message);
    }
  };
  return (
    <>
      <h2>Digitize</h2>
      <div className="pt-card">
        <div className="pt-form">
          <label>Mode</label>
          <div className="pt-row pt-gap">
            <label>
              <input type="radio" checked={mode === "new"} onChange={() => setMode("new")} /> new digitization
            </label>
            <label>
              <input type="radio" checked={mode === "review"} onChange={() => setMode("review")} /> review a reference
            </label>
          </div>
          <label>Figure</label>
          <select className="pt-select" value={image} onChange={(e) => setImage(+e.target.value)}>
            {f.figures.map((x, i) => (
              <option key={i} value={i}>
                {x.rel}
              </option>
            ))}
          </select>
          <label>Panel</label>
          <select className="pt-select" value={panel} onChange={(e) => setPanel(e.target.value)}>
            <option value="">(none)</option>
            {panels.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
          <label>Reference</label>
          <select className="pt-select" value={reference} onChange={(e) => setReference(e.target.value)}>
            <option value="">(none)</option>
            {refs.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          {fig?.ext === ".pdf" && (
            <>
              <label>PDF page · crop</label>
              <div className="pt-row pt-gap">
                <input className="pt-input" type="number" min={1} placeholder="page" value={page} onChange={(e) => setPage(e.target.value)} />
                <input className="pt-input" placeholder="x0,y0,x1,y1 (points, from the top left)" value={crop} onChange={(e) => setCrop(e.target.value)} />
              </div>
            </>
          )}
          <label>Save to</label>
          <input className="pt-input" placeholder={mode === "review" ? "empty: rewrite the reference in place (curves-json)" : "a new curves-json file, in the checkout or a notes folder"} value={out} onChange={(e) => setOut(e.target.value)} />
          <label>Reader options</label>
          <input className="pt-input" placeholder='review only, JSON, e.g. {"err": {"a": "a_se"}}' value={options} onChange={(e) => setOptions(e.target.value)} />
          <label>By</label>
          <input className="pt-input" placeholder="your name (recorded in the file)" value={by} onChange={(e) => setBy(e.target.value)} />
          <span />
          <div className="pt-row pt-gap">
            <button className="pt-button pt-primary" onClick={start}>
              Open in a tab
            </button>
            {msg && <span className="pt-error">{msg}</span>}
          </div>
        </div>
      </div>
    </>
  );
}

function DigitizeSession({ id }: { id: string }) {
  return <iframe className="pt-frame" src={`/dz/${encodeURIComponent(id)}/`} title="digitize" />;
}

// -- settings ------------------------------------------------------------------------------------

type SettingsValues = {
  edit_cmd: string;
  theme: string;
  by: string;
  read_only: boolean;
  write_checkout: boolean;
  branches: boolean;
  frame_ancestors: string[];
  hide: string[];
  notes: string[];
};
type SettingsView = {
  values: SettingsValues;
  version: string;
  exists: boolean;
  comments: boolean;
  writable: boolean;
  project: string;
  file: string;
  overrides: string[];
  pending: string[];
  running: Record<string, unknown>;
};
const SETTING_LABEL: Record<string, string> = {
  edit_cmd: "Editor command",
  theme: "Theme file",
  by: "Your name",
  read_only: "Read-only",
  write_checkout: "Write in checkouts",
  branches: "Other checkouts",
  frame_ancestors: "Apps that may embed digitize",
  hide: "Hidden in the file tree",
  notes: "Notes folders",
};

function Settings({ onOpen }: { onOpen: OpenFile }) {
  const { data, error, reload } = useApi<SettingsView>("/api/settings");
  const [draft, setDraft] = useState<SettingsValues | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<SettingsView | null>(null);
  const view = saved && data && saved.version === data.version ? saved : data;
  const dirty = !!draft && !!view && JSON.stringify(draft) !== JSON.stringify(view.values);
  useEffect(() => {
    if (!dirty) setDraft(view ? view.values : null);
    // a change on disk refreshes the form unless there are unsaved edits
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.version]);
  const v = draft ?? view?.values;
  const set = <K extends keyof SettingsValues>(k: K, val: SettingsValues[K]) => v && setDraft({ ...v, [k]: val });
  const save = async () => {
    if (!view || !draft) return;
    setSaving(true);
    setProblem(null);
    try {
      const out = await post<SettingsView>("/api/settings", { values: draft, version: view.version });
      setSaved(out);
      setDraft(out.values);
    } catch (e) {
      setProblem(e instanceof ApiError && e.status === 409 ? "settings.yaml changed since this page read it. Reload to see it (your edits here are dropped)." : (e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const disabled = !view?.writable || saving;
  /** What serve runs with now (a command-line flag or the default), shown greyed in an empty field. */
  const running = (k: string) => {
    const r = view?.running[k];
    return typeof r === "string" ? r : "";
  };
  const note = (k: string) =>
    view?.overrides.includes(k) ? <div className="pt-muted pt-hint">This project sets its own value in settings.yaml, which wins here.</div> : null;
  return (
    <>
      <Header
        title="Settings"
        m={null}
        sub={
          <>
            pawtrace on this machine, kept in{" "}
            <a href="#" className="pt-link" onClick={(e) => (e.preventDefault(), onOpen("pawtrace/settings.yaml", true))}>
              settings.yaml
            </a>{" "}
            (open it to edit the YAML directly). Nothing here goes into a project.
          </>
        }
      >
        {dirty && (
          <button className="pt-button" disabled={saving} onClick={() => (setDraft(view?.values ?? null), setProblem(null))}>
            Discard
          </button>
        )}
        <button className="pt-button pt-primary" disabled={disabled || !dirty} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </Header>
      <Problem error={problem ?? error} />
      {view && !view.writable && <p className="pt-muted">pawtrace serve is read-only, so these can't be changed here.</p>}
      {view?.comments && dirty && <p className="pt-muted">Saving rewrites settings.yaml: its comments are dropped, its other keys are kept.</p>}
      {!!view?.pending.length && !dirty && (
        <div className="pt-card">
          Restart pawtrace serve to apply: {view.pending.map((k) => SETTING_LABEL[k] ?? k).join(", ")}.{" "}
          <span className="pt-muted">(A command-line flag keeps its value until it's dropped.)</span>
        </div>
      )}
      {v && (
        <fieldset className="pt-fieldset" disabled={disabled}>
          <h2>Editing</h2>
          <div className="pt-form">
            <label htmlFor="pt-set-edit">Editor command</label>
            <div>
              <input id="pt-set-edit" className="pt-input" placeholder={running("edit_cmd") || "myeditor {path}"} value={v.edit_cmd} onChange={(e) => set("edit_cmd", e.target.value)} />
              <div className="pt-muted pt-hint">Opens a file in your editor; {"{path}"} is the file's full path. Applies at once. Greyed: what's running now.</div>
              {note("edit_cmd")}
            </div>
            <label htmlFor="pt-set-by">Your name</label>
            <div>
              <input id="pt-set-by" className="pt-input" placeholder={running("by")} value={v.by} onChange={(e) => set("by", e.target.value)} />
              <div className="pt-muted pt-hint">Recorded on digitizations started here. Applies at once.</div>
              {note("by")}
            </div>
          </div>

          <h2>Appearance</h2>
          <div className="pt-form">
            <label htmlFor="pt-set-theme">Theme file</label>
            <div>
              <input id="pt-set-theme" className="pt-input" placeholder={running("theme") || "the default theme"} value={v.theme} onChange={(e) => set("theme", e.target.value)} />
              <div className="pt-muted pt-hint">A JSON theme file: colours, per-device text sizes. Empty: $PAWTRACE_THEME, then ~/.config/pawtrace/theme.json, then the built-in one.</div>
              {note("theme")}
            </div>
            <label htmlFor="pt-set-newtab">New tab button</label>
            <div>
              <ModeSelect id="pt-set-newtab" get={newTabButtonMode} set={setNewTabButtonMode} />
              <div className="pt-muted pt-hint">The + at the right of each tab bar. Automatic: only on touch tablets and phones (Ctrl+K elsewhere). This device only; applies at once.</div>
            </div>
            <label htmlFor="pt-set-sidebarbtn">Sidebar button</label>
            <div>
              <ModeSelect id="pt-set-sidebarbtn" get={sidebarButtonMode} set={setSidebarButtonMode} />
              <div className="pt-muted pt-hint">The show / hide sidebar button at the right of the title row (Ctrl+B does the same). Automatic: shown. This device only; applies at once.</div>
            </div>
          </div>

          <h2>Files</h2>
          <div className="pt-form">
            <Toggle label="Read-only" value={v.read_only} onChange={(x) => set("read_only", x)} hint="No file writes at all, these settings included." />
            <Toggle label="Write in checkouts" value={v.write_checkout} onChange={(x) => set("write_checkout", x)} hint="Let the app write inside checkouts, beyond the project's own folders." />
            <Toggle label="Other checkouts" value={v.branches} onChange={(x) => set("branches", x)} hint="With a tree: map in pawtrace.yaml, also show the project's other checkouts." />
            <label>Hidden in the file tree</label>
            <div>
              <ListEditor values={v.hide} placeholder="__init__.py" onChange={(x) => set("hide", x)} />
              <div className="pt-muted pt-hint">File or folder names the sidebar and find skip; * and ? work (e.g. *.pyc). Hidden files still open from links. Applies at once.</div>
              {note("hide")}
            </div>
          </div>

          <h2>{view?.project ?? "This project"}</h2>
          <div className="pt-form">
            <label>Notes folders</label>
            <div>
              <ListEditor values={v.notes} placeholder="~/notes/my-project" onChange={(x) => set("notes", x)} />
              {!v.notes.length && Array.isArray(view?.running.notes) && (view.running.notes as string[]).length > 0 && (
                <div className="pt-muted pt-hint">Running with {(view.running.notes as string[]).join(", ")} from the command line.</div>
              )}
              <div className="pt-muted pt-hint">Folders outside the repo shown beside the project: review forms, figures, notes.</div>
            </div>
          </div>

          <h2>Embedding</h2>
          <div className="pt-form">
            <label>Apps that may embed digitize</label>
            <div>
              <ListEditor values={v.frame_ancestors} placeholder="http://127.0.0.1:8795" onChange={(x) => set("frame_ancestors", x)} />
              <div className="pt-muted pt-hint">Origins allowed to show the digitize page inside them.</div>
              {note("frame_ancestors")}
            </div>
          </div>
        </fieldset>
      )}
      {!v && !error && <p className="pt-muted">Loading…</p>}
      {error && <button className="pt-button" onClick={reload}>Try again</button>}
    </>
  );
}

type ButtonMode = "auto" | "always" | "never";

/** A per-device button mode that densk-core keeps in localStorage (so it isn't in settings.yaml). */
function ModeSelect({ id, get, set }: { id: string; get: () => ButtonMode; set: (mode: ButtonMode) => void }) {
  const [mode, setMode] = useState<ButtonMode>(get);
  const change = (next: ButtonMode) => (set(next), setMode(next));
  return (
    <select id={id} className="pt-select" value={mode} onChange={(e) => change(e.target.value as ButtonMode)}>
      <option value="auto">Automatic</option>
      <option value="always">Always</option>
      <option value="never">Never</option>
    </select>
  );
}

function Toggle({ label, value, onChange, hint }: { label: string; value: boolean; onChange: (v: boolean) => void; hint: string }) {
  return (
    <>
      <label>{label}</label>
      <label className="pt-toggle">
        <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
        <span className="pt-muted">{hint}</span>
      </label>
    </>
  );
}

function ListEditor({ values, placeholder, onChange }: { values: string[]; placeholder: string; onChange: (v: string[]) => void }) {
  const [next, setNext] = useState("");
  const add = () => {
    const t = next.trim();
    if (t && !values.includes(t)) onChange([...values, t]);
    setNext("");
  };
  return (
    <div className="pt-list">
      {values.map((x, i) => (
        <div key={x} className="pt-row">
          <input className="pt-input pt-grow" value={x} onChange={(e) => onChange(values.map((y, j) => (j === i ? e.target.value : y)))} />
          <button className="pt-button" title="Remove" onClick={() => onChange(values.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <div className="pt-row">
        <input className="pt-input pt-grow" placeholder={placeholder} value={next} onChange={(e) => setNext(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="pt-button" disabled={!next.trim()} onClick={add}>
          Add
        </button>
      </div>
    </div>
  );
}
