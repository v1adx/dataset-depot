"""One way to say that something blew up.

`str(exc)` is what every failure notification used to show, and for the
exceptions a pipeline actually raises that is next to nothing:
`str(KeyError("Name"))` is `'Name'` — no type, no place, and because the
exception was caught, no traceback printed anywhere either.
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

log = logging.getLogger("depot_gui")


def describe(exc: BaseException, context: str = "") -> str:
    """`KeyError: 'Name' — in transform at sales.py:42`.

    Also puts the whole traceback on the console, which is the only place a
    caught exception can still be read in full.

    The frame named is the deepest one outside site-packages: a bad column
    raises inside pandas, and pointing at `pandas/core/indexes/base.py` would
    be worse than pointing nowhere. Falls back to the true last frame when
    everything is library code.
    """
    log.error("%s", context or type(exc).__name__, exc_info=exc)

    text = f"{type(exc).__name__}: {exc}"
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "site-packages" not in f.filename] or frames
    if ours:
        f = ours[-1]
        text += f" — in {f.name} at {Path(f.filename).name}:{f.lineno}"
    return text
