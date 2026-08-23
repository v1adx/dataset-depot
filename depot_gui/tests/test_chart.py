"""The chart view's pure half: running the setup, and putting what it named
into the config.

The config itself is JavaScript and is parsed by the browser, so what can be
tested here is the seam — that a name bound in Python arrives in the config as
something JavaScript can read, and that nothing else in the text is touched.
"""
import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from depot_gui.components.chart import (
    default_config,
    draw,
    inject,
    variables,
)


def frame() -> pd.DataFrame:
    return pd.DataFrame({
        "Date": pd.to_datetime(["2024-01-01", "2024-02-01"]),
        "Region": ["north", "south"],
        "Income": np.array([120, 140], dtype="int64"),
        "Spend": [90.5, 110.25],
    })


# --- The Python half ---

def test_the_setup_names_things_with_the_frame_in_scope():
    env = variables("income = df['Income'] * 2", frame())
    assert env["income"].tolist() == [240, 280]


def test_the_setup_can_aggregate_which_is_the_point_of_having_one():
    env = variables("by_region = df.groupby('Region')['Income'].sum()", frame())
    assert env["by_region"]["north"] == 120


def test_what_the_setup_started_with_is_not_what_it_named():
    """`$df` is a mistake, and it reads better as "df is not defined" from the
    browser than as a serialised frame in the config."""
    env = variables("total = df['Income'].sum()", frame())
    assert set(env) == {"total"}


def test_a_broken_setup_says_which_of_its_lines_broke():
    with pytest.raises(KeyError):
        variables("a = 1\nb = df['Nope']", frame())
    with pytest.raises(SyntaxError):
        variables("a = (", frame())


# --- Names in the config ---

def test_a_name_becomes_the_value_it_was_bound_to():
    env = variables("income = df['Income']", frame())
    assert inject("{ data: $income }", env) == "{ data: [120, 140] }"


def test_pandas_and_numpy_values_arrive_as_javascript_can_read_them():
    """A Series, a Timestamp, a Period, a numpy integer: none of them survive
    json on their own, and every one of them is what a setup reaches for."""
    env = variables(
        "when = df['Date']\nmonths = df['Date'].dt.to_period('M')\n"
        "total = df['Income'].sum()",
        frame(),
    )
    assert inject("$when", env) == '["2024-01-01T00:00:00", "2024-02-01T00:00:00"]'
    assert inject("$months", env) == '["2024-01", "2024-02"]'
    assert inject("$total", env) == "260"


def test_a_gap_stays_a_gap():
    """json writes NaN as `NaN`, which JavaScript reads and ECharts draws as a
    break in the line. Nothing has to turn it into null."""
    env = variables("spend = df['Spend'].where(df['Spend'] > 100)", frame())
    assert inject("$spend", env) == "[NaN, 110.25]"


def test_a_name_nobody_bound_is_left_for_the_browser_to_complain_about():
    assert inject("{ data: $missing }", variables("", frame())) == "{ data: $missing }"


def test_nothing_but_names_is_touched():
    """The config is JavaScript and stays JavaScript: functions, comments and
    strings go through untouched."""
    source = (
        "{ tooltip: { valueFormatter: v => v + ' ml' }, // a comment\n"
        "  label: '100$' }"
    )
    assert inject(source, {"ml": 1}) == source


# --- The config a new view opens on ---

def test_a_new_view_opens_on_something_that_draws():
    df = frame()
    setup, option = default_config(df)
    filled = inject(option, variables(setup, df))
    assert "$" not in filled
    assert '"Income"' in filled and "[120, 140]" in filled
    assert '["2024-01-01T00:00:00", "2024-02-01T00:00:00"]' in filled


def test_a_column_that_is_not_an_identifier_still_gets_a_variable():
    """`Date.Year` is a column this app makes itself."""
    df = pd.DataFrame({"Date.Year": ["2024", "2025"], "1st place": [1, 2]})
    setup, option = default_config(df)
    filled = inject(option, variables(setup, df))
    assert "$" not in filled
    assert '"1st place"' in filled  # the label keeps the column's real name


def test_two_columns_never_become_one_variable():
    df = pd.DataFrame({"a b": ["x", "y"], "a-b": [1, 2], "a.b": [3, 4]})
    setup, _ = default_config(df)
    names = [line.split(" = ")[0] for line in setup.splitlines()]
    assert len(set(names)) == 3


def test_the_default_charts_a_frame_of_numbers_only():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    setup, option = default_config(df)
    filled = inject(option, variables(setup, df))
    assert "data: [1, 2]" in filled  # the first number became the axis
    assert '{ name: "b", type: "bar", data: [3, 4] }' in filled


def test_the_default_of_an_empty_frame_still_injects():
    df = pd.DataFrame()
    setup, option = default_config(df)
    assert setup == ""
    assert "$" not in inject(option, variables(setup, df))


# --- The call that draws it ---

def test_the_drawing_call_carries_the_config_and_reports_back():
    js = draw(7, "{ series: [] }")
    assert "getElement(7).chart.setOption(({ series: [] }), true)" in js
    assert "catch" in js and "return String(e)" in js


def test_the_default_config_is_valid_javascript():
    """The config is generated JavaScript and nothing in Python parses it, so
    the check is the engine the browser uses: the same `new Function` wrapper
    `draw` relies on. Skipped where node is not installed — it guards the
    generated text, not the app."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    df = frame()
    setup, option = default_config(df)
    filled = json.dumps(inject(option, variables(setup, df)))
    script = (
        f"const o = new Function('return (' + {filled} + ')')();"
        "process.stdout.write(o.series.map(s => s.name + '/' + s.type).join(','));"
    )
    run = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "Income/bar,Spend/bar"
