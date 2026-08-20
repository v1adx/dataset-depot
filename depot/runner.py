"""Executing a subgraph: topological order, phases, and version movement.

Two entry points, both taking one dataset and walking everything it depends
on. ``plan`` reports what would happen and touches nothing; ``run`` does it.
They share the version-choosing rule so the two cannot drift apart, which is
what makes a dry run trustworthy before code that writes to external systems.

These are the programmatic API — the command line and the UI are built on
them. From a dataset, ``Dataset.load`` and ``Dataset.pipeline`` are the
convenient way in.
"""
from __future__ import annotations

import time
from typing import Callable

import pandas as pd

from . import cache
from .dataset import Dataset
from .decide import Decision, decide
from .graph import topological

Event = Callable[[str, Decision], None]


def _probe_value(dts: Dataset) -> float | None:
    return dts.probe(dts) if dts.probe is not None else None


def _source_moved(dts: Dataset) -> bool:
    """Has the module changed since the stored result was produced?

    Only once there is a stored result to compare against: on a first run
    everything is new, and saying so would be noise.
    """
    stored = cache.read_meta(dts).source
    return bool(stored) and stored != cache.source_digest(dts)


def _new_version(
    dts: Dataset,
    decision: Decision,
    probe_value: float | None,
    now: float,
    current: float,
    versions: dict[str, float] | None = None,
) -> float:
    """The dataset's version after a run.

    A version moves only when the inputs moved — never because work merely
    ran. Shared by ``plan`` and ``run``; two copies would drift.

    Three answers, in order of how well the dataset knows itself:

    A probe asked the source and was told. Take that.

    No extractors means nothing was fetched — the data is its inputs, rearranged,
    so its version is theirs and not the clock's. This is what makes the derived
    half of a graph idempotent: recomputing from unchanged inputs lands on the
    same version, and nothing downstream stirs.

    Otherwise something was fetched from a source that cannot say how old it is,
    and only the moment of fetching is honest.

    Never older than the newest ref consumed, whichever answer applies. A probe
    can be a shade behind a ref written moments earlier, and claiming the older
    version would mean "I am older than my inputs" — precisely the condition for
    recomputing, so the dataset would rebuild itself forever.
    """
    versions = versions or {}
    refs = [versions.get(r.key, r.changed) for r in dts.refs]

    if decision.probe_moved and probe_value is not None:
        version = probe_value
    elif decision.source_moved:
        # The refs cannot speak for a change in the code, and neither can a
        # clock the data came from. Only the moment of recomputing can — and
        # if the output turns out identical, the digest puts it back.
        version = now
    elif not dts.extractors:
        version = max(refs, default=now)
    elif decision.refs_moved or not dts.probe:
        version = now
    else:
        version = current

    return max([version] + refs)


def plan(target: Dataset, force: bool = False) -> list[Decision]:
    """What a run would do. No side effects.

    Probes are called and metafiles are read; nothing else. No extractor,
    transform, validator or extra runs, and nothing is written. Versions are
    projected forward through the topological order, so a node is judged
    against what its refs *would* become — otherwise only the first layer
    would be right.
    """
    decisions = []
    projected: dict[str, float] = {}
    now = time.time()
    for dts in topological(target):
        dts.load_meta()
        probe_value = _probe_value(dts)
        decision = decide(
            dts, probe_value, force=force, now=now, ref_changed=projected,
            source_moved=_source_moved(dts),
        )
        after = dts.changed
        # plan cannot know that an extractor would set the version itself,
        # because it does not run extractors.
        if decision.extract:
            after = _new_version(dts, decision, probe_value, now, dts.changed, projected)
        if after != dts.changed:
            decision.transform = True
        projected[dts.key] = after
        decisions.append(decision)
    return decisions


def run(
    target: Dataset,
    force: bool = False,
    on_event: Event | None = None,
) -> list[Decision]:
    """Bring the target and everything it depends on up to date.

    Each node executes exactly once, dependencies first. ``on_event`` is
    called with ``("started", decision)`` and ``("finished", decision)``
    around every node, in execution order, so a UI can follow along; the
    returned decisions are the same objects, complete.
    """
    decisions = []
    for dts in topological(target):
        decision = _run_one(dts, force=force, on_event=on_event)
        decisions.append(decision)
    return decisions


