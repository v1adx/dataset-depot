from depot.dataset import Dataset
from depot.decide import decide


def test_up_to_date_does_nothing():
    d = Dataset(name="x", type="t", threshold=3600)
    d.timestamp = 1000.0
    d.changed = 1000.0
    r = decide(d, now=1100.0)
    assert not r.extract and not r.transform and not r.validate
    assert not r.works
    assert r.reason == "up to date"


def test_force_wakes_extract_transform_validate():
    d = Dataset(name="x", type="t")
    d.timestamp = 1000.0
    d.changed = 1000.0
    r = decide(d, force=True, now=1100.0)
    assert r.extract and r.transform and r.validate
    assert "force" in r.reason


def test_probe_newer_wakes_extract_not_transform():
    d = Dataset(name="x", type="t", probe=lambda _: 0.0)
    d.changed = 1000.0
    r = decide(d, probe_value=2000.0, now=3000.0)
    assert r.extract and r.validate
    assert not r.transform
    assert r.probe_moved
    assert "probe" in r.reason


def test_probe_not_newer_stays_quiet():
    d = Dataset(name="x", type="t", probe=lambda _: 0.0)
    d.changed = 2000.0
    r = decide(d, probe_value=2000.0, now=3000.0)
    assert not r.works
    assert not r.probe_moved


def test_timer_wakes_extract():
    d = Dataset(name="x", type="t", threshold=100)
    d.timestamp = 1000.0
    r = decide(d, now=1100.0)
    assert r.extract and r.validate
    assert not r.transform
    assert "source outdated" in r.reason


def test_timer_not_expired():
    d = Dataset(name="x", type="t", threshold=100)
    d.timestamp = 1000.0
    r = decide(d, now=1050.0)
    assert not r.works


def test_threshold_none_means_no_timer():
    d = Dataset(name="x", type="t", threshold=None)
    d.timestamp = 0.0
    r = decide(d, now=1_000_000.0)
    assert not r.works


def test_threshold_zero_always_extracts():
    d = Dataset(name="x", type="t", threshold=0)
    d.timestamp = 1000.0
    r = decide(d, now=1000.0)
    assert r.extract


def test_probe_wins_over_threshold():
    # When a probe exists, the timer plays no part in the decision.
    d = Dataset(name="x", type="t", probe=lambda _: 0.0, threshold=1)
    d.changed = 2000.0
    d.timestamp = 0.0
    r = decide(d, probe_value=2000.0, now=1_000_000.0)
    assert not r.works


def test_ref_newer_wakes_extract_and_transform():
    ref = Dataset(name="r", type="t")
    ref.changed = 2000.0
    d = Dataset(name="x", type="t", refs=[ref])
    d.changed = 1000.0
    r = decide(d, now=3000.0)
    assert r.extract and r.transform and r.validate
    assert r.refs_moved
    assert "ref t:r" in r.reason


def test_ref_older_stays_quiet():
    ref = Dataset(name="r", type="t")
    ref.changed = 500.0
    d = Dataset(name="x", type="t", refs=[ref])
    d.changed = 1000.0
    r = decide(d, now=3000.0)
    assert not r.works


def test_first_run_wakes_via_zero_state():
    # changed = 0 and timestamp = 0: no separate "no data" condition needed.
    ref = Dataset(name="r", type="t")
    ref.changed = 1.0
    derived = Dataset(name="x", type="t", refs=[ref])
    assert decide(derived, now=10.0).transform

    api = Dataset(name="a", type="t", threshold=3600)
    assert decide(api, now=10.0).extract

    filed = Dataset(name="f", type="t", probe=lambda _: 0.0)
    assert decide(filed, probe_value=5.0, now=10.0).extract


def test_first_run_reason_is_not_a_nonsense_age():
    # now is a real epoch and timestamp is 0, so an age would be nonsense.
    d = Dataset(name="a", type="t", threshold=3600)
    r = decide(d, now=1_700_000_000.0)
    assert r.extract
    assert r.reason == "first run"


def test_reasons_are_joined():
    ref = Dataset(name="r", type="t")
    ref.changed = 2000.0
    d = Dataset(name="x", type="t", refs=[ref], threshold=1)
    d.timestamp = 2999.0
    d.changed = 1000.0
    r = decide(d, now=3000.0)
    assert "source outdated" in r.reason and "ref t:r" in r.reason
