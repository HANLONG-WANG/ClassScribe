from __future__ import annotations

from scripts.check_architecture import check


def test_repository_structure_and_dependency_boundaries() -> None:
    assert check() == []
