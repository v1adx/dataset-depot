"""node_painter: a run event → how the node looks, a failure included.

The runner sends "finished" from a finally block — before the exception from
the failed node reaches Catalog.run — so a node that fell over reports
"started" → "finished" exactly as a successful one does, and only then does
"failed" arrive with no decision. This file reproduces that sequence and checks
that the node ends up painted "error" rather than left "done".
"""
from __future__ import annotations

from depot import Dataset, Decision

from depot_gui.pages import node_painter


class FakeFlow:
    """Records the calls instead of reaching cytoscape via ui.run_javascript."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def mark(self, key, state, label=None, reason="") -> None:
        self.calls.append(("mark", key, state))

    def apply(self, stage, decision) -> None:
        self.calls.append(("apply", stage, decision))


def _decision(type_="source", name="records", extract=True) -> Decision:
    return Decision(dataset=Dataset(name=name, type=type_), extract=extract)


def test_a_node_that_dies_mid_run_ends_up_marked_error():
    flow = FakeFlow()
    on_node = node_painter(flow)
    decision = _decision()

    on_node("started", decision)
    on_node("finished", decision)  # runner's finally fires before the exception propagates
    on_node("failed", None)

    assert flow.calls[-1] == ("mark", "source:records", "error")


def test_the_error_mark_comes_after_finished_was_applied():
    flow = FakeFlow()
    on_node = node_painter(flow)
    decision = _decision()

    on_node("started", decision)
    on_node("finished", decision)
    on_node("failed", None)

    kinds = [call[0] for call in flow.calls]
    assert kinds == ["apply", "apply", "mark"]
    assert flow.calls[1] == ("apply", "finished", decision)


def test_a_successful_run_never_marks_error():
    flow = FakeFlow()
    on_node = node_painter(flow)
    decision = _decision()

    on_node("started", decision)
    on_node("finished", decision)

    assert ("mark", "source:records", "error") not in flow.calls


def test_failed_with_nothing_started_marks_nothing():
    flow = FakeFlow()
    on_node = node_painter(flow)

    on_node("failed", None)

    assert flow.calls == []


def test_two_nodes_in_sequence_the_second_one_dying_only_marks_the_second():
    flow = FakeFlow()
    on_node = node_painter(flow)
    first = _decision(name="records")
    second = _decision(name="sales")

    on_node("started", first)
    on_node("finished", first)
    on_node("started", second)
    on_node("finished", second)
    on_node("failed", None)

    assert flow.calls[-1] == ("mark", "source:sales", "error")


def test_a_run_that_fails_before_its_first_started_does_not_repaint_a_previous_runs_node():
    """_run_one probes and reads cache.read_meta before its first "started" —
    both may reach a remote source. If that blows up on the very first node
    of a run, last_started still holds whatever the PREVIOUS run finished on,
    a node that may well have succeeded. DatasetPanel emits "run-started"
    before every catalog.run to give the tracker a chance to forget that, so
    a run failing with no "started" at all must mark nothing — not the
    healthy node left over from run 1.
    """
    flow = FakeFlow()
    on_node = node_painter(flow)
    node_a = _decision(name="records")

    on_node("run-started", None)
    on_node("started", node_a)
    on_node("finished", node_a)

    calls_after_run_1 = list(flow.calls)

    on_node("run-started", None)
    on_node("failed", None)  # run 2 died before reporting any "started"

    assert flow.calls == calls_after_run_1
    assert ("mark", "source:records", "error") not in flow.calls
