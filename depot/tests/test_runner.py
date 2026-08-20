import pandas as pd
import pytest

from depot import cache, config
from depot.dataset import Dataset
from depot.runner import plan, run


@pytest.fixture(autouse=True)
def _cache(tmp_path):
    config.set_cache_dir(tmp_path)
    yield
    config.reset()


def _counter():
    calls = []

    def fn(dts):
        calls.append(dts.key)
        dts.dataframe = pd.DataFrame({"n": [len(calls)]})

    fn.calls = calls
    return fn


def test_extract_runs_on_first_run_with_timer():
    extract = _counter()
    d = Dataset(name="api", type="raw", threshold=3600, extractors=[extract])
    run(d)
    assert extract.calls == ["raw:api"]


def test_second_run_within_timer_does_nothing():
    extract = _counter()
    d = Dataset(name="api", type="raw", threshold=3600, extractors=[extract])
    run(d)
    run(d)
    assert len(extract.calls) == 1


def test_state_survives_a_fresh_dataset_object():
    extract = _counter()
    first = Dataset(name="api", type="raw", threshold=3600, extractors=[extract])
    run(first)

    second = Dataset(name="api", type="raw", threshold=3600, extractors=[extract])
    run(second)
    assert len(extract.calls) == 1


def test_extras_run_only_when_changed_moves():
    extras = _counter()
    extract = _counter()
    d = Dataset(name="api", type="raw", threshold=3600,
                extractors=[extract], extras=[extras])
    run(d)
    assert len(extras.calls) == 1
    run(d)
    assert len(extras.calls) == 1


def test_validators_do_not_run_on_a_quiet_pass():
    validator = _counter()
    extract = _counter()
    d = Dataset(name="api", type="raw", threshold=3600,
                extractors=[extract], validators=[validator])
    run(d)
    assert len(validator.calls) == 1
    run(d)
    assert len(validator.calls) == 1


def test_diamond_executes_shared_node_once_under_force():
    shared_extract = _counter()
    shared = Dataset(name="shared", type="t", threshold=0,
                     extractors=[shared_extract])
    left = Dataset(name="left", type="t", refs=[shared], transforms=[_counter()])
    right = Dataset(name="right", type="t", refs=[shared], transforms=[_counter()])
    root = Dataset(name="root", type="t", refs=[left, right],
                   transforms=[_counter()])

    run(root, force=True)
    assert len(shared_extract.calls) == 1


def test_ref_change_wakes_dependent():
    src = Dataset(name="src", type="t", threshold=0, extractors=[_counter()])
    transform = _counter()
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[transform])

    run(derived)
    assert len(transform.calls) == 1
    run(derived)
    assert len(transform.calls) == 2  # threshold=0 on src moves changed every run


def test_quiet_chain_stays_quiet():
    src = Dataset(name="src", type="t", threshold=3600, extractors=[_counter()])
    transform = _counter()
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[transform])

    run(derived)
    run(derived)
    assert len(transform.calls) == 1


def test_probe_drives_extract():
    probe_value = [100.0]
    extract = _counter()
    d = Dataset(name="file", type="raw",
                probe=lambda _: probe_value[0], extractors=[extract])

    run(d)
    assert len(extract.calls) == 1

    run(d)
    assert len(extract.calls) == 1

    probe_value[0] = 200.0
    run(d)
    assert len(extract.calls) == 2


def test_probe_value_becomes_changed():
    d = Dataset(name="file", type="raw",
                probe=lambda _: 4242.0, extractors=[_counter()])
    run(d)
    assert d.changed == 4242.0


def test_extract_wakes_transform_in_the_same_pass():
    extract = _counter()
    transform = _counter()
    d = Dataset(name="file", type="raw", probe=lambda _: 100.0,
                extractors=[extract], transforms=[transform])
    run(d)
    assert len(transform.calls) == 1


def test_force_with_unchanged_probe_writes_nothing_outside():
    extras = _counter()
    d = Dataset(name="file", type="raw", probe=lambda _: 100.0,
                extractors=[_counter()], extras=[extras])
    run(d)
    assert len(extras.calls) == 1

    run(d, force=True)
    assert len(extras.calls) == 1  # the source did not move, so nothing is written outward


