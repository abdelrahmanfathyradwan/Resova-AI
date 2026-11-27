"""AI Technical Proposal Generator service.

Generates, edits, regenerates, duplicates, and persists structured 9-section
technical proposals tied to a specific project and owner user.
Never fabricates credentials, past projects, or pricing, and never sends
proposals automatically.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from resova.config.profile import ClientProfile, load_profile
from resova.config.settings import Settings
from resova.db.base import utcnow
from resova.db.models import (
    DraftStatus,
    Project,
    ProjectAnalysis,
    ProposalDraft,
    ProposalEditorStatus,
    UserProfessionalProfile,
)
from resova.utils.errors import AIError

logger = logging.getLogger(__name__)

SECTION_KEYS: tuple[str, ...] = (
    "intro",
    "understanding",
    "proposed_solution",
    "scope_of_work",
    "methodology",
    "deliverables",
    "timeline",
    "cost",
    "closing",
)

SECTION_LABELS_AR: dict[str, str] = {
    "intro": "1. المقدمة",
    "understanding": "2. فهم المشروع",
    "proposed_solution": "3. الحل المقترح",
    "scope_of_work": "4. نطاق العمل",
    "methodology": "5. منهجية التنفيذ",
    "deliverables": "6. التسليمات النهائية",
    "timeline": "7. المدة الزمنية",
    "cost": "8. التكلفة",
    "closing": "9. الخاتمة",
}

DEFAULT_COST_PLACEHOLDER = "[يحددها المستخدم يدويًا]"

TECHNICAL_PROPOSAL_SYSTEM_PROMPT = """\
You are a senior technical proposal writer for freelance engineering, architecture,
interior design, presentation design, and CV/resume projects.

Strict Rules:
1. Read and deeply understand the project title, description, and analysis.
   NEVER copy-paste the project description verbatim.
2. Tailor the proposal specifically to the project domain (engineering/MEP/fire,
   architecture/interior/3D, presentations/pitch decks, CV/ATS, or general).
3. Use ONLY verified details provided in the User Professional Profile and
   confirmed Client Profile skills. NEVER invent past projects, years of experience,
   certifications, client names, or tools that were not explicitly provided.
4. If personal details (name, portfolio, tools) are missing, use clean general
   professional wording or editable placeholders like [الاسم المهني] without fabricating.
5. NEVER invent a price or budget! Set "cost" to "[يحددها المستخدم يدويًا]".
6. For "timeline", provide ONLY an editable preliminary estimate phrased clearly
   as non-binding (e.g., "مدة تقديرية مبدئية قابلة للتعديل حسب مراجعة التفاصيل...").
7. Avoid exaggeration, unrealistic guarantees, or adding out-of-scope services.
8. Write in clear, natural, professional Arabic (technical English terms like
   HVAC, MEP, AutoCAD, Revit, Pitch Deck, ATS may be included where appropriate).
9. Respond with ONLY valid JSON containing these exact 9 string keys:
   intro, understanding, proposed_solution, scope_of_work, methodology,
   deliverables, timeline, cost, closing.
