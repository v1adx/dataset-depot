"""Acceptance for layer 1 — the control scenarios from the specification."""
import time

import pandas as pd
import pytest

from depot import cache, config
from depot.dataset import Dataset
from depot.runner import run


@pytest.fixture(autouse=True)
def _cache(tmp_path):
    config.set_cache_dir(tmp_path)
    yield
    config.reset()


class Api:
    """Counts trips to an external source."""

    def __init__(self):
        self.hits = 0

    def __call__(self, dts):
        self.hits += 1
        dts.dataframe = pd.DataFrame({"n": [self.hits]})


def _passthrough(dts):
    """A transform that always leaves a non-empty dataframe."""
    dts.dataframe = pd.DataFrame({"x": [1]})


def _fresh_graph(api_recent, api_period, sink):
    """A report over two loaders on different timers: no diamonds, depth 4."""
    recent = Dataset(name="records_recent", type="source",
                     threshold=3600, cache=False, extractors=[api_recent])
    period = Dataset(name="records_period", type="source",
                     threshold=86400, cache=False, extractors=[api_period])
    records = Dataset(name="records", type="source", refs=[recent, period],
                      transforms=[_passthrough])
    sales = Dataset(name="daily_sales", type="staging", refs=[records],
                    transforms=[_passthrough])
    report = Dataset(name="payment_reconciliation", type="reports",
                     refs=[sales], transforms=[_passthrough], extras=[sink])
    return report


def test_isolated_rerun_touches_nothing():
    """Two isolated runs, with fresh Dataset objects each time."""
    recent, period = Api(), Api()
    sink = Api()

    run(_fresh_graph(recent, period, sink))
    hits_after_first = (recent.hits, period.hits, sink.hits)

    run(_fresh_graph(recent, period, sink))
    assert (recent.hits, period.hits, sink.hits) == hits_after_first


def test_timer_survives_process_restart():
    """A fresh object five minutes later does not go to the source."""
    api = Api()

    first = Dataset(name="records_recent", type="source",
                    threshold=3600, cache=False, extractors=[api])
    run(first)
    assert api.hits == 1

    second = Dataset(name="records_recent", type="source",
                     threshold=3600, cache=False, extractors=[api])
    run(second)
    assert api.hits == 1


def test_force_on_a_diamond_runs_shared_node_once():
    """A report over three staging sets, two of which share one loader."""
    api = Api()
    sink = Api()
    records = Dataset(name="records", type="source", threshold=0,
                      extractors=[api], extras=[sink])
    appointments = Dataset(name="appointments", type="staging", refs=[records],
                           transforms=[_passthrough])
    services = Dataset(name="services", type="staging", refs=[records],
                       transforms=[_passthrough])
    clients = Dataset(name="clients", type="staging", transforms=[_passthrough])
    report = Dataset(name="tomorrow", type="reports",
                     refs=[appointments, services, clients],
                     transforms=[_passthrough])

    run(report, force=True)
    assert api.hits == 1
    assert sink.hits == 1


def test_unchanged_probe_rewrites_nothing():
    """An unmoved probe: no parquet, no dependents, no extras."""
    # The probe value is on the wall clock, like a derived dataset's changed.
    # With an artificially small number (500.0) this test would pass without
    # proving anything: the dependent could never wake, whatever the probe did.
    probe_at = [time.time()]
    sink = Api()
    src = Dataset(name="src", type="raw", probe=lambda _: probe_at[0],
                  extractors=[Api()], extras=[sink])
    transform_calls = []
    derived = Dataset(name="derived", type="staging", refs=[src],
                      transforms=[lambda d: transform_calls.append(1) or _passthrough(d)])

    run(derived)
    assert sink.hits == 1
    assert len(transform_calls) == 1

    mtime_before = cache.data_path(src).stat().st_mtime_ns
    run(derived)

    assert sink.hits == 1
    assert len(transform_calls) == 1
    assert cache.data_path(src).stat().st_mtime_ns == mtime_before


def test_moved_probe_wakes_the_dependent():
    """The other side of the previous test: a moved probe must wake the dependent."""
    probe_at = [time.time()]
    src = Dataset(name="src", type="raw", probe=lambda _: probe_at[0],
                  extractors=[Api()])
    transform_calls = []
    derived = Dataset(name="derived", type="staging", refs=[src],
                      transforms=[lambda d: transform_calls.append(1) or _passthrough(d)])

    run(derived)
    assert len(transform_calls) == 1

    probe_at[0] = time.time() + 60
    run(derived)
    assert len(transform_calls) == 2


def test_quiet_run_opens_only_the_target_parquet(monkeypatch):
    """Laziness: an up-to-date chain pulls no ref into memory."""
    src = Dataset(name="src", type="raw", threshold=3600, extractors=[Api()])
    derived = Dataset(name="derived", type="staging", refs=[src],
                      transforms=[_passthrough])
    run(derived)

    opened = []
    original = cache.load
    monkeypatch.setattr(cache, "load", lambda d: opened.append(d.key) or original(d))

    fresh_src = Dataset(name="src", type="raw", threshold=3600, extractors=[Api()])
    fresh_derived = Dataset(name="derived", type="staging", refs=[fresh_src],
                            transforms=[_passthrough])
    run(fresh_derived)
    assert opened == []

    _ = fresh_derived.dataframe
    assert opened == ["staging:derived"]


def test_file_change_reaches_the_transform(tmp_path):
    """The file changed, so the transform ran over the new data."""
    import os

    source = tmp_path / "data.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    os.utime(source, (1000, 1000))

    # The probe is written out here on purpose: this scenario is about the
    # framework, not about a ready-made helper, and the ready-made file probe
    # belongs with the file templates rather than with the core.
    def newest_mtime(dts):
        return os.path.getmtime(dts.props["path"])

    def read_file(dts):
        dts.dataframe = pd.read_csv(dts.props["path"])

    def double(dts):
        dts.dataframe = dts.dataframe.assign(value=dts.dataframe["value"] * 2)

    d = Dataset(name="prices", type="raw", probe=newest_mtime,
                extractors=[read_file], transforms=[double],
                props={"path": str(source)})

    run(d)
    assert d.dataframe["value"].tolist() == [2]

    source.write_text("value\n5\n", encoding="utf-8")
    os.utime(source, (2000, 2000))
    run(d)
    assert d.dataframe["value"].tolist() == [10]


def test_ref_change_wakes_the_extractor():
    """An extractor may read a ref's values, so a ref must wake it."""
    api = Api()
    # The keys really change: a ref that produced the same list twice would be
    # saying nothing moved, and details would rightly not look again.
    keys = Dataset(name="keys", type="raw", threshold=0, extractors=[Api()])
    details = Dataset(name="details", type="raw", refs=[keys],
                      threshold=None, extractors=[api])

    run(details)
    assert api.hits == 1
    run(details)
    assert api.hits == 2


def test_idempotence_for_every_shape():
    """A second run straight after the first does nothing."""
    shapes = {
        "api": Dataset(name="api", type="s", threshold=3600, extractors=[Api()]),
        "probe": Dataset(name="probe", type="s", probe=lambda _: 1_700_000_000.0,
                         extractors=[Api()]),
        "loader": Dataset(name="loader", type="s", threshold=3600, cache=False,
                          extractors=[Api()]),
    }
    for name, dts in shapes.items():
        sink = Api()
        dts.extras = [sink]
        run(dts)
        first = sink.hits
        run(dts)
        assert sink.hits == first, f"{name} is not idempotent"