def test_extractor_may_set_changed_itself():
    def extract(dts):
        dts.dataframe = pd.DataFrame({"x": [1]})
        dts.changed = 777.0

    d = Dataset(name="loader", type="raw", threshold=0, extractors=[extract])
    run(d)
    assert d.changed == 777.0


def test_cache_false_writes_meta_but_no_parquet():
    d = Dataset(name="loader", type="raw", threshold=0, cache=False,
                extractors=[_counter()])
    run(d)
    assert cache.meta_path(d).is_file()
    assert not cache.exists(d)


def test_timer_survives_restart_for_cache_false():
    extract = _counter()
    first = Dataset(name="loader", type="raw", threshold=3600, cache=False,
                    extractors=[extract])
    run(first)

    second = Dataset(name="loader", type="raw", threshold=3600, cache=False,
                     extractors=[extract])
    run(second)
    assert len(extract.calls) == 1


def test_plan_has_no_side_effects():
    extract = _counter()
    extras = _counter()
    d = Dataset(name="api", type="raw", threshold=3600,
                extractors=[extract], extras=[extras])

    decisions = plan(d)
    assert extract.calls == []
    assert extras.calls == []
    assert not cache.meta_path(d).exists()
    assert len(decisions) == 1
    assert decisions[0].extract


def test_plan_matches_run_order():
    leaf = Dataset(name="leaf", type="t", threshold=0, extractors=[_counter()])
    root = Dataset(name="root", type="t", refs=[leaf], transforms=[_counter()])
    assert [d.dataset.key for d in plan(root)] == ["t:leaf", "t:root"]


def test_plan_projects_versions_through_the_chain():
    # Regression: plan() judged every node against its refs' current version
    # and was therefore only correct for the first layer.
    leaf = Dataset(name="leaf", type="t", threshold=0, extractors=[_counter()])
    root = Dataset(name="root", type="t", refs=[leaf], transforms=[_counter()])

    decisions = {d.dataset.key: d for d in plan(root)}
    assert decisions["t:leaf"].extract
    assert decisions["t:root"].transform
    assert "ref t:leaf" in decisions["t:root"].reason


def test_plan_agrees_with_run():
    leaf = Dataset(name="leaf", type="t", threshold=0, extractors=[_counter()])
    mid = Dataset(name="mid", type="t", refs=[leaf], transforms=[_counter()])
    root = Dataset(name="root", type="t", refs=[mid], transforms=[_counter()])

    predicted = [(d.dataset.key, d.extract, d.transform) for d in plan(root)]
    actual = [(d.dataset.key, d.extract, d.transform) for d in run(root)]
    assert predicted == actual


def test_events_are_emitted_per_node():
    leaf = Dataset(name="leaf", type="t", threshold=0, extractors=[_counter()])
    root = Dataset(name="root", type="t", refs=[leaf], transforms=[_counter()])

    seen = []
    run(root, on_event=lambda kind, d: seen.append((kind, d.dataset.key)))
    assert seen == [
        ("started", "t:leaf"), ("finished", "t:leaf"),
        ("started", "t:root"), ("finished", "t:root"),
    ]


def _explodes(_d):
    raise RuntimeError("the database is down")


def test_a_node_whose_probe_raises_is_still_announced():
    """The probe fails before there is a verdict — say the node's name anyway.

    A consumer that only hears of a node once it has been judged never hears
    of this one at all: the failure arrives right after the previous node's
    "finished", so anything tracking "what is running now" would attribute it
    to a node that had just succeeded. Watch a graph through such a consumer
    and the healthy dataset turns red while the broken one stays green.
    """
    leaf = Dataset(name="leaf", type="t", threshold=0, extractors=[_counter()])
    root = Dataset(name="root", type="t", refs=[leaf], probe=_explodes)

    seen = []
    with pytest.raises(RuntimeError, match="the database is down"):
        run(root, on_event=lambda kind, d: seen.append((kind, d.dataset.key)))

    assert seen == [
        ("started", "t:leaf"), ("finished", "t:leaf"),
        ("started", "t:root"), ("finished", "t:root"),
    ]


