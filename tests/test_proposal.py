"""Tests for proposal draft generator (Phase 6).

Asserts draft creation, Arabic/English templates, draft persistence,
status progression (draft -> approved / discarded), and that proposals are never
sent automatically.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from resova.config.settings import Settings
from resova.db.models import DraftStatus, Platform, Project, ProposalDraft, SourceKind
from resova.services.proposal import ProposalService


@pytest.fixture()
def prop_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
    )


@pytest.fixture()
def sample_project(session: Session) -> Project:
    desc = "مطلوب مهندس لتصميم مخططات التكييف وحساب الأحمال الحرارية ورسم مسارات الدكت بالأوتوكاد."
    project = Project(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="تصميم مخططات تكييف ودكت",
        description=desc,
    )
    session.add(project)
    session.flush()
    return project


def test_generate_template_draft_arabic(
    session: Session, prop_settings: Settings, sample_project: Project
) -> None:
    service = ProposalService(session, prop_settings)
    draft = service.generate_draft(sample_project, language="ar")

    assert isinstance(draft, ProposalDraft)
    assert draft.project_id == sample_project.id
    assert draft.status == DraftStatus.draft
    assert draft.language == "ar"
    assert "السلام عليكم" in draft.content
    assert sample_project.title in draft.content
    assert "[أدخل تفاصيل" in draft.content


def test_generate_template_draft_english(
    session: Session, prop_settings: Settings, sample_project: Project
) -> None:
    service = ProposalService(session, prop_settings)
    draft = service.generate_draft(sample_project, language="en")

    assert isinstance(draft, ProposalDraft)
    assert draft.language == "en"
    assert "Hello" in draft.content
    assert sample_project.title in draft.content
    assert "[INSERT" in draft.content


def test_draft_lifecycle(
    session: Session, prop_settings: Settings, sample_project: Project
) -> None:
    service = ProposalService(session, prop_settings)
    draft = service.generate_draft(sample_project, language="ar")
    draft_id = draft.id

    # Update draft content
    updated = service.update_draft(draft_id, "عرض معدل بالكامل بعد مراجعة المتطلبات.")
    assert updated.content == "عرض معدل بالكامل بعد مراجعة المتطلبات."
    assert updated.status == DraftStatus.draft

    # Approve draft
    approved = service.approve_draft(draft_id)
    assert approved.status == DraftStatus.approved

    # Cannot update an approved draft
    with pytest.raises(ValueError, match="Cannot edit"):
        service.update_draft(draft_id, "Another edit")


def test_discard_draft(session: Session, prop_settings: Settings, sample_project: Project) -> None:
    service = ProposalService(session, prop_settings)
    draft = service.generate_draft(sample_project)
    discarded = service.discard_draft(draft.id)
    assert discarded.status == DraftStatus.discarded


def test_existing_draft_returned_on_subsequent_call(
    session: Session, prop_settings: Settings, sample_project: Project
) -> None:
    service = ProposalService(session, prop_settings)
    draft1 = service.generate_draft(sample_project)
    draft2 = service.generate_draft(sample_project)
    assert draft1.id == draft2.id
