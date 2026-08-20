"""Ready-made datasets: the ones that come from files, and the one that
describes the depot itself.

They are classes rather than factories so a project can specialise them the
way it specialises anything else — subclass, add a field, override a phase.
Their configuration lives in typed fields instead of the untyped props bag,
and the index can report what a dataset is by its class name.
"""
from .files import DatasetFileBacked, DatasetFromFile, DatasetFromLastFile, DatasetFromSetOfFiles, DatasetListOfFiles
from .index import DatasetIndex

__all__ = [
    "DatasetFileBacked",
    "DatasetFromFile",
    "DatasetFromLastFile",
    "DatasetFromSetOfFiles",
    "DatasetListOfFiles",
    "DatasetIndex",
]
