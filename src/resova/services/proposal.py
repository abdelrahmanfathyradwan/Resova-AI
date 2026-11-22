"""Proposal draft generation service (Phase 6).

Generates professional, editable proposal drafts based on the project description
and client profile. Never fabricates experience or credentials. Drafts are saved
to the database and are NEVER sent automatically.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from resova.config.profile import ClientProfile, load_profile
from resova.config.settings import Settings
from resova.db.base import utcnow
from resova.db.models import DraftStatus, Project, ProposalDraft

logger = logging.getLogger(__name__)

PROPOSAL_SYSTEM_PROMPT = """\
You are a professional freelance proposal writer. You write clear, compelling proposals
for freelance projects.

Rules:
- Write in the same language as the project description (Arabic or English).
- Only mention skills the freelancer ACTUALLY has (provided in their profile).
- NEVER invent past projects, certifications, or experience not in the profile.
- If information is missing, leave a placeholder like [INSERT DETAILS] instead of fabricating.
- Be professional, concise, and directly relevant to the project requirements.
- The proposal should be ready to review and send with minimal editing.
- Do NOT include pricing unless the project specifies a budget.
"""


def _build_proposal_prompt(
    title: str,
    description: str | None,
    platform: str,
    profile: ClientProfile,
    language: str = "ar",
) -> str:
    """Build the user prompt for proposal generation."""
    skills = []
    for track in profile.tracks:
        confirmed = track.match_keywords
        if confirmed:
            skills.extend(confirmed[:5])

    confirmed_extra = profile.confirmed_skills
    if confirmed_extra:
        skills.extend(confirmed_extra)

    titles = ", ".join(profile.titles) if profile.titles else "Freelance Professional"

    if language == "ar":
        return f"""\
اكتب مسودة عرض احترافي لهذا المشروع:

**عنوان المشروع:** {title}
**وصف المشروع:** {description or "غير متوفر"}
**المنصة:** {platform}

**ملف المستقل:**
- المسميات الوظيفية: {titles}
- المهارات المؤكدة: {", ".join(skills) if skills else "غير محدد"}

اكتب العرض باللغة العربية. لا تخترع مشاريع سابقة أو شهادات أو خبرات غير مذكورة في الملف.
إذا كانت هناك معلومات ناقصة، ضع [أدخل التفاصيل] بدلاً من اختلاقها.
"""
    else:
        return f"""\
Write a professional proposal draft for this project:

**Project Title:** {title}
**Project Description:** {description or "not provided"}
**Platform:** {platform}

**Freelancer Profile:**
- Job Titles: {titles}
- Confirmed Skills: {", ".join(skills) if skills else "not specified"}

