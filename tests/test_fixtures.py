"""Tests for the labeled-projects fixture used by later evaluation (Phase 1 / Step 2).

The fixture is governance data: it must load, validate, and stay free of URLs,
screenshots and personal names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "labeled_projects.yaml"


class LabeledProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    platform: Literal["mostaql", "khamsat"]
    source_kind: Literal["website_page"]
    category: str
    budget: str | None = None
    duration: str | None = None
    deliverable_type: str
    comments_count: int | None = None
    label: Literal["accept", "reject", "reject_policy"]
    reason: Literal["pending_client"]
    split: Literal["dev", "holdout"]


class LabeledDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    projects: list[LabeledProject]

    @model_validator(mode="after")
    def _check_ids_unique(self) -> LabeledDataset:
        ids = [project.id for project in self.projects]
        if len(set(ids)) != len(ids):
            raise ValueError(f"fixture ids must be unique, got {ids}")
        return self


@pytest.fixture(scope="module")
def dataset() -> LabeledDataset:
    raw = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    return LabeledDataset.model_validate(raw)


def _by_label(dataset: LabeledDataset, label: str) -> list[LabeledProject]:
    return [project for project in dataset.projects if project.label == label]


def test_fixture_file_exists() -> None:
    assert FIXTURE_PATH.is_file()


def test_dataset_loads_and_validates(dataset: LabeledDataset) -> None:
    assert dataset.version == 1
    assert len(dataset.projects) == 17


def test_label_composition(dataset: LabeledDataset) -> None:
    assert len(_by_label(dataset, "accept")) == 13
    assert len(_by_label(dataset, "reject")) == 3
    assert len(_by_label(dataset, "reject_policy")) == 1


def test_split_is_about_70_30(dataset: LabeledDataset) -> None:
    dev = [project for project in dataset.projects if project.split == "dev"]
    holdout = [project for project in dataset.projects if project.split == "holdout"]
    assert len(dev) == 12
    assert len(holdout) == 5
    assert 0.65 <= len(dev) / len(dataset.projects) <= 0.75


def test_every_entry_is_a_website_page_not_an_alert_email(dataset: LabeledDataset) -> None:
    assert all(project.source_kind == "website_page" for project in dataset.projects)


def test_every_label_is_pending_client(dataset: LabeledDataset) -> None:
    assert all(project.reason == "pending_client" for project in dataset.projects)


def test_accepted_platform_distribution(dataset: LabeledDataset) -> None:
    accepts = _by_label(dataset, "accept")
    assert sum(project.platform == "khamsat" for project in accepts) == 5
    assert sum(project.platform == "mostaql" for project in accepts) == 8


def test_no_urls_in_fixture_text() -> None:
    lowered = FIXTURE_PATH.read_text(encoding="utf-8").lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "www." not in lowered


def test_unknown_label_rejected() -> None:
    with pytest.raises(ValidationError):
        LabeledDataset.model_validate(
            {
                "version": 1,
                "projects": [
                    {
                        "id": "x",
                        "platform": "mostaql",
                        "source_kind": "website_page",
                        "category": "c",
                        "deliverable_type": "d",
                        "label": "maybe",
                        "reason": "pending_client",
                        "split": "dev",
                    }
                ],
            }
        )


def test_non_website_source_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        LabeledProject.model_validate(
            {
                "id": "x",
                "platform": "khamsat",
                "source_kind": "alert_email",
                "category": "c",
                "deliverable_type": "d",
                "label": "accept",
                "reason": "pending_client",
                "split": "holdout",
            }
        )
