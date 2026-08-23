"""A chart written as ECharts' own config, with the data named from Python.

Perspective's plugins each draw one kind of mark, so the ordinary combined
chart — income and spend as bars with the balance as a line over a second
axis — has no configuration in it. ECharts does that in one `series` list, and
NiceGUI ships ECharts already, so what was missing is not a library but an
editor.

The view is two editors, and the seam between them is the seam between data
and presentation. The top one is Python: it runs with the frame in scope and
its job is to name things — `income = df["Income"]`. The bottom one is the
ECharts config verbatim, in JavaScript, where those names are read back as
`$income`. Substitution is a single regular expression over identifiers, so
nothing has to parse either language to find the boundary.

The config stays JavaScript all the way into the browser: it is handed to
`new Function` there (which is what `chart.setOption` is given), so an example
pasted out of the ECharts gallery runs unedited — `formatter: v => v + ' ml'`
included. That is the one thing no config-as-JSON can do, and it is why the
options NiceGUI holds for this element stay empty: the chart is drawn by the
call below, not by the element's props.
"""
from __future__ import annotations

import json
import re
import time

import numpy as np
import pandas as pd
from nicegui import ui

from depot import Dataset

from ..errors import describe

LABEL = "Chart"
ICON = "insert_chart_outlined"

# How long an editor has to go quiet before its text is run. Every keystroke
# would otherwise re-run the setup, re-serialise the frame and redraw, and a
# config caught halfway through being typed is an error in the error line.
EDIT_PAUSE = 0.4

# What the setup starts with rather than what it produced. Handing `$df` to a
# chart is a mistake, and "df is not defined" from the JavaScript side says so
# more clearly than a serialised frame would.
SEEDS = ("df", "pd", "np")


def _jsonable(value):
    """One value the config can carry, for anything json refuses.

    Reached only for what `json.dumps` cannot write itself, and whatever comes
    back is written in turn — which is what lets a datetime Series come out as
    a list of ISO strings from the two lines below. NaN needs no case of its
    own: json writes it as `NaN`, which is a JavaScript literal, and ECharts
    reads it as a gap in the line.
    """
    if isinstance(value, (pd.Series, pd.Index)):
        return value.tolist()
    if hasattr(value, "isoformat"):  # Timestamp, datetime, date
        return value.isoformat()
    if isinstance(value, np.generic):  # int64, float64, bool_
        return value.item()
    return str(value)


def variables(setup: str, df: pd.DataFrame) -> dict:
    """Run the Python half; return what it named.

    Compiled under a name of its own so a traceback out of the setup reads
    `<setup>, line 3` rather than pointing at this file.
    """
    env: dict = {"df": df, "pd": pd, "np": np}
    exec(compile(setup, "<setup>", "exec"), env)  # noqa: S102 - the user's own
    return {k: v for k, v in env.items() if k not in SEEDS and not k.startswith("__")}


def inject(option: str, env: dict) -> str:
    """`$name` in the config, replaced by what the setup put in `name`.

    Only identifiers are substituted — no expressions — which is what keeps
    the config's line numbers the ones the editor shows. A name the setup
    never bound is left standing, and the browser then says `foo is not
    defined`, which is the truth and is easier to read than anything this
    function could raise about it.
    """
    return re.sub(
        r"\$([A-Za-z_]\w*)",
        lambda m: json.dumps(env[m.group(1)], default=_jsonable)
        if m.group(1) in env
        else m.group(0),
        option,
    )


def _name(column: str, taken: set[str]) -> str:
    """A column as something that can be assigned to.

    `Date.Year` is a column this app makes itself (the pivot parts), and it is
    not an identifier. Collisions are numbered rather than allowed: two columns
    must not quietly become one variable.
    """
    base = re.sub(r"\W", "_", column).strip("_") or "col"
    if base[0].isdigit():
        base = f"_{base}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def default_config(df: pd.DataFrame) -> tuple[str, str]:
    """The setup and the config a new view opens on.

    Two empty editors say nothing about what may be written in them, so a new
    view opens on a chart of its own columns: the first non-numeric column
    along the bottom, every number as a bar. The two comments are the two
    things a panel of pickers could not have offered.
    """
    numeric = [c for c in df.columns if df[c].dtype.kind in "iuf"]
    x = next((c for c in df.columns if c not in numeric), None)
    if x is None and numeric:  # nothing to group along, so the first number
        x, numeric = numeric[0], numeric[1:]
    taken: set[str] = set()
    columns = ([x] if x is not None else []) + numeric
    pairs = [(c, _name(str(c), taken)) for c in columns]

    setup = "\n".join(f"{name} = df[{json.dumps(c)}]" for c, name in pairs)
    along = f"${pairs[0][1]}" if pairs else "[]"
    series = ",\n".join(
        f'    {{ name: {json.dumps(c)}, type: "bar", data: ${name} }}'
        for c, name in pairs[1:]
    )
    option = f"""{{
  tooltip: {{ trigger: "axis", axisPointer: {{ type: "cross" }} }},
  legend: {{}},
  toolbox: {{ feature: {{
    dataView: {{}}, magicType: {{ type: ["line", "bar"] }},
    restore: {{}}, saveAsImage: {{}}
  }} }},
  xAxis: {{ type: "category", data: {along} }},
  yAxis: [{{ type: "value" }}, {{ type: "value" }}],
  series: [
{series}
    // yAxisIndex: 1 moves a series to the right-hand axis
    // tooltip: {{ valueFormatter: v => v + ' ml' }} is JavaScript, and it runs
  ]
}}"""
    return setup, option