def test_a_probe_failure_says_so_in_the_log():
    """Otherwise the node reads as "up to date" — its provisional verdict."""
    d = Dataset(name="api", type="raw", probe=_explodes)

    seen = []
    with pytest.raises(RuntimeError):
        run(d, on_event=lambda kind, decision: seen.append(decision))

    assert "probe failed: the database is down" in seen[-1].reason
    assert seen[-1].works is False


def test_reason_is_recorded():
    d = Dataset(name="api", type="raw", threshold=3600, extractors=[_counter()])
    decisions = run(d)
    # First run: timestamp == 0 gives "first run" (see decide.py and
    # test_first_run_reason_is_not_a_nonsense_age in test_decide.py).
    assert "first run" in decisions[0].reason


def test_pipeline_and_load_are_wrappers():
    d = Dataset(name="api", type="raw", threshold=0, extractors=[_counter()])
    d.pipeline()
    assert not d.dataframe.empty

    other = Dataset(name="api2", type="raw", threshold=0, extractors=[_counter()])
    df = other.load()
    assert isinstance(df, pd.DataFrame)
    assert not df.empty


def test_force_with_unchanged_probe_does_not_rewrite_the_data():
    d = Dataset(name="file", type="raw", probe=lambda _: 100.0,
                extractors=[_counter()])
    run(d)
    data_mtime = cache.data_path(d).stat().st_mtime_ns
    stored = cache.read_meta(d)

    run(d, force=True)

    assert cache.data_path(d).stat().st_mtime_ns == data_mtime
    # The version is unchanged — the source did not move — but the visit is
    # recorded, and the stored description of the parquet survives it.
    after = cache.read_meta(d)
    assert after.changed == stored.changed
    assert after.timestamp > stored.timestamp
    assert (after.schema, after.shape, after.nested) == (stored.schema, stored.shape, stored.nested)


def test_failing_validator_persists_nothing_and_keeps_the_old_version():
    def boom(dts):
        raise AssertionError("the data is invalid")

    fired = []
    d = Dataset(name="api", type="raw", threshold=3600,
                extractors=[_counter()], validators=[boom],
                extras=[lambda dts: fired.append(1)])

    with pytest.raises(AssertionError):
        run(d)

    assert not cache.meta_path(d).exists()
    assert not cache.exists(d)
    assert d.changed == 0.0
    assert fired == []


# --- the empty delta of a loader (cache=False) ------------------------------

def _brings(rows):
    def extract(dts):
        dts.dataframe = pd.DataFrame(rows)
    return extract


def test_loader_that_brought_nothing_does_not_move_the_version():
    d = Dataset(name="recent", type="raw", threshold=0, cache=False,
                extractors=[_brings([])])
    run(d)
    assert d.changed == 0.0      # went to the source, nothing was there
    assert d.timestamp > 0.0     # but the timer restarts all the same


def test_loader_that_brought_nothing_keeps_dependents_asleep():
    src = Dataset(name="recent", type="raw", threshold=0, cache=False,
                  extractors=[_brings([])])
    transform = _counter()
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[transform])

    run(derived)
    run(derived)
    assert transform.calls == []


def test_loader_that_brought_something_wakes_dependents():
    src = Dataset(name="recent", type="raw", threshold=0, cache=False,
                  extractors=[_brings({"id": [1]})])
    transform = _counter()
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[transform])

    run(derived)
    assert len(transform.calls) == 1


def test_the_delta_is_this_runs_delta_not_the_previous_ones(monkeypatch):
    # An extractor that returns early without assigning must not be credited
    # with what a previous run brought. The clock is faked because the two
    # runs otherwise land in the same millisecond on Windows, and a regression
    # would set changed to a "now" indistinguishable from the old one.
    from depot import runner

    ticks = iter(range(1_000_000, 1_000_100))
    monkeypatch.setattr(runner.time, "time", lambda: float(next(ticks)))

    brought = [{"id": [1]}]

    def extract(dts):
        if brought:
            dts.dataframe = pd.DataFrame(brought.pop())

    d = Dataset(name="recent", type="raw", threshold=0, cache=False,
                extractors=[extract])
    run(d)
    first = d.changed
    assert first > 0.0

    run(d)
    assert d.changed == first


