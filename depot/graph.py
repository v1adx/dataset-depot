"""The reachable subgraph from one node, and its topological order."""
from __future__ import annotations

from .dataset import Dataset


class CycleError(Exception):
    """A cycle was found in the dataset graph."""


def reachable(target: Dataset) -> list[Dataset]:
    """Every dataset reachable from the target through refs, each once.

    Deduplication is by identity (``type:name``), so a node reached along
    several paths is one node — which is the whole point. The old framework
    recursed instead, executing a shared dependency once per path: duplicate
    API calls, duplicate writes to external systems, and sibling branches
    computed from different snapshots of the same source and then joined.
    """
    seen: dict[str, Dataset] = {}
    stack = [target]
    while stack:
        node = stack.pop()
        if node.key in seen:
            continue
        seen[node.key] = node
        stack.extend(node.refs)
    return list(seen.values())


def topological(target: Dataset) -> list[Dataset]:
    """The reachable subgraph ordered so each node follows all of its refs.

    The target comes last. Within a layer the order is by key, so a run is
    reproducible rather than dependent on dictionary insertion order. The
    order belongs to the call that computed it and outlives nothing.
    """
    nodes = reachable(target)
    index = {n.key: n for n in nodes}

    pending = {n.key: {r.key for r in n.refs if r.key in index} for n in nodes}
    order: list[Dataset] = []

    while pending:
        ready = sorted(key for key, deps in pending.items() if not deps)
        if not ready:
            raise CycleError(
                "cycle in the dataset graph: " + ", ".join(sorted(pending))
            )
        for key in ready:
            order.append(index[key])
            del pending[key]
        for deps in pending.values():
            deps.difference_update(ready)

    return order
