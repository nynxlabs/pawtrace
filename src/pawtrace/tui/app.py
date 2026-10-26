"""``pawtrace tui``: the people's view over the same files the CLI reads.

Read-only, except the add-paper walk-through, which creates ``papers/<id>/``
like ``pawtrace add-paper`` and fills in the rights block.
"""
from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from .. import rights as rts
from ..project import Project
from ..stages import STAGES
from . import brand, intake
from .data import PaperView, PanelView, extraction, load_paper
from .figure import RENDERER, Figure
from .widgets import BrandHeader, TitleBar, glyph, verdict_text

PRODUCT = "pawtrace"


def _stage_cells(pv: PaperView) -> list[Text]:
    return [Text(glyph(s.state), style="dim" if s.state in ("todo", "off") else "") for s in pv.stages]


class HomeScreen(Screen):
    BINDINGS = [
        Binding("enter", "open", "open"),
        Binding("a", "add_paper", "add paper"),
        Binding("r", "reload", "reload"),
        Binding("i", "about", "about"),
        Binding("t", "toggle_theme", "light/dark"),
        Binding("q", "app.quit", "quit"),
    ]

    DEFAULT_CSS = """
    HomeScreen { layout: vertical; }
    #project { height: 1; padding: 0 2; color: $text-muted; margin-top: 1; }
    #papers { height: auto; max-height: 50%; margin: 1 2 0 2; }
    #stages { height: 1fr; margin: 1 2; padding: 0 1; border-top: solid $border; }
    """

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.papers: list[PaperView] = []

    def compose(self) -> ComposeResult:
        yield BrandHeader(PRODUCT, "rebuild and check published computational research")
        yield Static(id="project")
        yield DataTable(id="papers", cursor_type="row", zebra_stripes=False)
        yield Static(id="stages")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#papers", DataTable)
        t.add_column("paper", key="paper")
        t.add_column("title", key="title", width=42)
        for n, s in enumerate(STAGES, 1):
            t.add_column(f"{n}", key=s)
        t.add_column("panels", key="panels")
        self.action_reload()

    def action_reload(self) -> None:
        p = self.project
        on = [k for k, v in p.modules().items() if v]
        self.query_one("#project", Static).update(
            Text.assemble((p.name, "bold"), f"  ·  profile {p.profile}  ·  {len(on)} modules on  ·  {p.root}")
        )
        self.papers = [load_paper(p, pid) for pid in p.papers()]
        t = self.query_one("#papers", DataTable)
        t.clear()
        for pv in self.papers:
            n_pass = sum(1 for x in pv.panels if x.verdict)
            title = pv.title if len(pv.title) <= 40 else pv.title[:39] + "…"
            t.add_row(Text(pv.id, style="bold"), title, *_stage_cells(pv), f"{n_pass}/{len(pv.panels)} pass", key=pv.id)
        if not self.papers:
            self.query_one("#stages", Static).update(Text("no papers yet — press a to add one", style="dim"))
        else:
            self._show_stages(self.papers[0])

    def _show_stages(self, pv: PaperView) -> None:
        out = Text()
        out.append(f"{pv.id}", style="bold")
        out.append(f"  {pv.title}  {pv.year}\n\n", style="dim")
        for n, s in enumerate(pv.stages, 1):
            out.append(f" {glyph(s.state)}  {n} {s.name:<12}", style="" if s.state in ("done", "active") else "dim")
            out.append(f"{s.note}\n", style="dim")
        out.append(f"\n {brand.PASS} done   {brand.PARTIAL} in progress   {brand.FAIL} not started   {brand.NONE} module off", style="dim")
        self.query_one("#stages", Static).update(out)

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        pv = next((x for x in self.papers if x.id == ev.row_key.value), None)
        if pv:
            self._show_stages(pv)

    def on_data_table_row_selected(self, ev: DataTable.RowSelected) -> None:
        self.app.push_screen(PaperScreen(self.project, str(ev.row_key.value)))

    def action_open(self) -> None:
        t = self.query_one("#papers", DataTable)
        if self.papers and t.cursor_row is not None:
            self.app.push_screen(PaperScreen(self.project, self.papers[t.cursor_row].id))

    def action_add_paper(self) -> None:
        self.app.push_screen(AddPaperScreen(self.project), lambda created: self.action_reload() if created else None)

    def action_about(self) -> None:
        self.app.push_screen(AboutScreen())

    def action_toggle_theme(self) -> None:
        self.app.theme = "nynxlabs-light" if self.app.theme == "nynxlabs" else "nynxlabs"


class PaperScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("f", "toggle_figures", "figures"),
        Binding("r", "reload", "reload"),
        Binding("q", "app.quit", "quit"),
    ]

    DEFAULT_CSS = """
    PaperScreen #body { height: 1fr; }
    PaperScreen #left { width: 1fr; min-width: 50; padding: 0 1; }
    PaperScreen #right { width: 1fr; padding: 0 1; border-left: solid $border; }
    PaperScreen #right.-hidden { display: none; }
    PaperScreen #panels { height: auto; max-height: 10; margin-bottom: 1; }
    PaperScreen TabbedContent { height: 1fr; }
    PaperScreen .tabtext { padding: 1 1; }
    PaperScreen #gates { height: 1fr; }
    """

    def __init__(self, project: Project, paper_id: str) -> None:
        super().__init__()
        self.project = project
        self.paper_id = paper_id
        self.pv: PaperView | None = None

    def compose(self) -> ComposeResult:
        yield TitleBar(PRODUCT, self.paper_id)
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield DataTable(id="panels", cursor_type="row")
                with TabbedContent(id="tabs"):
                    with TabPane("gates", id="tab-gates"):
                        yield DataTable(id="gates", cursor_type="row")
                    with TabPane("spec + ledger", id="tab-spec"):
                        yield VerticalScroll(Static(id="spec", classes="tabtext"))
                    with TabPane("rights", id="tab-rights"):
                        yield VerticalScroll(Static(id="rights", classes="tabtext"))
                    with TabPane("sources", id="tab-sources"):
                        yield VerticalScroll(Static(id="sources", classes="tabtext"))
            with Vertical(id="right"):
                yield Figure("ours", None, "", id="fig-ours")
                yield Figure("paper", None, "", id="fig-paper")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#panels", DataTable)
        t.add_column("panel", key="panel")
        t.add_column("title", key="title", width=30)
        t.add_column("verdict", key="verdict", width=12)
        t.add_column("runs", key="runs")
        g = self.query_one("#gates", DataTable)
        g.add_column("", key="mark")
        g.add_column("gate", key="gate")
        g.add_column("check", key="check", width=34)
        g.add_column("why", key="why")
        self.action_reload()

    def action_reload(self) -> None:
        self.pv = pv = load_paper(self.project, self.paper_id)
        t = self.query_one("#panels", DataTable)
        t.clear()
        for p in pv.panels:
            title = p.title if len(p.title) <= 30 else p.title[:29] + "…"
            t.add_row(Text(p.id.split("/", 1)[1], style="bold"), title, verdict_text(p.verdict, p.score), str(p.runs), key=p.id)
        self.query_one("#spec", Static).update(self._spec_text(pv))
        self.query_one("#rights", Static).update(self._rights_text(pv))
        self.query_one("#sources", Static).update(self._sources_text(pv))
        if pv.panels:
            self._show_panel(pv.panels[0])

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        if ev.data_table.id != "panels" or not self.pv:
            return
        p = next((x for x in self.pv.panels if x.id == ev.row_key.value), None)
        if p:
            self._show_panel(p)

    def _show_panel(self, p: PanelView) -> None:
        g = self.query_one("#gates", DataTable)
        g.clear()
        for gid, gate in ((p.report or {}).get("gates") or {}).items():
            ok = gate.get("pass")
            mark = Text(brand.PASS) if ok else Text("×", style="reverse bold")
            inputs = ", ".join(f"{k}={_fmt(v)}" for k, v in (gate.get("inputs") or {}).items())
            name = Text(gid, style="" if gate.get("required", True) else "dim")
            check = f"{gate.get('check')}({inputs})"
            check = check if len(check) <= 34 else check[:33] + "…"
            g.add_row(mark, name, Text(check, style="dim"), Text(str(gate.get("why") or ""), style="dim"))
        ours_note = (
            f"{p.ours_png.name} · run {p.latest['id']}" if p.ours_png and p.latest else "no overlay yet: `pawtrace overlay " + p.id + "`"
        )
        self.query_one("#fig-ours", Figure).show(p.ours_png, f"ours  ·  {p.id}", ours_note)
        self.query_one("#fig-paper", Figure).show(p.paper_png, "paper", p.paper_png_note)

    @staticmethod
    def _spec_text(pv: PaperView) -> Text:
        s = pv.summary
        out = Text()
        if not s:
            return Text("no spec.yaml", style="dim")
        by = s["items_by_status"]
        out.append(f"{s['items']} items  ", style="bold")
        out.append("  ".join(f"{v} {k}" for k, v in by.items()) + (f"  {s['candidates']} candidates" if s["candidates"] else "") + "\n\n", style="dim")
        out.append("open gaps  ", style="bold")
        out.append((", ".join(s["open_items"]) or "none") + "\n")
        out.append("deviations adopted  ", style="bold")
        out.append((", ".join(s["deviations_adopted"]) or "none") + "\n")
        out.append("open author questions  ", style="bold")
        out.append((", ".join(s["open_questions"]) or "none") + "\n\n")
        out.append("lint\n", style="bold")
        if not pv.lint:
            out.append("  clean\n", style="dim")
        for m in pv.lint:
            out.append(f"  {m['level']:<5} ", style="reverse" if m["level"] == "error" else "")
            out.append(f" {m['message']}\n", style="dim")
        return out

    @staticmethod
    def _rights_text(pv: PaperView) -> Text:
        r = rts.rights_of(pv.spec)
        if not r:
            return Text("no rights block: intake isn't done (lint error)", style="reverse")
        rules = rts.rules_of(pv.spec)
        out = Text()
        rows = [
            ("license", r.get("license")),
            ("license url", r.get("license_url")),
            ("copy we work from", r.get("copy_url")),
            ("open version", r.get("open_version")),
            ("access", r.get("access")),
            ("access terms", r.get("access_terms")),
            ("redistribute", rules.get("redistribute")),
            ("quotes in spec", rules.get("quotes_in_spec")),
            ("AI tools", rts.ai_tools(pv.spec)),
        ]
        for k, v in rows:
            out.append(f"{k:<20}", style="dim")
            out.append(f"{v if v not in (None, '') else '—'}\n")
        perms = r.get("permissions") or []
        out.append(f"\n{'permissions':<20}", style="dim")
        out.append(f"{len(perms) or 'none'}\n")
        for p in perms:
            if isinstance(p, dict):
                out.append(f"  {p.get('from')} · {p.get('date')} · {', '.join(map(str, p.get('grants') or []))}\n", style="dim")
        share = rts.may_redistribute(pv.spec)
        out.append("\nwhat that means  ", style="bold")
        out.append(
            ("the paper's PDF, text and figure crops may be committed and published" if share
             else "the PDF, text and figure crops stay on this machine (source/ is gitignored); digitizations are our own measurements")
            + "\n",
            style="dim",
        )
        return out

    @staticmethod
    def _sources_text(pv: PaperView) -> Text:
        out = Text()
        pol = pv.spec.get("policy") or {}
        ac = pol.get("authors_code") or {}
        out.append("code-use policy\n", style="bold")
        if pol.get("when_silent"):
            out.append(f"  when the paper is silent: {' → '.join(pol['when_silent'])}\n", style="dim")
        if ac:
            out.append(f"  authors' code: {ac.get('use', '?')} ({ac.get('license', 'license unknown')}) {ac.get('url') or ''}\n", style="dim")
        for o in pol.get("off_limits") or []:
            out.append("  off limits  ", style="reverse")
            out.append(f" {o}\n")
        out.append("\nextraction\n", style="bold")
        ex = extraction(pv.source_dir)
        if not ex:
            out.append("  none (`pawtrace extract run " + pv.id + "`)\n", style="dim")
        else:
            c = ex.get("counts") or {}
            out.append(
                f"  {ex.get('id', '?')} · {ex.get('backend', '?')} · {ex.get('pages', '?')} pages · "
                f"{c.get('figures', 0)} figures, {c.get('equations', 0)} equations\n",
                style="dim",
            )
        out.append("\nsource log\n", style="bold")
        out.append(
            "  the source-log module (what each person or agent read) isn't built yet;\n"
            "  it will list reads here and flag anything off limits.\n",
            style="dim",
        )
        return out

    def action_toggle_figures(self) -> None:
        self.query_one("#right").toggle_class("-hidden")


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v[:4]) + (", …" if len(v) > 4 else "") + "]"
    return str(v)


