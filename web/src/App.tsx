// pawtrace on densk-core: the workbench (tabs, panes, sidebar, viewers and editors) over pawtrace
// serve's files, plus pawtrace's own pages as tabs with `pawtrace:` paths:
//
//   pawtrace:overview                 every paper: stages, items, gaps, questions, gates, rights
//   pawtrace:paper/<id>               one paper: stages, rights, spec, ledger, questions, panels
//   pawtrace:panel/<paper>/<panel>    one panel: gates, our overlay, the paper's figure
//   pawtrace:reviews                  intake and review forms with their marks (open one to mark it)
//   pawtrace:figures                  references and how each was made; figure images; digitize
//   pawtrace:digitize?<prefill>       the digitize form, prefilled (mode, panel, reference, image)
//   pawtrace:digitize/<session>       a digitize session
//   pawtrace:settings                 this machine's settings (the user config, ui.yaml)
//
// Everything else is a file in the tree and opens in the core's viewers and editors: review forms
// are markdown, so marking one is editing it (saved with the core's conflict check).

import {
  createHttpHost,
  setTabTitles,
  Workbench,
  type Host,
  type TabViewArgs,
  type TreeNode,
  type WorkbenchContext,
} from "densk-core";
import { useEffect, useRef } from "react";
import { currentCheckoutId, get, meta } from "./api";
import { PawtracePage, pageTitle } from "./pages";

export const SCHEME = "pawtrace:";

/** The HTTP host, with pawtrace's pages placed in the tree: the "pawtrace" folder holds pawtrace's
 * own pages (Settings) and docs (README, docs/, playbooks/, as the server lists them; the server's
 * settings.yaml stays out of the tree and opens from the Settings page), and the project's folder
 * starts with the project's pages (Overview, Reviews, Figures) with each paper's page first inside
 * that paper's folder. A paper whose folder isn't in the tree (another checkout, no tree: map) gets
 * its page at the end of the project's folder. */
export function pawtraceHost(): Host {
  const http = createHttpHost();
  type Paper = { id: string; folder: string | null };
  const papers = async (): Promise<Paper[]> => {
    try {
      const m = await meta();
      return (await get<{ papers: Paper[] }>(`/api/home?co=${encodeURIComponent(currentCheckoutId(m))}`)).papers;
    } catch {
      return []; // the server isn't reachable yet: the pages still open and say so
    }
  };
  const own: TreeNode = { name: "pawtrace", path: `${SCHEME}pages`, dir: [{ name: "Settings", path: `${SCHEME}settings` }] };
  const projectPages: TreeNode[] = [
    { name: "Overview", path: `${SCHEME}overview` },
    { name: "Reviews and intakes", path: `${SCHEME}reviews` },
    { name: "Figures and digitize", path: `${SCHEME}figures` },
  ];
  const paperPage = (p: Paper): TreeNode => ({ name: "Paper page", path: `${SCHEME}paper/${p.id}` });
  /** `node` with each paper's page put first in its folder; `placed` collects the papers placed. */
  const place = (node: TreeNode, byFolder: Map<string, Paper>, placed: Set<string>): TreeNode => {
    if (!node.dir) return node;
    const hit = byFolder.get(node.path);
    const dir = node.dir.map((n) => place(n, byFolder, placed));
    if (hit) placed.add(hit.id);
    return { ...node, dir: hit ? [paperPage(hit), ...dir] : dir };
  };
  const flat = (nodes: TreeNode[]): string[] => nodes.flatMap((n) => (n.dir ? flat(n.dir) : [n.path]));
  const pagePaths = async () => [
    ...flat([own]),
    ...flat(projectPages),
    ...(await papers()).map((p) => `${SCHEME}paper/${p.id}`),
  ];
  return {
    ...http,
    tree: async () => {
      const [ps, served] = await Promise.all([papers(), http.tree()]);
      const byFolder = new Map(ps.filter((p) => p.folder).map((p) => [p.folder!, p]));
      const placed = new Set<string>();
      const docs = (served.find((n) => n.path === "pawtrace")?.dir ?? []).filter((n) => n.path !== "pawtrace/settings.yaml");
      const mine: TreeNode = { ...own, dir: [...(own.dir ?? []), ...docs] };
      const rest = served.filter((n) => n.path !== "pawtrace").map((n) => place(n, byFolder, placed));
      const loose = ps.filter((p) => !placed.has(p.id)).map((p) => ({ name: p.id, path: `${SCHEME}paper/${p.id}` }));
      const [project, ...others] = rest;
      // pawtrace's own folder and the project's folder start open.
      if (!project) return [{ ...mine, open: true }, { name: "project", path: `${SCHEME}project`, open: true, dir: [...projectPages, ...loose] }];
      return [{ ...mine, open: true }, { ...project, open: true, dir: [...projectPages, ...(project.dir ?? []), ...loose] }, ...others];
    },
    find: async (query, signal) => {
      const q = query.toLowerCase().split(/\s+/).filter(Boolean);
      const mine = (await pagePaths()).filter((p) => q.every((w) => p.toLowerCase().includes(w) || pageTitle(p).toLowerCase().includes(w)));
      const files = (await http.find?.(query, signal)) ?? [];
      return [...mine, ...files];
    },
  };
}

setTabTitles((path) => (path.startsWith(SCHEME) ? pageTitle(path) : undefined));

/** pawtrace's pages; everything else falls through to the core's viewers. */
function usePawtraceTab({ path, onOpen }: TabViewArgs) {
  if (!path || !path.startsWith(SCHEME)) return undefined;
  return <PawtracePage path={path} onOpen={onOpen} />;
}

/** First run: open the overview. Keys from an embedded digitize page reach the tab keys. */
function usePawtrace(context: WorkbenchContext) {
  const opened = useRef(false);
  const ctx = useRef(context);
  ctx.current = context;
  useEffect(() => {
    if (opened.current || !context.tree) return;
    opened.current = true;
    if (context.everyTab.every((t) => !t.path)) context.open(`${SCHEME}overview`);
  }, [context.tree, context.everyTab, context]);
  useEffect(() => {
    // digitize.html (same origin, in a frame) forwards Ctrl+K/Q/R/digits/Tab/[ ]/PageUp/Down and Ctrl's release
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== location.origin || !event.data || event.data.type !== "pawtrace-key") return;
      const d = event.data as { kind: string; key: string; code: string; ctrl: boolean; shift: boolean };
      if (d.kind !== "keydown" && d.kind !== "keyup") return;
      window.dispatchEvent(new KeyboardEvent(d.kind, { key: d.key, code: d.code, ctrlKey: d.ctrl, shiftKey: d.shift, bubbles: true, cancelable: true }));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
}

export function App() {
  return (
    <Workbench
      labels={{ untitled: "pawtrace", pick: "Pick a page or a file from the sidebar.", loading: "Loading the project…" }}
      useTabView={usePawtraceTab}
      useExtension={usePawtrace}
    />
  );
}