Write the proposal in English. Do NOT invent past projects, certifications, or experience
not listed in the profile. Use [INSERT DETAILS] for missing information.
"""


class ProposalService:
    """Generate and manage proposal drafts.

    Drafts are NEVER sent automatically. They are saved to the database for
    the human to review, edit, and manually submit.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._profile: ClientProfile | None = None

    @property
    def profile(self) -> ClientProfile:
        if self._profile is None:
            self._profile = load_profile(self._settings.profile_path)
        return self._profile

    def generate_draft(
        self,
        project: Project,
        language: str = "ar",
    ) -> ProposalDraft:
        """Generate a proposal draft for a project.

        Uses AI if available, otherwise creates a template-based draft.
        The draft is saved to the database with status ``draft``.

        Returns the saved ``ProposalDraft`` ORM instance.
        """
        # Check for existing drafts
        existing = (
            self._session.query(ProposalDraft)
            .filter_by(project_id=project.id, status=DraftStatus.draft)
            .first()
        )
        if existing:
            logger.info(
                "Draft already exists for project %d (draft id %d); returning existing.",
                project.id,
                existing.id,
            )
            return existing

        # Try AI-generated draft
        content = self._generate_with_ai(project, language)
        if content is None:
            content = self._generate_template(project, language)

        # Determine version number
        version = self._session.query(ProposalDraft).filter_by(project_id=project.id).count() + 1

        draft = ProposalDraft(
            project_id=project.id,
            content=content,
            language=language,
            status=DraftStatus.draft,
            version=version,
            generated_at=utcnow(),
        )
        self._session.add(draft)
        self._session.flush()

        logger.info("Generated proposal draft %d for project %d", draft.id, project.id)
        return draft

    def _generate_with_ai(self, project: Project, language: str) -> str | None:
        """Try to generate a draft using the OpenAI API."""
        if not self._settings.enable_ai_analysis:
            return None

        api_key = self._settings.openai_api_key
        if api_key is None:
            return None

        try:
            import openai

            client = openai.OpenAI(api_key=api_key.get_secret_value())
            prompt = _build_proposal_prompt(
                title=project.title,
                description=project.description,
                platform=project.platform.value,
                profile=self.profile,
                language=language,
            )
            response = client.chat.completions.create(
                model=self._settings.openai_model,
                messages=[
                    {"role": "system", "content": PROPOSAL_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=1500,
            )
            return response.choices[0].message.content or self._generate_template(project, language)
        except ImportError:
            logger.warning("openai package not installed; falling back to template draft.")
            return None
        except Exception as exc:
            logger.warning("AI proposal generation failed: %s; falling back to template.", exc)
            return None

    def _generate_template(self, project: Project, language: str) -> str:
        """Generate a template-based proposal draft (no AI required)."""
        profile = self.profile
        titles = ", ".join(profile.titles) if profile.titles else "[INSERT TITLE]"

        skills = []
        for track in profile.tracks:
            confirmed = track.match_keywords
            if confirmed:
                skills.extend(confirmed[:3])

        skills_text = ", ".join(skills[:6]) if skills else "[INSERT SKILLS]"

        if language == "ar":
            intro = f'بعد الاطلاع على تفاصيل مشروعكم "{project.title}"،'
            return f"""\
السلام عليكم،

أنا {titles}، ولدي خبرة في {skills_text}.

{intro}
أعتقد أنني أستطيع تقديم عمل احترافي يلبي متطلباتكم.

[أدخل تفاصيل إضافية عن كيفية تنفيذ المشروع]

[أدخل الجدول الزمني المقترح]

أرجو التواصل لمناقشة التفاصيل.

مع أطيب التحيات
"""
        else:
            return f"""\
Hello,

I am a {titles} with experience in {skills_text}.

After reviewing your project "{project.title}", I believe I can deliver professional
work that meets your requirements.

[INSERT additional details about your approach]

[INSERT proposed timeline]

I look forward to discussing the details.

Best regards
"""

    def get_drafts(self, project_id: int) -> list[ProposalDraft]:
        """Return all drafts for a given project."""
        return (
            self._session.query(ProposalDraft)
            .filter_by(project_id=project_id)
            .order_by(ProposalDraft.version.desc())
            .all()
        )

    def update_draft(self, draft_id: int, content: str) -> ProposalDraft:
        """Update the content of an existing draft."""
        draft = self._session.get(ProposalDraft, draft_id)
        if draft is None:
            raise ValueError(f"Draft {draft_id} not found")
        if draft.status != DraftStatus.draft:
            raise ValueError(f"Cannot edit draft {draft_id}: status is {draft.status}")
        draft.content = content
        self._session.flush()
        return draft

    def approve_draft(self, draft_id: int) -> ProposalDraft:
        """Mark a draft as approved (ready to submit manually)."""
        draft = self._session.get(ProposalDraft, draft_id)
        if draft is None:
            raise ValueError(f"Draft {draft_id} not found")
        draft.status = DraftStatus.approved
        self._session.flush()
        return draft

    def discard_draft(self, draft_id: int) -> ProposalDraft:
        """Discard a draft."""
        draft = self._session.get(ProposalDraft, draft_id)
        if draft is None:
            raise ValueError(f"Draft {draft_id} not found")
        draft.status = DraftStatus.discarded
        self._session.flush()
        return draft