def test_an_empty_frame_still_counts_as_work_for_a_product_dataset():
    # The rule reads a dataframe as a delta only when it is not the product.
    d = Dataset(name="report", type="t", threshold=0, extractors=[_brings([])])
    run(d)
    assert d.changed > 0.0


def test_a_quiet_visit_survives_the_process():
    # Regression: the metafile was written only when the version moved, so a
    # loader that went to the source and found it quiet never persisted its
    # timestamp — and every new process started its timer from zero.
    extract = _brings([])
    first = Dataset(name="recent", type="raw", threshold=3600, cache=False,
                    extractors=[extract])
    run(first)
    assert cache.read_meta(first).timestamp > 0.0

    calls = []
    second = Dataset(name="recent", type="raw", threshold=3600, cache=False,
                     extractors=[lambda d: calls.append(1)])
    run(second)
    assert calls == []  # the timer holds across the process boundary


def test_a_dataset_is_never_older_than_its_refs():
    # Regression: a probe answers for the source it watches, and that answer
    # can be a shade behind a ref written moments earlier. Taking it verbatim
    # left the dependent permanently older than its input, so it recomputed on
    # every single run.
    src = Dataset(name="src", type="t", threshold=3600, extractors=[_counter()])
    behind = Dataset(name="derived", type="t", refs=[src],
                     probe=lambda d: 1.0,  # always far behind src's time.time()
                     extractors=[_counter()])

    run(behind)
    transform = _counter()
    behind.transforms = [transform]

    run(behind)
    assert transform.calls == []


# --- where a version comes from ----------------------------------------------

def test_a_pure_transform_takes_its_refs_version():
    src = Dataset(name="src", type="t", threshold=3600, extractors=[_counter()])
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[_counter()])

    run(derived)
    assert derived.changed == src.changed  # not the clock: the inputs


def test_a_pure_transform_is_idempotent_under_force():
    # Nothing was fetched, so recomputing cannot have produced newer data. The
    # version has to stay where it was, or every forced run would wake the graph.
    # The source brings the same rows each time, so it holds its version too —
    # force reaches the refs as well, and a source that really did fetch
    # something new would be right to move.
    src = Dataset(name="src", type="t", threshold=3600, extractors=[_brings({"n": [1]})])
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[_counter()])
    run(derived)
    first = derived.changed

    run(derived, force=True)
    assert derived.changed == first


def test_a_forced_transform_does_not_wake_its_own_dependents():
    src = Dataset(name="src", type="t", threshold=3600, extractors=[_brings({"n": [1]})])
    middle = Dataset(name="middle", type="t", refs=[src], transforms=[_counter()])
    downstream = _counter()
    top = Dataset(name="top", type="t", refs=[middle], transforms=[downstream])

    run(top)
    assert len(downstream.calls) == 1

    run(middle, force=True)  # rebuild the middle for its own sake
    run(top)
    assert len(downstream.calls) == 1  # the middle produced the same version


def test_an_extractor_without_a_probe_still_takes_the_clock():
    # It went outside to a source that cannot say how old its answer is.
    d = Dataset(name="api", type="t", threshold=0, extractors=[_counter()])
    run(d)
    assert d.changed > 0.0


# --- a product that came out the same ---------------------------------------

def test_identical_content_does_not_move_the_version():
    d = Dataset(name="table", type="t", threshold=0, extractors=[_brings({"n": [1, 2]})])
    run(d)
    first = d.changed
    assert first > 0.0

    run(d)
    assert d.changed == first


