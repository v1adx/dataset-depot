"""Finding the datasets a project declares, and how they relate.

A dataset is declared by a module that builds one, so discovering them means
importing. There is no static route: the type comes from the path, a name can
be an expression, refs are import aliases, and a template call is a call. The
spec settled this — an AST reader would be guessing.

What that costs is one import of every dataset module, about a third of a
second for forty of them. Anything that needs the answer without paying it
reads the index dataset instead (see templates.DatasetIndex), which caches this
and re-runs when a module's mtime moves.
"""
from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config
from .dataset import Dataset


@dataclass
class Scan:
    """What a sweep of the source tree turned up, and what it could not read.

    Failures are returned rather than printed: a library has no business
    deciding where a warning goes, and a caller that hides them is how a broken
    module turns into "no such dataset".
    """

    found: dict[str, "Found"]
    failures: dict[str, str]


@dataclass
class Found:
    """A dataset and where it was declared."""

    dataset: Dataset
    module: str
    variable: str

    @property
    def doc(self) -> str:
        """The module docstring: in this convention it documents the dataset."""
        return (sys.modules[self.module].__doc__ or "").strip()


def discover(root: Path | str | None = None) -> Scan:
    """Every dataset declared under the root, keyed by ``type:name``.

    A module that cannot be imported is set aside rather than taking the whole
    sweep down with it — one broken dataset should not hide the other
    thirty-six — but it is handed back, not swallowed.
    """
    root = Path(root) if root else config.source()
    base = root.resolve().parent

    # A ref is a plain import — `from datasets.source.records import dts` — so
    # the root has to be an importable package, and its parent has to be on the
    # path for that to be true. Nothing else puts it there: the root is a
    # filesystem path out of the environment, and it need not sit inside the
    # project reading it. Mutating sys.path is a liberty, but a small one next
    # to what this function already does, which is import every module it finds.
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))

    found: dict[str, Found] = {}
    failures: dict[str, str] = {}

    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = str(path.resolve().relative_to(base).with_suffix("")).replace(os.sep, ".")
        try:
            module = sys.modules.get(module_name) or importlib.import_module(module_name)
        except Exception as e:
            failures[str(path)] = f"{type(e).__name__}: {e}"
            continue
        declared_in = path.resolve()
        for variable, obj in vars(module).items():
            if variable.startswith("dts") and isinstance(obj, Dataset):
                # Only what this module declares. Refs are read as
                # `ref.dataframe`, so the object has to be imported, and the
                # importer names it whatever reads best — `dts_rates` as
                # readily as `rates`. Taking every visible Dataset would make
                # the importer a second declarant of an identity it never
                # created, and, since the sweep is ordered by path, an importer
                # sorted earlier would take the identity away from the module
                # that owns it. The declaring file is recorded at construction
                # precisely so this can be told apart.
                if obj.source is not None and obj.source != declared_in:
                    continue
                clash = found.get(obj.key)
                if clash and clash.module != module_name:
                    # Two files, one identity. A path can no longer produce
                    # this — the type keeps its folders — but a name or a type
                    # written by hand still can, from anywhere in the tree, and
                    # the two would then share one parquet. Keeping the first
                    # and dropping the second in silence is the worst option.
                    failures[str(path)] = (
                        f"identity {obj.key!r} is already taken by {clash.module}"
                    )
                    continue
                found.setdefault(obj.key, Found(obj, module_name, variable))

    return Scan(found, failures)


def datasets(root: Path | str | None = None) -> list[Dataset]:
    """Just the datasets, for when the declaration site does not matter."""
    return [f.dataset for f in discover(root).found.values()]


def get(name: str, root: Path | str | None = None) -> Dataset:
    """One dataset by ``type:name``, or by name alone when it is unambiguous."""
    scan = discover(root)
    if name in scan.found:
        return scan.found[name].dataset

    matches = [f for key, f in scan.found.items() if f.dataset.name == name]
    if len(matches) == 1:
        return matches[0].dataset
    if len(matches) > 1:
        raise KeyError(f"{name!r} is ambiguous: " + ", ".join(sorted(m.dataset.key for m in matches)))

    # It may well be there and simply unreadable. Saying "no such dataset"
    # when the module is one import away from working sends people hunting
    # for the wrong thing.
    broken = [f"{path}: {why}" for path, why in scan.failures.items() if Path(path).stem in name or name in path]
    if broken:
        raise KeyError(f"{name!r} could not be imported — " + "; ".join(broken))
    if scan.failures:
        raise KeyError(f"no dataset {name!r}; {len(scan.failures)} module(s) could not be imported")
    raise KeyError(f"no dataset {name!r} under {root or config.source()}")


def layers(pool: list[Dataset]) -> dict[str, int]:
    """How deep each dataset sits: sources are 0, everything else follows.

    Computed over the pool given rather than by walking refs from one target,
    so the whole depot lines up on the same scale — which is what a graph view
    needs. A dataset whose refs are outside the pool counts them as sources.
    """
    depth: dict[str, int] = {}
    known = {d.key for d in pool}

    def of(d: Dataset, seen: frozenset) -> int:
        if d.key in depth:
            return depth[d.key]
        if d.key in seen:  # a cycle: stop rather than recurse forever
            return 0
        inner = seen | {d.key}
        value = max((of(r, inner) + 1 for r in d.refs if r.key in known), default=0)
        depth[d.key] = value
        return value

    for d in pool:
        of(d, frozenset())
    return depth


def dependants(pool: list[Dataset]) -> dict[str, int]:
    """How many datasets in the pool name each one as a ref."""
    counts = {d.key: 0 for d in pool}
    for d in pool:
        for ref in d.refs:
            if ref.key in counts:
                counts[ref.key] += 1
    return counts
