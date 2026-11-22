"""Smoke tests for the Resova package skeleton and versioning.

These tests assert packaging and module import hygiene.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Packages that are part of Resova
PACKAGES = [
    "resova.config",
    "resova.db",
    "resova.utils",
    "resova.sources",
    "resova.api",
    "resova.services",
    "resova.ai",
    "resova.telegram",
    "resova.scheduler",
]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_resova_is_importable() -> None:
    package = importlib.import_module("resova")
    assert package.__version__ == "0.1.0"


def test_version_matches_pyproject() -> None:
    expected = _pyproject()["project"]["version"]
    assert importlib.import_module("resova").__version__ == expected


def test_python_requirement_is_3_12_or_newer() -> None:
    assert _pyproject()["project"]["requires-python"] == ">=3.12"


@pytest.mark.parametrize("name", PACKAGES)
def test_all_packages_importable(name: str) -> None:
    module = importlib.import_module(name)
    assert module is not None