def test_identical_content_keeps_dependents_asleep():
    src = Dataset(name="src", type="t", threshold=0, extractors=[_brings({"n": [1, 2]})])
    transform = _counter()
    derived = Dataset(name="derived", type="t", refs=[src], transforms=[transform])

    run(derived)
    assert len(transform.calls) == 1

    run(derived)
    assert len(transform.calls) == 1  # the source was consulted and said the same thing


def test_identical_content_does_not_rewrite_the_parquet():
    d = Dataset(name="table", type="t", threshold=0, extractors=[_brings({"n": [1, 2]})])
    run(d)
    written = cache.data_path(d).stat().st_mtime_ns

    run(d)
    assert cache.data_path(d).stat().st_mtime_ns == written
    assert cache.read_meta(d).timestamp > 0.0  # the visit is still recorded


def test_identical_content_does_not_fire_extras():
    extras = _counter()
    d = Dataset(name="table", type="t", threshold=0,
                extractors=[_brings({"n": [1, 2]})], extras=[extras])
    run(d)
    assert len(extras.calls) == 1

    run(d)
    assert len(extras.calls) == 1  # nothing new to push outward


def test_changed_content_still_moves_the_version():
    rows = [{"n": [1, 2]}, {"n": [3, 4]}]
    d = Dataset(name="table", type="t", threshold=0,
                extractors=[lambda dts: dts.__setattr__("dataframe", pd.DataFrame(rows.pop(0)))])
    run(d)
    first = d.changed

    run(d)
    assert d.changed > first


def test_a_missing_parquet_is_rewritten_even_when_the_content_matches():
    d = Dataset(name="table", type="t", threshold=0, extractors=[_brings({"n": [1, 2]})])
    run(d)
    cache.data_path(d).unlink()

    run(d)
    assert cache.data_path(d).is_file()


def test_a_loader_is_still_governed_by_its_delta(monkeypatch):
    # cache=False has no stored product to compare against; the empty-delta
    # rule is what answers for it, and it is unaffected — an identical batch
    # still counts as something arriving.
    from depot import runner

    ticks = iter(range(1_000_000, 1_000_100))
    monkeypatch.setattr(runner.time, "time", lambda: float(next(ticks)))

    d = Dataset(name="loader", type="t", threshold=0, cache=False,
                extractors=[_brings({"id": [1]})])
    run(d)
    first = d.changed

    run(d)
    assert d.changed > first


# --- a failing extra --------------------------------------------------------

def test_failing_extra_keeps_the_version_that_was_written():
    def boom(dts):
        raise RuntimeError("the source is down")

    d = Dataset(name="api", type="raw", threshold=3600,
                extractors=[_counter()], extras=[boom])

    with pytest.raises(RuntimeError):
        run(d)

    # The data was computed, validated and stored; only the delivery failed.
    # Rolling the version back would leave memory disagreeing with the disk.
    assert d.changed > 0.0
    assert cache.read_meta(d).changed == d.changed
    assert cache.exists(d)


def test_failing_extra_is_not_retried_next_run():
    # The accepted consequence of the rule above: making the extra run again
    # is the extra's own business, not the framework's.
    calls = []

    def boom(dts):
        calls.append(1)
        raise RuntimeError("the source is down")

    extract = _counter()
    d = Dataset(name="api", type="raw", threshold=3600,
                extractors=[extract], extras=[boom])

    with pytest.raises(RuntimeError):
        run(d)

    run(d)  # quiet: the version is already on disk
    assert len(calls) == 1
    assert len(extract.calls) == 1


def test_failing_cache_write_still_rolls_back(monkeypatch):
    from depot import runner

    def boom(dts):
        raise OSError("disk full")

    monkeypatch.setattr(runner.cache, "save", boom)
    d = Dataset(name="api", type="raw", threshold=3600, extractors=[_counter()])

    with pytest.raises(OSError):
        run(d)
    assert d.changed == 0.0  # nothing was stored, so nothing is claimed


