"""A factory inside the package — stands in for depot's future templates."""
from depot.dataset import Dataset


def make() -> Dataset:
    return Dataset()