def _run_one(dts: Dataset, force: bool, on_event: Event | None) -> Decision:
    dts.load_meta()

    # Announced before it is judged, on a provisional verdict. The probe goes
    # to the source — a database, an API — and is the likeliest step here to
    # fail. A consumer told of a node only once its verdict existed would
    # never hear of a node whose probe raised, and, seeing a failure arrive
    # after the previous node's "finished", would blame that healthy node.
    decision = Decision(dataset=dts)
    if on_event is not None:
        on_event("started", decision)

    started = time.perf_counter()
    changed_before = dts.changed
    timestamp_before = dts.timestamp

    try:
        stored = cache.read_meta(dts)
        try:
            probe_value = _probe_value(dts)
        except Exception as exc:
            # On the provisional verdict, so the log says what happened
            # instead of reporting a node that exploded as up to date.
            decision.reasons.append(f"probe failed: {exc}")
            raise
        decision = decide(dts, probe_value, force=force,
                          source_moved=bool(stored.source) and stored.source != cache.source_digest(dts))

        try:
            if decision.extract:
                # A loader's dataframe is this run's delta, not something kept
                # between runs. Starting empty is what lets "brought nothing"
                # be answered by this run rather than by a previous one.
                if not dts.cache:
                    dts.dataframe = pd.DataFrame()

                for extractor in dts.extractors:
                    extractor(dts)
                dts.timestamp = time.time()

                # A loader that brought nothing changed nothing. Going to the
                # source and finding it quiet is the ordinary case for a timer-
                # driven extractor, and it must not wake the whole branch.
                brought_nothing = not dts.cache and dts.dataframe.empty

                if dts.changed == changed_before and not brought_nothing:
                    dts.changed = _new_version(  # the extractor set no version
                        dts, decision, probe_value, time.time(), changed_before
                    )

                if dts.changed != changed_before:
                    decision.transform = True

            if decision.transform:
                for transform in dts.transforms:
                    transform(dts)

            moved = dts.changed != changed_before

            # Validation comes before any write, so data that fails never
            # reaches the cache and the next run redoes the work.
            if decision.extract or decision.transform:
                for validator in dts.validators:
                    validator(dts)

            if moved and not cache.save(dts):
                # The work ran and produced exactly what was already stored, so
                # the source did not move after all and neither does the
                # version. The other half of the empty-delta rule: a loader
                # says "nothing arrived" with an empty frame, a product says it
                # by matching its own fingerprint.
                dts.changed = changed_before
                moved = False
                decision.reasons.append("content unchanged")

            elif force and not moved:
                # Forcing redoes the work; it does not make the result newer.
                # A version moves only when the inputs moved, so a forced run
                # over inputs that stood still recomputes and then has nothing
                # it may store — and that is worth saying, because the run
                # otherwise reads as a successful rebuild. To replace what is
                # stored, drop it first: reset, then run.
                decision.reasons.append("recomputed, not stored: version unchanged")

            # A depot older than the module fingerprint has metafiles without
            # one. Recording it on the first quiet pass is what gives the next
            # edit something to be compared against.
            backfill = cache.meta_path(dts).is_file() and not stored.source

            if not moved and (dts.timestamp != timestamp_before or backfill):
                # Went to the source and found it quiet. The data is unchanged,
                # but the visit has to survive the process or the timer restarts
                # from scratch on every run.
                cache.touch(dts)
        except Exception:
            # Restore in-memory state. Otherwise a validator failure leaves the
            # version advanced with nothing on disk to justify it, and
            # dependents in this same process would compute over invalid data.
            dts.changed = changed_before
            dts.timestamp = timestamp_before
            raise

        # Past the write the version is earned: the data is computed, valid and
        # stored. A failing extra means the delivery outward failed, not the
        # data — rolling the version back here would only make memory disagree
        # with the disk. Delivery guarantees belong to the extra itself.
        if moved:
            for extra in dts.extras:
                extra(dts)
                decision.extras_ran.append(
                    getattr(extra, "__name__", type(extra).__name__)
                )
    finally:
        decision.elapsed = time.perf_counter() - started
        if on_event is not None:
            on_event("finished", decision)

    return decision