def test_failing_validator_does_not_wake_dependents_in_the_same_process():
    def boom(dts):
        raise AssertionError("the data is invalid")

    src = Dataset(name="src", type="raw", threshold=3600,
                  extractors=[_counter()], validators=[boom])
    transform_calls = []
    derived = Dataset(name="derived", type="staging", refs=[src],
                      transforms=[lambda d: transform_calls.append(1)])

    with pytest.raises(AssertionError):
        run(derived)
    assert transform_calls == []

    # The second run in the same process tries src again rather than
    # computing the dependent over data that failed validation.
    with pytest.raises(AssertionError):
        run(derived)
    assert transform_calls == []


# --- the module is an input too ----------------------------------------------

_written = [0]


def _module(tmp_path, body: str):
    """A dataset declared in a real file, so it has a module to fingerprint.

    The mtime is pushed forward by hand. Two versions of the same length,
    written into the same path within one clock tick, look unchanged to
    Python's bytecode cache — it would hand back the previous compilation and
    the test would be measuring nothing.
    """
    import importlib.util
    import os

    path = tmp_path / "mod.py"
    path.write_text(body, encoding="utf-8")
    _written[0] += 1
    stamp = path.stat().st_mtime + _written[0]
    os.utime(path, (stamp, stamp))

    spec = importlib.util.spec_from_file_location(f"mod_{_written[0]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module.dts


def test_editing_the_module_makes_it_recompute(tmp_path):
    body = ("import pandas as pd\n"
            "from depot import Dataset\n"
            "dts = Dataset(name='m', type='t', threshold=3600,\n"
            "              extractors=[lambda d: setattr(d, 'dataframe', pd.DataFrame({{'n': [{v}]}}))])\n")
    path, dts = _module(tmp_path, body.format(v=1))
    run(dts)
    assert dts.dataframe["n"].tolist() == [1]

    # The timer has not expired and no ref moved: only the code is different.
    _, edited = _module(tmp_path, body.format(v=2))
    decisions = run(edited)

    assert "module changed" in decisions[0].reason
    assert edited.dataframe["n"].tolist() == [2]


def test_a_cosmetic_edit_does_not_wake_dependents(tmp_path):
    body = ("import pandas as pd\n"
            "from depot import Dataset\n"
            "{comment}"
            "dts = Dataset(name='m', type='t', threshold=3600,\n"
            "              extractors=[lambda d: setattr(d, 'dataframe', pd.DataFrame({{'n': [1]}}))])\n")
    path, dts = _module(tmp_path, body.format(comment=""))
    run(dts)
    first = dts.changed

    _, edited = _module(tmp_path, body.format(comment="# a comment\n"))
    decisions = run(edited)

    # It recomputed — it had to, the code is different — and produced exactly
    # what was already stored, so the version stays and nothing downstream stirs.
    assert "module changed" in decisions[0].reason
    assert "content unchanged" in decisions[0].reason
    assert edited.changed == first


def test_the_edit_is_reported_once_not_for_ever(tmp_path):
    body = ("import pandas as pd\n"
            "from depot import Dataset\n"
            "{comment}"
            "dts = Dataset(name='m', type='t', threshold=3600,\n"
            "              extractors=[lambda d: setattr(d, 'dataframe', pd.DataFrame({{'n': [1]}}))])\n")
    _module(tmp_path, body.format(comment=""))[1].pipeline()
    _, edited = _module(tmp_path, body.format(comment="# a comment\n"))
    run(edited)

    _, again = _module(tmp_path, body.format(comment="# a comment\n"))
    assert "module changed" not in run(again)[0].reason



def test_force_says_when_it_recomputed_and_stored_nothing():
    """Forcing redoes the work; it does not make the answer newer.

    A version moves only when the inputs moved, so forcing over inputs that
    stood still recomputes and then has nothing it may store. Silence there
    reads as a successful rebuild — which is how a run that kept the old data
    comes to look like one that replaced it.
    """
    src = Dataset(name="src", type="quiet", threshold=3600, extractors=[_brings({"n": [1]})])
    derived = Dataset(name="derived", type="quiet", refs=[src], transforms=[_counter()])
    run(derived)
    stored = cache.read_meta(derived)

    decision = run(derived, force=True)[-1]

    assert "recomputed, not stored" in decision.reason
    assert cache.read_meta(derived).changed == stored.changed