"""


class ProposalSections(BaseModel):
    """Structured 9-section technical proposal payload."""

    model_config = ConfigDict(extra="ignore")

    intro: str = Field(default="", max_length=8000)
    understanding: str = Field(default="", max_length=8000)
    proposed_solution: str = Field(default="", max_length=8000)
    scope_of_work: str = Field(default="", max_length=8000)
    methodology: str = Field(default="", max_length=8000)
    deliverables: str = Field(default="", max_length=8000)
    timeline: str = Field(default="", max_length=2000)
    cost: str = Field(default="", max_length=1000)
    closing: str = Field(default="", max_length=4000)

    def to_dict(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in SECTION_KEYS}

    def to_markdown(self) -> str:
        parts: list[str] = []
        for key in SECTION_KEYS:
            val = getattr(self, key).strip()
            label = SECTION_LABELS_AR[key]
            parts.append(f"## {label}\n{val if val else '—'}")
        return "\n\n".join(parts)


class UserProfessionalProfileData(BaseModel):
    """Editable user professional metadata (all optional, never fabricated)."""

    model_config = ConfigDict(extra="ignore")

    professional_name: str | None = Field(default=None, max_length=255)
    specialization: str | None = Field(default=None, max_length=4000)
    skills_services: str | None = Field(default=None, max_length=4000)
    portfolio_samples: str | None = Field(default=None, max_length=4000)
    tools_software: str | None = Field(default=None, max_length=4000)
    communication_style: str | None = Field(default=None, max_length=255)


def _clean_opt(val: str | None) -> str | None:
    if val is None:
        return None
    stripped = val.strip()
    return stripped if stripped else None


def _build_user_profile_context(prof: UserProfessionalProfile | None) -> str:
    if prof is None:
        return "لم يضف المستخدم بيانات ملف مهني خاصة بعد (لا تخترع أي معلومات شخصية أو خبرات)."
    lines: list[str] = []
    if prof.professional_name:
        lines.append(f"- الاسم المهني: {prof.professional_name}")
    if prof.specialization:
        lines.append(f"- التخصص والخبرات: {prof.specialization}")
    if prof.skills_services:
        lines.append(f"- المهارات والخدمات: {prof.skills_services}")
    if prof.tools_software:
        lines.append(f"- البرامج والأدوات: {prof.tools_software}")
    if prof.portfolio_samples:
        lines.append(f"- نماذج الأعمال المتاحة: {prof.portfolio_samples}")
    if prof.communication_style:
        lines.append(f"- أسلوب التواصل المفضل: {prof.communication_style}")
    if not lines:
        return "بيانات الملف المهني فارغة حاليًا (لا تخترع أي معلومات شخصية أو خبرات)."
    return "\n".join(lines)


def _detect_domain_context(
    project: Project,
    analysis: ProjectAnalysis | None,
) -> str:
    if analysis and analysis.track:
        t = analysis.track.lower()
        if t in ("engineering", "architectural_interior", "presentation_design", "cv_resume"):
            return t
        if "engineer" in t or "mep" in t:
            return "engineering"
        if "arch" in t or "interior" in t:
            return "architectural_interior"
        if "present" in t or "deck" in t:
            return "presentation_design"
        if "cv" in t or "resume" in t:
            return "cv_resume"
    text = f"{project.title} {project.description or ''}".lower()
    if any(k in text for k in ("حريق", "hvac", "mep", "تكييف", "شوب دروينج", "ميكانيك", "boq")):
        return "engineering"
    if any(k in text for k in ("معماري", "داخلي", "ديكور", "3d", "رندر", "فيلا", "مطابخ")):
        return "architectural_interior"
    if any(k in text for k in ("عرض تقديمي", "بوربوينت", "pitch deck", "بروفايل", "سلايدات")):
        return "presentation_design"
    if any(k in text for k in ("سيرة ذاتية", "ats", "cv", "resume", "linkedin", "europass")):
        return "cv_resume"
    return "general"


class TechnicalProposalService:
    """Service for AI-powered 9-section technical proposals with strict ownership."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._profile: ClientProfile | None = None

    @property
    def client_profile(self) -> ClientProfile:
        if self._profile is None:
            self._profile = load_profile(self._settings.profile_path)
        return self._profile

    # ------------------------------------------------------------------
    # User Professional Profile Management
    # ------------------------------------------------------------------

    def get_professional_profile(self, user_id: int) -> UserProfessionalProfile | None:
        return (
            self._session.query(UserProfessionalProfile)
            .filter_by(user_id=user_id)
            .first()
        )

    def upsert_professional_profile(
        self, user_id: int, data: UserProfessionalProfileData
    ) -> UserProfessionalProfile:
        prof = self.get_professional_profile(user_id)
        if prof is None:
            prof = UserProfessionalProfile(user_id=user_id)
            self._session.add(prof)
        prof.professional_name = _clean_opt(data.professional_name)
        prof.specialization = _clean_opt(data.specialization)
        prof.skills_services = _clean_opt(data.skills_services)
        prof.portfolio_samples = _clean_opt(data.portfolio_samples)
        prof.tools_software = _clean_opt(data.tools_software)
        prof.communication_style = _clean_opt(data.communication_style)
        self._session.flush()
        return prof

    # ------------------------------------------------------------------
    # Ownership & Retrieval
    # ------------------------------------------------------------------

    def get_proposal(self, draft_id: int, owner_user_id: int) -> ProposalDraft:
        draft = self._session.query(ProposalDraft).filter_by(id=draft_id).first()
        if draft is None:
            raise ValueError(f"Proposal draft {draft_id} not found")
        if draft.owner_user_id != owner_user_id:
            raise PermissionError("You do not have access to this proposal draft")
        return draft

    def list_project_proposals(
        self, project_id: int, owner_user_id: int
    ) -> list[ProposalDraft]:
        return (
            self._session.query(ProposalDraft)
            .filter_by(project_id=project_id, owner_user_id=owner_user_id)
            .order_by(ProposalDraft.version.desc(), ProposalDraft.id.desc())
            .all()
        )

    def parse_sections(self, draft: ProposalDraft) -> ProposalSections:
        if draft.sections_json:
            try:
                raw = json.loads(draft.sections_json)
                if isinstance(raw, dict):
                    return ProposalSections.model_validate(raw)
            except Exception:
                logger.warning("Failed to parse sections_json for draft %d", draft.id)
        return ProposalSections(
            intro=draft.content or "",
            timeline=draft.timeline_text or "",
            cost=draft.cost_text or "",
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_technical_proposal(
        self,
        project_id: int,
        owner_user_id: int,
        *,
        require_ai: bool = False,
        language: str = "ar",
    ) -> ProposalDraft:
        """Create a new structured 9-section proposal draft for a project."""
        project = self._session.query(Project).filter_by(id=project_id).first()
        if project is None:
            raise ValueError(f"Project {project_id} not found")

        analysis = (
            self._session.query(ProjectAnalysis)
            .filter_by(project_id=project.id)
            .order_by(ProjectAnalysis.analyzed_at.desc(), ProjectAnalysis.id.desc())
            .first()
        )
        prof = self.get_professional_profile(owner_user_id)

        sections, is_ai, model_name = self._produce_sections(
            project=project,
            analysis=analysis,
            prof=prof,
            require_ai=require_ai,
        )

        # Enforce strict cost & timeline rules regardless of AI output
        sections.cost = ""
        if not sections.timeline.strip():
            sections.timeline = (
                "مدة تقديرية مبدئية قابلة للتعديل بعد مراجعة كافة المدخلات والتفاصيل."
            )

        version = (
            self._session.query(ProposalDraft)
            .filter_by(project_id=project.id, owner_user_id=owner_user_id)
            .count()
            + 1
        )

        draft = ProposalDraft(
            project_id=project.id,
            owner_user_id=owner_user_id,
            title=f"عرض فني: {project.title}",
            content=sections.to_markdown(),
            sections_json=json.dumps(sections.to_dict(), ensure_ascii=False),
            timeline_text=sections.timeline,
            cost_text="",
            language=language,
            status=DraftStatus.draft,
            editor_status=ProposalEditorStatus.draft,
            is_ai_generated=is_ai,
            ai_model=model_name,
            version=version,
            generated_at=utcnow(),
        )
        self._session.add(draft)
        self._session.flush()
        return draft

    def regenerate_full(
        self,
        draft_id: int,
        owner_user_id: int,
        *,
        require_ai: bool = False,
    ) -> ProposalDraft:
        """Regenerate all sections of an existing proposal draft."""
        draft = self.get_proposal(draft_id, owner_user_id)
        project = self._session.query(Project).filter_by(id=draft.project_id).first()
        if project is None:
            raise ValueError(f"Project {draft.project_id} not found")

        analysis = (
            self._session.query(ProjectAnalysis)
            .filter_by(project_id=project.id)
            .order_by(ProjectAnalysis.analyzed_at.desc(), ProjectAnalysis.id.desc())
            .first()
        )
        prof = self.get_professional_profile(owner_user_id)

        sections, is_ai, model_name = self._produce_sections(
            project=project,
            analysis=analysis,
            prof=prof,
            require_ai=require_ai,
        )
        # Preserve user-entered cost if already provided
        sections.cost = draft.cost_text or ""

        draft.sections_json = json.dumps(sections.to_dict(), ensure_ascii=False)
        draft.timeline_text = sections.timeline
        draft.content = sections.to_markdown()
        draft.is_ai_generated = is_ai
        draft.ai_model = model_name
        draft.editor_status = ProposalEditorStatus.edited
        self._session.flush()
        return draft

    def regenerate_section(
        self,
        draft_id: int,
        owner_user_id: int,
        section_key: str,
        *,
        require_ai: bool = False,
    ) -> ProposalDraft:
        """Regenerate a single section while preserving all other sections."""
        if section_key not in SECTION_KEYS:
            raise ValueError(f"Invalid section key: {section_key}")

        draft = self.get_proposal(draft_id, owner_user_id)
        project = self._session.query(Project).filter_by(id=draft.project_id).first()
        if project is None:
            raise ValueError(f"Project {draft.project_id} not found")

        sections = self.parse_sections(draft)

        if section_key == "cost":
            # Never invent pricing via AI
            sections.cost = draft.cost_text or ""
            draft.sections_json = json.dumps(sections.to_dict(), ensure_ascii=False)
            draft.content = sections.to_markdown()
            self._session.flush()
            return draft

        analysis = (
            self._session.query(ProjectAnalysis)
            .filter_by(project_id=project.id)
            .order_by(ProjectAnalysis.analyzed_at.desc(), ProjectAnalysis.id.desc())
            .first()
        )
        prof = self.get_professional_profile(owner_user_id)

        new_text = self._produce_single_section(
            project=project,
            analysis=analysis,
            prof=prof,
            existing=sections,
            section_key=section_key,
            require_ai=require_ai,
        )
        setattr(sections, section_key, new_text)
        if section_key == "timeline":
            draft.timeline_text = new_text

        draft.sections_json = json.dumps(sections.to_dict(), ensure_ascii=False)
        draft.content = sections.to_markdown()
        draft.editor_status = ProposalEditorStatus.edited
        self._session.flush()
        return draft

    # ------------------------------------------------------------------
    # Editing, Duplication, Deletion
    # ------------------------------------------------------------------

    def update_proposal(
        self,
        draft_id: int,
        owner_user_id: int,
        sections: ProposalSections,
        *,
        title: str | None = None,
        timeline_text: str | None = None,
        cost_text: str | None = None,
        editor_status: ProposalEditorStatus | None = None,
    ) -> ProposalDraft:
        draft = self.get_proposal(draft_id, owner_user_id)

        if timeline_text is not None:
            sections.timeline = timeline_text.strip()
        if cost_text is not None:
            sections.cost = cost_text.strip()

        if title is not None and title.strip():
            draft.title = title.strip()
        draft.timeline_text = sections.timeline
        draft.cost_text = sections.cost
        draft.sections_json = json.dumps(sections.to_dict(), ensure_ascii=False)
        draft.content = sections.to_markdown()

        if editor_status is not None:
            draft.editor_status = editor_status
        elif draft.editor_status == ProposalEditorStatus.draft:
            draft.editor_status = ProposalEditorStatus.edited

        self._session.flush()
        return draft

    def duplicate_proposal(self, draft_id: int, owner_user_id: int) -> ProposalDraft:
        source = self.get_proposal(draft_id, owner_user_id)
        version = (
            self._session.query(ProposalDraft)
            .filter_by(project_id=source.project_id, owner_user_id=owner_user_id)
            .count()
            + 1
        )
        copy_draft = ProposalDraft(
            project_id=source.project_id,
            owner_user_id=owner_user_id,
            title=f"{source.title or 'عرض فني'} (نسخة {version})",
            content=source.content,
            sections_json=source.sections_json,
            timeline_text=source.timeline_text,
            cost_text=source.cost_text,
            language=source.language,
            status=DraftStatus.draft,
            editor_status=ProposalEditorStatus.draft,
            is_ai_generated=source.is_ai_generated,
            ai_model=source.ai_model,
            version=version,
            generated_at=utcnow(),
        )
        self._session.add(copy_draft)
        self._session.flush()
        return copy_draft

    def delete_proposal(self, draft_id: int, owner_user_id: int) -> None:
        draft = self.get_proposal(draft_id, owner_user_id)
        self._session.delete(draft)
        self._session.flush()

    # ------------------------------------------------------------------
    # Internal AI & Structured Fallback Builders
    # ------------------------------------------------------------------

    def _produce_sections(
        self,
        *,
        project: Project,
        analysis: ProjectAnalysis | None,
        prof: UserProfessionalProfile | None,
        require_ai: bool,
    ) -> tuple[ProposalSections, bool, str]:
        ai_enabled = self._settings.enable_ai_analysis and self._settings.openai_api_key is not None
        if ai_enabled:
            try:
                sections = self._call_openai_full(project, analysis, prof)
                return sections, True, self._settings.openai_model
            except AIError:
                if require_ai:
                    raise
                logger.warning("AI proposal generation failed; using structured fallback.")
        elif require_ai:
            raise AIError("خدمة الذكاء الاصطناعي غير مفعلة أو مفتاح API غير متوفر.")

        return self._build_fallback_sections(project, analysis, prof), False, "structured-fallback"

    def _produce_single_section(
        self,
        *,
        project: Project,
        analysis: ProjectAnalysis | None,
        prof: UserProfessionalProfile | None,
        existing: ProposalSections,
        section_key: str,
        require_ai: bool,
    ) -> str:
        ai_enabled = self._settings.enable_ai_analysis and self._settings.openai_api_key is not None
        if ai_enabled:
            try:
                return self._call_openai_section(project, analysis, prof, existing, section_key)
            except AIError:
                if require_ai:
                    raise
                logger.warning("AI section regeneration failed; using fallback.")
        elif require_ai:
            raise AIError("خدمة الذكاء الاصطناعي غير مفعلة أو مفتاح API غير متوفر.")

        fallback = self._build_fallback_sections(project, analysis, prof)
        return getattr(fallback, section_key)

    def _build_full_prompt(
        self,
        project: Project,
        analysis: ProjectAnalysis | None,
        prof: UserProfessionalProfile | None,
    ) -> str:
        analysis_block = "غير متوفر"
        if analysis is not None:
            analysis_block = (
                f"- المسار التخصصي: {analysis.track or 'غير محدد'}\n"
                f"- درجة التطابق: {analysis.score}%\n"
                f"- ملخص التحليل: {analysis.summary or 'غير متوفر'}\n"
                f"- سبب التقييم: {analysis.rationale or 'غير متوفر'}"
            )

        default_desc = (
            "لم يُذكر وصف تفصيلي، اعتمد على العنوان بوضوح دون افتراضات مبالغ فيها."
        )
        return (
            f"أنشئ مسودة عرض فني احترافي ومخصص لهذا المشروع:\n\n"
            f"**عنوان المشروع:** {project.title}\n"
            f"**المنصة:** {project.platform.value}\n"
            f"**التصنيف:** {project.category or 'غير محدد'}\n"
            f"**المدة المذكورة من العميل:** {project.duration or 'غير محددة'}\n"
            f"**وصف المشروع:**\n{project.description or default_desc}\n\n"
            f"**نتائج التحليل الحالية:**\n{analysis_block}\n\n"
            f"**بيانات المستخدم المهنية المتاحة:**\n{_build_user_profile_context(prof)}\n"
        )

    def _call_openai_full(
        self,
        project: Project,
        analysis: ProjectAnalysis | None,
        prof: UserProfessionalProfile | None,
    ) -> ProposalSections:
        api_key = self._settings.openai_api_key
        if api_key is None:
            raise AIError("مفتاح OpenAI غير مهيأ.")

        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(api_key=api_key.get_secret_value())
            response = client.chat.completions.create(
                model=self._settings.openai_model,
                messages=[
                    {"role": "system", "content": TECHNICAL_PROPOSAL_SYSTEM_PROMPT},
                    {"role": "user", "content": self._build_full_prompt(project, analysis, prof)},
                ],
                temperature=0.4,
                max_tokens=2200,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            parsed: dict[str, Any] = json.loads(content)
            return ProposalSections.model_validate(parsed)
        except Exception as exc:
            raise AIError(f"تعذر توليد العرض الفني عبر الذكاء الاصطناعي: {exc}") from exc

    def _call_openai_section(
        self,
        project: Project,
        analysis: ProjectAnalysis | None,
        prof: UserProfessionalProfile | None,
        existing: ProposalSections,
        section_key: str,
    ) -> str:
        api_key = self._settings.openai_api_key
        if api_key is None:
            raise AIError("مفتاح OpenAI غير مهيأ.")

        label = SECTION_LABELS_AR[section_key]
        user_prompt = (
            f"{self._build_full_prompt(project, analysis, prof)}\n"
            f"**الأقسام الحالية للعرض:**\n"
            f"{json.dumps(existing.to_dict(), ensure_ascii=False)}\n\n"
            f"أعد صياغة وتوليد القسم التالي فقط بشكل محسّن ومتوافق مع باقي الأقسام: "
            f'"{section_key}" ({label}).\n'
            f'أرجع JSON يحتوي فقط على المفتاح "{section_key}".'
        )
        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(api_key=api_key.get_secret_value())
            response = client.chat.completions.create(
                model=self._settings.openai_model,
                messages=[
                    {"role": "system", "content": TECHNICAL_PROPOSAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            parsed = json.loads(content)
            text = str(parsed.get(section_key, "")).strip()
            if not text:
                raise AIError("لم يرجع النموذج محتوى للقسم المطلوب.")
            return text
        except AIError:
            raise
        except Exception as exc:
            raise AIError(f"تعذر إعادة توليد القسم ({label}): {exc}") from exc

    def _build_fallback_sections(
        self,
        project: Project,
        analysis: ProjectAnalysis | None,
        prof: UserProfessionalProfile | None,
    ) -> ProposalSections:
        """Build a domain-adapted structured proposal without inventing facts or prices."""
        domain = _detect_domain_context(project, analysis)
        name_part = (
            f"أنا {prof.professional_name}"
            if prof and prof.professional_name
            else "أتشرف بالتقدم لتنفيذ مشروعكم"
        )
        spec_part = (
            f" في مجال {prof.specialization}"
            if prof and prof.specialization
            else ""
        )
        tools_part = (
            f" باستخدام الأدوات المناسبة ({prof.tools_software})"
            if prof and prof.tools_software
            else ""
        )
        portfolio_note = (
            f"\nكما يسعدني مشاركة نماذج أعمال ذات صلة: {prof.portfolio_samples}."
            if prof and prof.portfolio_samples
            else ""
        )

        domain_solutions = {
            "engineering": (
                "إعداد دراسة فنية ومخططات هندسية متكاملة وفق الأصول الهندسية والمعايير المعتمدة، "
                "مع مراجعة التداخلات الفنية وضمان وضوح الرسومات التنفيذية وجداول الكميات."
            ),
            "architectural_interior": (
                "تطوير تصور معماري وداخلي متوازن يراعي توزيع الفراغات والوظيفية والجانب الجمالي، "
                "مع إخراج المخططات واللقطات ثلاثية الأبعاد بدقة عالية."
            ),
            "presentation_design": (
                "تصميم عرض تقديمي منظم بصريًا يبرز الرسالة الأساسية بأسلوب احترافي "
                "وهيكل شرائح متناسق وسهل القراءة والعرض."
            ),
            "cv_resume": (
                "صياغة وتنسيق سيرة ذاتية احترافية متوافقة مع أنظمة الفرز (ATS) "
                "تبرز الخبرات والإنجازات بوضوح وبأسلوب مهني مناسب للجهة المستهدفة."
            ),
            "general": (
                "تنفيذ المخرجات المطلوبة بصورة منظمة ومباشرة بناءً على مراجعة المتطلبات المحددة "
                "وبما يحقق الهدف العملي للمشروع."
            ),
        }

        domain_deliverables = {
            "engineering": (
                "- المخططات والرسومات الهندسية بصيغ قابلة للمراجعة والتعديل (مثل CAD / PDF).\n"
                "- الجداول أو التقارير الفنية المرتبطة بنطاق المشروع."
            ),
            "architectural_interior": (
                "- المخططات المعمارية أو التصميمية المعتمدة.\n"
                "- لقطات الإظهار/الرندر أو الملفات النهائية بصيغ عالية الجودة."
            ),
            "presentation_design": (
                "- ملف العرض التقديمي النهائي قابلًا للتعديل (PowerPoint / PPTX).\n"
                "- نسخة PDF جاهزة للعرض والمشاركة."
            ),
            "cv_resume": (
                "- ملف السيرة الذاتية بصيغة PDF احترافية ونسخة قابلة للتعديل (Word).\n"
                "- أي ملحقات متفق عليها ضمن النطاق (مثل خطاب التغطية أو توصيات LinkedIn)."
            ),
            "general": (
                "- الملفات والمخرجات النهائية المتفق عليها بصيغ واضحة وقابلة للمراجعة."
            ),
        }

        desc_hint = (
            "بناءً على المعطيات الواردة في تفاصيل المشروع"
            if project.description
            else "بناءً على عنوان المشروع المتاح حاليًا (مع إمكانية تأكيد التفاصيل الإضافية معكم)"
        )

        intro = (
            f"السلام عليكم ورحمة الله،\n"
            f"{name_part}{spec_part}، وقد اطلعت باهتمام على مشروعكم «{project.title}»."
        )
        understanding = (
            f"يهدف المشروع {desc_hint} إلى إنجاز مخرجات محددة في «{project.title}» "
            f"بجودة مهنية واضحة وتنفيذ منظم يلبي احتياجكم الفعلي دون تعقيد غير ضروري."
        )
        proposed_solution = (
            f"نقترح {domain_solutions.get(domain, domain_solutions['general'])}"
            f"{tools_part}.{portfolio_note}"
        )
        scope_of_work = (
            "- مراجعة المتطلبات والمدخلات الأولية للمشروع وتحديد الأولويات.\n"
            "- تنفيذ الأعمال الفنية الأساسية المطلوبة ضمن نطاق المشروع المعلن فقط.\n"
            "- إجراء مراجعة نهائية وتنفيذ التعديلات المتفق عليها قبل التسليم."
        )
        methodology = (
            "1. مرحلة الاستلام والمراجعة: تأكيد المتطلبات والملفات المرجعية.\n"
            "2. مرحلة التنفيذ الأولي: إعداد المسودة الأولى ومشاركتها للملاحظات.\n"
            "3. مرحلة الاعتماد والتسليم: استيعاب الملاحظات وتسليم النسخة النهائية."
        )
        deliverables = domain_deliverables.get(domain, domain_deliverables["general"])
        duration_hint = (
            f" (المدة المشار إليها في المشروع: {project.duration})" if project.duration else ""
        )
        timeline = (
            f"مدة تقديرية مبدئية قابلة للتعديل بعد الاطلاع الكامل على التفاصيل{duration_hint}."
        )
        cost = ""
        closing = (
            "يسعدني تواصلكم لمناقشة تفاصيل المشروع والإجابة عن أي استفسار قبل البدء."
            + (
                f"\nأسلوب التواصل المفضل: {prof.communication_style}."
                if prof and prof.communication_style
                else ""
            )
        )

        return ProposalSections(
            intro=intro,
            understanding=understanding,
            proposed_solution=proposed_solution,
            scope_of_work=scope_of_work,
            methodology=methodology,
            deliverables=deliverables,
            timeline=timeline,
            cost=cost,
            closing=closing,
        )
