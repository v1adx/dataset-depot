"""The artifact viewer: what to show a file with, and what to build around it.

By depot's contract an artifact is a file; everything checked here is the
viewer's decisions about it, all taken before any nicegui is involved.
"""
import re
from html import unescape
from pathlib import Path

import pytest

from depot_gui.widgets.function_runner import artifact_file, artifact_frame, artifact_kind


def test_the_file_an_artifact_returned_is_what_gets_shown(tmp_path):
    file = tmp_path / "balance.html"
    file.write_text("<p>hi</p>", encoding="utf-8")

    assert artifact_file(file) == file
    assert artifact_file(str(file)) == file


@pytest.mark.parametrize("result", [None, "", 42, {"type": "message", "content": "hi"}])
def test_anything_that_is_not_a_file_shows_nothing(result):
    assert artifact_file(result) is None


def test_a_path_with_no_file_behind_it_shows_nothing(tmp_path):
    assert artifact_file(tmp_path / "never_written.html") is None


@pytest.mark.parametrize("name, kind", [
    ("balance.html", "markup"),
    ("balance.HTML", "markup"),
    ("chart.png", "image"),
    ("chart.svg", "image"),
    ("export.csv", "file"),
    ("report.xlsx", "file"),
])
def test_kind_is_decided_by_the_suffix(name, kind):
    assert artifact_kind(Path(name)) == kind


def _srcdoc(frame: str) -> str:
    """The document as the browser will see it — that is, after the attribute
    has been parsed."""
    return unescape(re.search(r'srcdoc="(.*)"\s+style=', frame, re.S).group(1))


def test_the_frame_carries_the_fragment_and_the_viewers_own_style():
    document = _srcdoc(artifact_frame("<p>Balance</p>"))

    assert "<p>Balance</p>" in document
    assert "font-family" in document


def test_the_frame_is_an_iframe_so_the_artifacts_scripts_run():
    # Markup assigned as innerHTML never executes its <script>; a document in
    # srcdoc does. The chart artifact depends on it.
    frame = artifact_frame("<script>drawChart()</script>")

    assert frame.startswith("<iframe")
    assert "srcdoc=" in frame


def test_quotes_in_the_fragment_do_not_break_out_of_the_attribute():
    frame = artifact_frame('<div id="chart" style="height:360px"></div>')

    assert '<div id="chart"' not in frame
    assert "&quot;chart&quot;" in frame
