// Placeholder front end for `pawtrace serve`: the project overview from the JSON API (docs/api.md).
// In the desktop app the shell adds the session token to every request; in a browser it comes from ?t=.
"use strict";
(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const q = new URLSearchParams(location.search);
  if (q.get("t")) { sessionStorage.setItem("pawtrace.t", q.get("t")); q.delete("t"); history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "")); }
  const token = sessionStorage.getItem("pawtrace.t");
  const api = async (path) => {
    const r = await fetch(path, { headers: token ? { "X-Pawtrace-Token": token } : {} });
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error((body && body.error) || `${r.status}`);
    return body;
  };
  const MAP = { bg: "--bg", sidebar: "--chrome", borderStrong: "--line", border: "--border", text: "--text", textStrong: "--strong",
                textMuted: "--muted", success: "--ok", danger: "--bad", warning: "--warn" };
  let co = "0", stamp = null;

  async function theme() {
    try {
      const t = await api("/api/theme"); // densk-core's snapshot: {available, theme: {colors}, text, tabs}
      if (!t.available) return;
      const colors = (t.theme && t.theme.colors) || {};
      for (const [k, v] of Object.entries(MAP)) if (colors[k]) document.documentElement.style.setProperty(v, colors[k]);
      const cls = (navigator.userAgent.match(/\b(?:Pawtrace|Nynx)DeviceClass\/(desktop|laptop)(?![a-z0-9-])/) || [])[1] || "desktop";
      const tabs = t.tabs && (t.tabs[cls] || t.tabs.desktop);
      if (tabs && tabs.barHeight) document.documentElement.style.setProperty("--bar", tabs.barHeight);
      const text = t.text && (t.text[cls] || t.text.desktop);
      if (text && text.ui) document.documentElement.style.fontSize = text.ui + "px";
    } catch (_) { /* keep the defaults */ }
  }

  async function render() {
    const [home, notes] = await Promise.all([api("/api/home?co=" + encodeURIComponent(co)), api("/api/notes").catch(() => ({ notes: [] }))]);
    const forms = notes.notes.filter((n) => n.kind !== "note");
    $("#main").innerHTML = `
      <h2>Papers</h2>
      <table><thead><tr><th>Paper</th><th>Stages</th><th class="n">Items</th><th class="n">Gaps</th><th class="n">Questions</th><th>Gates</th><th>AI tools</th></tr></thead><tbody>
      ${home.papers.map((p) => `<tr><td><strong>${esc(p.id)}</strong><div class="muted">${esc(p.title)}${p.year ? " · " + esc(p.year) : ""}</div></td>
        <td>${p.stages.map((s) => `<span class="st ${esc(s.state)}" title="${esc(s.name)}: ${esc(s.state)} — ${esc(s.note)}"></span>`).join("")}</td>
        <td class="n">${p.items}</td><td class="n">${p.open_items}</td><td class="n">${p.open_questions}</td>
        <td>${p.panels.n ? `<span class="ok">${p.panels.pass}</span> / ${p.panels.n}${p.panels.fail ? ` <span class="bad">(${p.panels.fail} failing)</span>` : ""}` : '<span class="muted">no panels</span>'}</td>
        <td class="${p.rights && p.rights.ai_tools === "never" ? "bad" : ""}">${esc(p.rights ? p.rights.ai_tools : "—")}</td></tr>`).join("")}
      </tbody></table>
      ${forms.length ? `<h2>Intake and review forms</h2><table><tbody>${forms.map((n) => `<tr><td>${esc(n.title)}<div class="muted">${esc(n.rel)}</div></td>
        <td class="muted">${Object.entries(n.counts).map(([k, v]) => `${v} ${esc(k)}`).join(" · ")}</td></tr>`).join("")}</tbody></table>` : ""}`;
  }

  async function start() {
    await theme();
    let meta;
    try { meta = await api("/api/meta"); } catch (err) { $("#main").innerHTML = `<p class="err">Can't reach the pawtrace server: ${esc(err.message)}</p>`; return; }
    $("#project").textContent = meta.project;
    document.title = `${meta.project} · pawtrace`;
    const sel = $("#co");
    if (meta.checkouts.length > 1) {
      sel.innerHTML = meta.checkouts.map((c) => `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("");
      sel.hidden = false;
      sel.onchange = () => { co = sel.value; stamp = null; render().catch(show); };
    }
    await render().catch(show);
    setInterval(async () => {
      if (document.hidden) return;
      try { const s = await api("/api/stamp?co=" + co); if (stamp !== null && s.stamp !== stamp) await render(); stamp = s.stamp; $("#status").textContent = ""; }
      catch (err) { $("#status").textContent = "server unreachable"; }
    }, 4000);
  }
  const show = (err) => { $("#main").innerHTML = `<p class="err">${esc(err.message)}</p>`; };
  start();
})();
