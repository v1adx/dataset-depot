"""The panel's recovery logic, without a page.

`DatasetPanel.__init__` builds a widget tree, which needs a live nicegui slot
context — but none of the logic worth testing does. Constructing the object
through `object.__new__` and giving it the handful of attributes these methods
actually touch exercises the real code with no widgets involved, in the same
spirit as `test_meta_card.py` testing the extracted pure functions.

What is pinned here is every failure path, because all three bugs found while
this file was written lived in one: a shared recovery branch that showed the
previous dataset's tables under the new one's name, an error event that could
never fire, and a rendering bug reported to the user as a pipeline failure.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from depot import Dataset

from depot_gui.panel import DatasetPanel


class FakeCatalog:
    """Answers `dataset` from a dict; `run` returns decisions or raises."""

    def __init__(self, decisions=None, raises: Exception | None = None) -> None:
        self.decisions = decisions if decisions is not None else ["a decision"]
        self.raises = raises
        self.calls: list[tuple[str, bool]] = []

    def dataset(self, key: str) -> Dataset:
        type_, _, name = key.partition(":")
        return Dataset(name=name, type=type_)

    async def run(self, key, force=False, on_node=None):
        self.calls.append((key, force))
        if self.raises is not None:
            raise self.raises
        return self.decisions


def make_panel(catalog: FakeCatalog, dts: Dataset | None = None) -> DatasetPanel:
    panel = object.__new__(DatasetPanel)
    panel._catalog = catalog
    panel._dts = dts
    panel._decisions = []
    panel._last_error = None
    panel._node_handlers = []
    panel.states: list[str] = []
    panel.refreshed = 0
    panel.refresh_raises: Exception | None = None

    def _set_state(state: str) -> None:
        panel.states.append(state)

    def _refresh_all() -> None:
        if panel.refresh_raises is not None:
            raise panel.refresh_raises
        panel.refreshed += 1

    panel._set_state = _set_state
    panel._refresh_all = _refresh_all
    return panel


def events_of(panel: DatasetPanel) -> list[tuple]:
    seen: list[tuple] = []
    panel.on_node_event(lambda stage, decision: seen.append((stage, decision)))
    return seen


# --- the run itself ---

async def test_a_run_is_bracketed_so_the_graph_can_forget_the_previous_one():
    panel = make_panel(FakeCatalog())
    seen = events_of(panel)
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    assert seen[0] == ("run-started", None)


async def test_a_successful_run_keeps_its_decisions_and_renders_once():
    panel = make_panel(FakeCatalog(decisions=["one", "two"]))
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    assert panel._decisions == ["one", "two"]
    assert panel.refreshed == 1
    assert panel.states == ["loading", "loaded"]


async def test_a_successful_run_clears_a_previous_failure():
    panel = make_panel(FakeCatalog())
    panel._last_error = "source:records: boom"
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    assert panel._last_error is None


async def test_a_pipeline_failure_propagates_so_each_caller_can_recover_its_own_way():
    panel = make_panel(FakeCatalog(raises=RuntimeError("boom")))
    with patch("nicegui.ui.notify"), pytest.raises(RuntimeError, match="boom"):
        await panel._run("source:records")


# --- a rendering failure is not a pipeline failure ---

async def test_a_rendering_failure_does_not_claim_the_pipeline_failed():
    panel = make_panel(FakeCatalog())
    panel.refresh_raises = AttributeError("'int' object has no attribute 'replace'")
    with patch("nicegui.ui.notify") as notify:
        await panel._run("source:records")
    message = notify.call_args[0][0]
    assert message.startswith("Could not render")
    assert "Pipeline failed" not in message


async def test_a_rendering_failure_keeps_the_run_log():
    """The pipeline succeeded; its log is the useful thing on screen."""
    panel = make_panel(FakeCatalog(decisions=["one"]))
    panel.refresh_raises = ValueError("bad column")
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    assert panel._decisions == ["one"]
    assert panel._last_error is None


async def test_a_rendering_failure_does_not_paint_the_node_red():
    panel = make_panel(FakeCatalog())
    panel.refresh_raises = ValueError("bad column")
    seen = events_of(panel)
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    assert ("failed", None) not in seen


async def test_a_rendering_failure_still_leaves_the_panel_loaded():
    panel = make_panel(FakeCatalog())
    panel.refresh_raises = ValueError("bad column")
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    assert panel.states[-1] == "loaded"


# --- select and force recover differently, and must keep doing so ---

async def test_select_failing_falls_back_to_empty_and_forgets_the_dataset():
    """Otherwise the previous dataset's tables stay on screen under the new
    dataset's identity — which is what current_key() would then report."""
    panel = make_panel(FakeCatalog(raises=RuntimeError("boom")))
    with patch("nicegui.ui.notify"):
        await panel.select("source:records")
    assert panel._dts is None
    assert panel.current_key() is None
    assert panel.states[-1] == "empty"


async def test_force_failing_stays_put_because_what_is_on_screen_is_still_valid():
    dts = Dataset(name="records", type="source")
    panel = make_panel(FakeCatalog(raises=RuntimeError("boom")), dts=dts)
    with patch("nicegui.ui.notify"):
        await panel.force()
    assert panel._dts is dts
    assert panel.current_key() == "source:records"
    assert panel.states[-1] == "loaded"


async def test_both_recoveries_announce_the_failure_to_the_graph():
    panel = make_panel(FakeCatalog(raises=RuntimeError("boom")))
    seen = events_of(panel)
    with patch("nicegui.ui.notify"):
        await panel.select("source:records")
    assert ("failed", None) in seen


async def test_an_unknown_key_notifies_without_running_anything():
    catalog = FakeCatalog()
    catalog.dataset = lambda key: (_ for _ in ()).throw(KeyError(key))
    panel = make_panel(catalog)
    with patch("nicegui.ui.notify") as notify:
        await panel.select("nope:missing")
    assert "No dataset" in notify.call_args[0][0]
    assert catalog.calls == []


# --- the log ---

def test_the_log_of_a_panel_that_never_ran():
    assert make_panel(FakeCatalog()).last_log() == "nothing has run yet"


async def test_the_log_after_a_failed_select_does_not_raise_though_dts_is_gone():
    """last_log reads self._dts.key, and select's recovery sets it to None."""
    panel = make_panel(FakeCatalog(raises=RuntimeError("boom")))
    with patch("nicegui.ui.notify"):
        await panel.select("source:records")
    assert "run failed" in panel.last_log()


async def test_a_failure_outranks_the_previous_run_in_the_log():
    """Rendering the last successful run here would read as if the run that
    just failed had succeeded."""
    panel = make_panel(FakeCatalog(decisions=["one"]))
    with patch("nicegui.ui.notify"):
        await panel._run("source:records")
    panel._catalog.raises = RuntimeError("boom")
    with patch("nicegui.ui.notify"):
        await panel.select("source:records")
    assert panel.last_log().startswith("run failed")
