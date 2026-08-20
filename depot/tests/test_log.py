import pandas as pd
import pytest

from depot import config
from depot.dataset import Dataset
from depot.log import render
from depot.runner import plan, run


@pytest.fixture(autouse=True)
def _cache(tmp_path):
    config.set_cache_dir(tmp_path)
    yield
    config.reset()


def _brings(rows):
    return lambda d: setattr(d, "dataframe", pd.DataFrame(rows))


def _chain():
    # A timer that holds, so a second pass has something quiet to report.
    src = Dataset(name="src", type="raw", threshold=3600, extractors=[_brings({"n": [1]})])
    mid = Dataset(name="mid", type="staging", refs=[src], transforms=[_brings({"n": [2]})])
    top = Dataset(name="top", type="reports", refs=[mid], transforms=[_brings({"n": [3]})])
    return src, mid, top


def test_nothing_to_do_says_so():
    assert render([]) == "nothing to do"


def test_a_plan_speaks_in_the_future():
    _, _, top = _chain()
    out = render(plan(top))
    assert "will run" in out and "ran in" not in out
    assert "run" in out


def test_a_finished_run_reports_the_clock():
    _, _, top = _chain()
    out = render(run(top))
    assert "ran in" in out
    assert "ms" in out or "s" in out


def test_indentation_follows_the_layers():
    _, _, top = _chain()
    lines = render(plan(top)).splitlines()[1:]

    assert lines[0].startswith("  raw:src")
    assert lines[1].startswith("    staging:mid")
    assert lines[2].startswith("      reports:top")


def test_the_header_counts_what_works():
    src, mid, top = _chain()
    run(top)                       # everything is fresh now
    out = render(plan(top))
    assert "0 of 3" in out


def test_extras_that_fired_are_named():
    def push(d):
        pass

    d = Dataset(name="src", type="raw", threshold=0,
                extractors=[_brings({"n": [1]})], extras=[push])
    assert "extras: push" in render(run(d))


def test_a_quiet_dataset_has_no_clock():
    src, _, top = _chain()
    run(top)
    out = render(run(top))
    assert "·" in out  # the mark for "did nothing", not a duration


def test_a_plan_separates_certain_from_speculative():
    # A timer firing is certain. A dataset woken only by that ref is not: the
    # ref may run and still produce the same content, and nothing short of
    # running can tell.
    _, _, top = _chain()
    lines = render(plan(top)).splitlines()

    assert "1 of 3 will run, 2 may follow" in lines[0]
    assert "run" in lines[1]      # the source, on its own timer
    assert "maybe" in lines[2]    # the middle, woken only by a ref
