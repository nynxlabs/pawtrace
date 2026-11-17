// pawtrace's JSON API (docs/api.md) for the panels. Same origin; the session token rides in the
// HttpOnly cookie a `?t=` page load sets, or in a header the desktop app adds, so no code here sees it.

import { host } from "densk-core";
import { useEffect, useState, useSyncExternalStore } from "react";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError((body && body.error) || (res.status === 403 ? "not signed in to this pawtrace server" : `HTTP ${res.status}`), res.status);
  return body as T;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError((out && out.error) || `HTTP ${res.status}`, res.status);
  return out as T;
}

// -- live refresh: any file change on the server bumps the revision; panels refetch ------------------

let revision = 0;
const revisionListeners = new Set<() => void>();
let subscribed = false;
function subscribeRevision(listener: () => void) {
  if (!subscribed) {
    subscribed = true;
    host().subscribe(() => {
      revision++;
      for (const l of revisionListeners) l();
    });
  }
  revisionListeners.add(listener);
  return () => revisionListeners.delete(listener);
}
export const useRevision = () => useSyncExternalStore(subscribeRevision, () => revision);

/** GET `path` (null = nothing yet); refetched when the files change. */
export function useApi<T>(path: string | null): { data: T | null; error: string | null; reload: () => void } {
  const rev = useRevision();
  const [state, setState] = useState<{ data: T | null; error: string | null }>({ data: null, error: null });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    get<T>(path).then(
      (data) => live && setState({ data, error: null }),
      (err: Error) => live && setState((s) => ({ data: s.data, error: err.message })),
    );
    return () => {
      live = false;
    };
  }, [path, rev, nonce]);
  return { ...state, reload: () => setNonce((n) => n + 1) };
}

// -- the checkout being shown (by branch label, so it survives a server restart) ---------------------

export type Checkout = { id: string; label: string; branch: string | null; path: string };
export type Meta = {
  version: string;
  project: string;
  checkouts: Checkout[];
  notes: { id: number; label: string; path: string }[];
  spaces: { id: string; kind: string; label: string; writable: boolean; tree: string }[];
  can_edit: boolean;
  by: string | null;
};

let metaPromise: Promise<Meta> | null = null;
export const meta = () => (metaPromise ??= get<Meta>("/api/meta"));

const CO_KEY = "pawtrace.checkout";
let checkoutLabel = localStorage.getItem(CO_KEY) ?? "";
const coListeners = new Set<() => void>();
export function setCheckout(label: string) {
  checkoutLabel = label;
  localStorage.setItem(CO_KEY, label);
  for (const l of coListeners) l();
}
const subscribeCo = (l: () => void) => {
  coListeners.add(l);
  return () => coListeners.delete(l);
};
/** The checkout id to ask the API for ("0" until the meta has loaded). */
export function useCheckout(m: Meta | null): { id: string; label: string } {
  const label = useSyncExternalStore(subscribeCo, () => checkoutLabel);
  const hit = m?.checkouts.find((c) => c.label === label) ?? m?.checkouts[0];
  return { id: hit?.id ?? "0", label: hit?.label ?? "" };
}
export function useMeta(): Meta | null {
  const [m, setM] = useState<Meta | null>(null);
  useEffect(() => {
    void meta().then(setM, () => setM(null));
  }, []);
  return m;
}
export function currentCheckoutId(m: Meta): string {
  return (m.checkouts.find((c) => c.label === checkoutLabel) ?? m.checkouts[0])?.id ?? "0";
}
