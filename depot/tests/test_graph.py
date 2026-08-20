import pytest

from depot.dataset import Dataset
from depot.graph import CycleError, reachable, topological


def _d(name, refs=None):
    return Dataset(name=name, type="t", refs=refs or [])


def test_single_node():
    a = _d("a")
    assert topological(a) == [a]


def test_chain_is_bottom_up():
    leaf = _d("leaf")
    mid = _d("mid", [leaf])
    root = _d("root", [mid])
    assert [x.name for x in topological(root)] == ["leaf", "mid", "root"]


def test_diamond_visits_shared_node_once():
    shared = _d("shared")
    left = _d("left", [shared])
    right = _d("right", [shared])
    root = _d("root", [left, right])

    order = topological(root)
    names = [x.name for x in order]
    assert names.count("shared") == 1
    assert names.index("shared") < names.index("left")
    assert names.index("shared") < names.index("right")
    assert names[-1] == "root"


def test_deep_diamond_keeps_every_node_once():
    bottom = _d("bottom")
    l1 = _d("l1", [bottom])
    r1 = _d("r1", [bottom])
    l2 = _d("l2", [l1, r1])
    r2 = _d("r2", [l1, r1])
    root = _d("root", [l2, r2])

    names = [x.name for x in topological(root)]
    assert sorted(names) == ["bottom", "l1", "l2", "r1", "r2", "root"]


def test_reachable_ignores_unrelated_nodes():
    other = _d("other")
    leaf = _d("leaf")
    root = _d("root", [leaf])
    assert other not in reachable(root)


def test_reachable_deduplicates_a_shared_node():
    # A diamond: shared is reachable by two paths but is one node.
    # Checked against reachable directly, because topological deduplicates
    # again through its own dictionaries and would mask a break here.
    shared = _d("shared")
    left = _d("left", [shared])
    right = _d("right", [shared])
    root = _d("root", [left, right])

    keys = [x.key for x in reachable(root)]
    assert len(keys) == len(set(keys))
    assert sorted(keys) == ["t:left", "t:right", "t:root", "t:shared"]


def test_layer_order_is_deterministic():
    b = _d("b")
    a = _d("a")
    root = _d("root", [b, a])
    assert [x.name for x in topological(root)] == ["a", "b", "root"]


def test_cycle_raises():
    a = _d("a")
    b = _d("b", [a])
    a.refs = [b]
    with pytest.raises(CycleError):
        topological(a)
