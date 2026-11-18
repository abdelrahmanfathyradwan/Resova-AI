"""Smoke tests for the Phase 1 / Step 2.1 skeleton.

These tests assert packaging and stub hygiene only. No feature behaviour exists yet,
so there is nothing else to test at this step.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Packages that must exist now but are only implemented in a later step.
# NOTE: resova.config (Steps 2.2 / 2.3) and resova.db (Step 2.4) are implemented and
# covered by their own tests, so they are no longer listed here.
STUB_PACKAGES = [
    "resova.sources",
    "resova.utils",
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


@pytest.mark.parametrize("name", STUB_PACKAGES)
def test_stub_package_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


@pytest.mark.parametrize("name", STUB_PACKAGES)
def test_stub_package_is_marked_not_implemented(name: str) -> None:
    docstring = importlib.import_module(name).__doc__ or ""
    assert "NOT IMPLEMENTED" in docstring