def draw(chart_id: int, option: str) -> str:
    """The one line that puts a config on a chart, wrapped so it can fail.

    `new Function` is doing the parsing, which is the whole point: the config
    is JavaScript, not JSON, so functions in it are functions. notMerge is on
    because without it a series deleted from the config stays on the chart.

    The try/catch is what brings a syntax error back to Python — an exception
    thrown here would otherwise only reach the browser's console, and the page
    would sit there showing the last chart with no word about why.
    """
    return f"""
try {{
    getElement({chart_id}).chart.setOption(({option}), true);
    return "";
}} catch (e) {{
    return String(e);
}}"""


def render(dts: Dataset, view_id: str, config: dict) -> None:
    df = dts.dataframe
    if df.empty:
        ui.label("No data").classes("text-gray-400 text-sm m-auto")
        return

    setup, option = config.get("setup"), config.get("option")
    if setup is None or option is None:
        setup, option = default_config(df)

    # Heights are inline rather than `h-full`, because nicegui.css gives both
    # .nicegui-echart and .nicegui-codemirror a height of 16rem and which of
    # the two single-class rules wins is a question of stylesheet order. An
    # inline style is not part of that argument. `min-height:0` goes with every
    # flex child that has to shrink: without it a flex item refuses to go below
    # its content, and the panel grows past the bottom of the page instead.
    FILL = "height:100%;min-height:0;"
    with ui.splitter(value=70).classes("w-full flex-1").style("min-height:0") as split:
        with split.before:
            # Empty on purpose: what the element holds is never what is drawn,
            # because setOption is called from draw() with JavaScript the props
            # could not carry.
            chart = ui.echart({}).classes("w-full").style(FILL)
        with split.after:
            with ui.splitter(horizontal=True, value=35).classes("w-full").style(
                FILL
            ) as panes:
                with panes.before:
                    setup_editor = ui.codemirror(setup, language="Python")
                    setup_editor.classes("w-full text-xs").style(FILL)
                with panes.after:
                    with ui.column().classes("w-full gap-0").style(FILL):
                        option_editor = ui.codemirror(option, language="JavaScript")
                        option_editor.classes("w-full flex-1 text-xs").style(
                            "min-height:0"
                        )
                        error = ui.label().classes(
                            "text-red-500 text-xs whitespace-pre-wrap p-1"
                        )

    async def apply() -> None:
        """Redraw, or say why not. A config that does not run leaves the last
        one that did on the screen — the chart is the reference while the next
        version of the config is being typed."""
        try:
            env = variables(setup_editor.value, df)
        except Exception as exc:
            error.set_text(describe(exc, "chart setup"))
            return
        try:
            failure = await ui.run_javascript(
                draw(chart.id, inject(option_editor.value, env))
            )
        except Exception as exc:  # a client that went away mid-edit
            error.set_text(describe(exc, "chart"))
            return
        error.set_text(failure or "")

    def save() -> None:
        """Through the browser, because the page that writes a view's settings
        listens for `view_config_changed` and this is not that client."""
        payload = json.dumps(
            {
                "key": dts.key,
                "view": view_id,
                "config": {
                    "setup": setup_editor.value,
                    "option": option_editor.value,
                },
            },
            ensure_ascii=False,
        )
        ui.run_javascript(f"window.emitEvent('view_config_changed', {payload})")

    # ponytail: a poll rather than a cancellable timer — NiceGUI has no
    # debounce for a value change, and the alternative is juggling timer
    # handles per keystroke.
    pending = {"at": 0.0, "dirty": False}

    async def tick() -> None:
        if not pending["dirty"] or time.monotonic() - pending["at"] < EDIT_PAUSE:
            return
        pending["dirty"] = False
        await apply()
        save()

    for editor in (setup_editor, option_editor):
        editor.on_value_change(
            lambda _: pending.update(at=time.monotonic(), dirty=True)
        )
    # The first draw waits for the client: run_javascript can only be awaited
    # once there is a browser on the other end, and a timer is the first thing
    # that runs when there is.
    ui.timer(0.1, apply, once=True)
    ui.timer(EDIT_PAUSE / 2, tick)
