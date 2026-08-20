"""The condition table: from a dataset's state, which phases run and why."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .dataset import Dataset, _format_age


@dataclass
class Decision:
    """What will happen to one dataset, and the reason it will happen.

    The runner fills ``elapsed`` and ``extras_ran`` once the work is done;
    ``plan`` leaves them empty. Rendering a list of these is the pipeline log,
    which is why a dry run and a real run print the same shape.
    """

    dataset: Dataset
    extract: bool = False
    transform: bool = False
    validate: bool = False
    probe_moved: bool = False
    refs_moved: bool = False
    source_moved: bool = False
    certain: bool = False  # something about this dataset fired, not just a ref
    reasons: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    extras_ran: list[str] = field(default_factory=list)

    @property
    def works(self) -> bool:
        return self.extract or self.transform

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "up to date"


def decide(
    dts: Dataset,
    probe_value: float | None = None,
    force: bool = False,
    now: float | None = None,
    ref_changed: dict[str, float] | None = None,
    source_moved: bool = False,
) -> Decision:
    """Which phases to run for one dataset, and why.

    Pure: no I/O of any kind. The probe belongs to the caller, which invokes
    it and hands the answer in as ``probe_value`` — that keeps this function
    testable and keeps a dry run from paying for anything twice.

    Conditions are OR-ed; each one that fires adds its phases. Something moved
    if: it was forced, the module that declares it changed, the probe answers
    with a newer version, the timer expired, or a ref moved. A probe and a
    timer are mutually exclusive: when a probe exists it answers exactly, and
    the timer is not consulted at all.

    No condition is needed for "never run before". A fresh dataset has
    ``changed = 0`` and ``timestamp = 0``, so a file-backed dataset fires on
    ``probe() > 0``, a derived one on ``ref.changed > 0``, and one with a
    timer on its own first-run branch.

    ``ref_changed`` lets ``plan`` project versions forward through the graph
    without executing anything; left out, refs are read from their real state.
    """
    now = time.time() if now is None else now
    r = Decision(dts)

    if force:
        r.extract = r.transform = r.validate = True
        r.certain = True
        r.reasons.append("force")

    if source_moved:
        # The module that produces this data is an input to it. Editing it and
        # leaving the old parquet in place is how a dataset comes to serve
        # something its own code can no longer produce.
        r.extract = r.transform = r.validate = True
        r.source_moved = r.certain = True
        r.reasons.append("module changed")

    if dts.probe is not None:
        if probe_value is not None and probe_value > dts.changed:
            r.extract = r.validate = True
            r.probe_moved = r.certain = True
            r.reasons.append("probe moved")
    elif dts.threshold is not None:
        if dts.timestamp == 0:
            r.extract = r.validate = True
            r.certain = True
            r.reasons.append("first run")
        elif (now - dts.timestamp) >= dts.threshold:
            r.extract = r.validate = True
            r.certain = True
            #age = now - dts.timestamp
            r.reasons.append(f"source outdated")

    versions = ref_changed or {}
    moved = [
        ref for ref in dts.refs
        if versions.get(ref.key, ref.changed) > dts.changed
    ]
    if moved:
        r.extract = r.transform = r.validate = True
        r.refs_moved = True
        r.reasons.extend(f"ref {ref.key}" for ref in moved)

    return r