class AddPaperScreen(ModalScreen[bool]):
    """One question at a time; the rights block and its lint update as you go."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+n", "next", "next", priority=True),
        Binding("ctrl+b", "prev", "back", priority=True),
        Binding("ctrl+s", "create", "create", priority=True),
    ]

    DEFAULT_CSS = """
    AddPaperScreen { align: center middle; background: $background 70%; }
    AddPaperScreen #dialog { width: 96%; height: 92%; border: solid $border; background: $background; padding: 0 1; }
    AddPaperScreen #cols { height: 1fr; }
    AddPaperScreen #ask { width: 1fr; padding: 1 1; }
    AddPaperScreen #preview { width: 1fr; padding: 1 1; border-left: solid $border; }
    AddPaperScreen #progress { height: 1; color: $text-muted; }
    AddPaperScreen #label { text-style: bold; margin-top: 1; }
    AddPaperScreen #help { color: $text-muted; margin: 1 0; }
    AddPaperScreen #hint { color: $foreground; margin-bottom: 1; }
    AddPaperScreen OptionList { height: auto; max-height: 12; border: none; }
    AddPaperScreen Input { border: tall $border; }
    """

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.answers = intake.blank_answers()
        self.i = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield TitleBar(PRODUCT, "add paper")
            with Horizontal(id="cols"):
                with Vertical(id="ask"):
                    yield Static(id="progress")
                    yield Static(id="label")
                    yield Static(id="help")
                    yield Static(id="hint")
                    yield Input(id="text")
                    yield OptionList(id="pick")
                with VerticalScroll(id="preview"):
                    yield Static(id="yaml")
            yield Footer()

    def on_mount(self) -> None:
        self._render_q()

    @property
    def q(self) -> intake.Question:
        return intake.QUESTIONS[self.i]

    def _render_q(self) -> None:
        q, n = self.q, len(intake.QUESTIONS)
        bar = "".join(brand.PASS if j < self.i else brand.PARTIAL if j == self.i else brand.FAIL for j in range(n))
        self.query_one("#progress", Static).update(f"{bar}  {self.i + 1}/{n}")
        self.query_one("#label", Static).update(q.label + ("  (optional)" if q.optional else ""))
        self.query_one("#help", Static).update(q.help)
        self.query_one("#hint", Static).update(intake.suggest(self.answers, q.key) or "")
        text, pick = self.query_one("#text", Input), self.query_one("#pick", OptionList)
        cur = str(self.answers.get(q.key) or "")
        if q.choices:
            text.display, pick.display = False, True
            pick.clear_options()
            pick.add_options([Option(c, id=c) for c in q.choices])
            pick.highlighted = q.choices.index(cur) if cur in q.choices else 0
            pick.focus()
        else:
            pick.display, text.display = False, True
            text.value = cur
            text.focus()
        self._render_preview()

    def _render_preview(self) -> None:
        a = self.answers
        out = Text()
        out.append(f"papers/{a['paper.id'] or '<id>'}/spec.yaml\n\n", style="dim")
        out.append("paper:\n")
        for k in ("title", "year", "doi"):
            out.append(f"  {k}: {a.get('paper.' + k) or ''}\n")
        out.append("\n" + intake.render_rights(a) + "\n\n")
        probs = intake.problems(a)
        out.append("check\n", style="bold")
        if not probs:
            out.append("  clean\n", style="dim")
        for lvl, msg in probs:
            out.append(f" {lvl} ", style="reverse" if lvl == "error" else "bold")
            out.append(f" {msg}\n", style="dim")
        self.query_one("#yaml", Static).update(out)

    def _store(self) -> None:
        q = self.q
        if q.choices:
            pick = self.query_one("#pick", OptionList)
            if pick.highlighted is not None:
                self.answers[q.key] = q.choices[pick.highlighted]
        else:
            self.answers[q.key] = self.query_one("#text", Input).value.strip()

    def on_input_changed(self, ev: Input.Changed) -> None:
        self.answers[self.q.key] = ev.value.strip()
        self._render_preview()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.action_next()

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        if self.q.choices:
            self.answers[self.q.key] = str(ev.option.id)
            self._render_preview()

    def on_option_list_option_selected(self, _: OptionList.OptionSelected) -> None:
        self.action_next()

    def action_next(self) -> None:
        self._store()
        if self.i < len(intake.QUESTIONS) - 1:
            self.i += 1
            self._render_q()
        else:
            self.action_create()

    def action_prev(self) -> None:
        self._store()
        if self.i:
            self.i -= 1
            self._render_q()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_create(self) -> None:
        self._store()
        errors = [m for lvl, m in intake.problems(self.answers) if lvl == "error"]
        if errors:
            self.notify("fix first: " + errors[0], severity="error", timeout=6)
            return
        try:
            created = intake.write_paper(self.project.papers_dir, self.answers)
        except Exception as exc:  # e.g. the paper already exists
            self.notify(str(exc), severity="error", timeout=6)
            return
        self.app.notify(f"created {len(created)} files in papers/{self.answers['paper.id']}/ — next: `pawtrace playbook intake`")
        self.dismiss(True)


class AboutScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,i,q,enter", "app.pop_screen", "close")]
    DEFAULT_CSS = """
    AboutScreen { align: center middle; background: $background 80%; }
    AboutScreen #about { width: auto; height: auto; padding: 1 4; border: solid $border; background: $background; }
    """

    def compose(self) -> ComposeResult:
        t = Text(brand.SPLASH + "\n\n")
        t.append("pawtrace", style="bold")
        t.append(" · nynxlabs\n", style="dim")
        t.append("a modular pipeline for rebuilding and checking published\ncomputational research, for people and AI agents\n\n", style="dim")
        t.append(f"figures: {RENDERER}", style="dim")
        yield Static(t, id="about")


class PawtraceTui(App):
    TITLE = "pawtrace"
    CSS = """
    Screen { background: $background; }
    DataTable { background: $background; }
    DataTable > .datatable--header { background: $background; color: $text-muted; text-style: none; }
    TabbedContent Tabs { height: 1; }
    TabbedContent Underline { display: none; }
    TabbedContent Tab.-active { background: $foreground; color: $background; }
    Footer { background: $background; }
    """

    def __init__(self, project: Project, *, light: bool = False) -> None:
        super().__init__()
        self.project = project
        self._light = light

    def on_mount(self) -> None:
        for th in brand.THEMES:
            self.register_theme(th)
        self.theme = "nynxlabs-light" if self._light else "nynxlabs"
        self.push_screen(HomeScreen(self.project))


def run(project: Project, *, light: bool = False) -> int:
    PawtraceTui(project, light=light).run()
    return 0
