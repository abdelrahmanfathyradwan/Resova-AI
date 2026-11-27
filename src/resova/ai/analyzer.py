"""AI-powered project analysis using OpenAI API.

Scores projects 0–100 against the client profile. Handles API failures, rate limits,
and daily usage caps. Never fabricates data — missing fields are reported as such.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from resova.config.profile import ClientProfile, load_profile
from resova.config.settings import Settings
from resova.db.models import ScoreBand
from resova.utils.errors import AIError
from resova.utils.normalize import normalize_text

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0"

# System prompt for the AI scorer
SYSTEM_PROMPT = """\
You are an expert freelance opportunity evaluator specializing in engineering,
architecture, presentation design, and CV/resume services. You analyze project
descriptions and score them 0–100 based on how well they match a freelancer's strict profile.

Rules:
- Score 0–100 where higher means better match. Minimum matching threshold is 65.
- Only use confirmed skills and approved keywords from the profile. Never assume skills not listed.
- Ground matching strictly on genuine specialization fit rather than single generic keywords.
- Generic terms such as "تصميم" (design), "رسم" (drawing), "كتابة" (writing), or "3D"
  alone without specialized domain context MUST NOT produce a qualifying match (score < 65).
- Strictly disqualify out-of-scope domains (score < 65, band 'below', recommendation 'skip'):
  * Web and mobile app development, software programming, CMS/WordPress.
  * Graphic design, logos, brand identity, flyers, motion graphics, video editing.
  * Fine art, artistic drawing, character illustration, portraits, comics.
  * General content writing, blogging, articles translation, digital marketing, SEO.
- Multi-deliverable projects: Evaluate based on the primary deliverable. If the primary deliverable
  is out-of-scope, reject the project even if a secondary deliverable mentions an in-scope keyword.
  If the primary deliverable matches an approved track, accept it and evaluate based on that track.
- Match score bands:
  * 85–100 (strong): Direct, unambiguous match to core specialization skills
    (e.g. fire protection system, HVAC shop drawings, architectural 3D rendering,
    pitch deck, ATS CV).
  * 70–84 (good): Strong match covering core track requirements or direct specialized deliverable.
  * 65–69 (moderate): Partial or adjacent match in an approved track.
  * 0–64 (below): Out-of-scope domain or generic/vague terms without domain context.
- Budget is NEVER a reason to exclude a project or reduce match score.
- Missing data should be noted as "not provided", never invented.
- Separate match_score (0-100 skill alignment) from confidence (0.0-1.0 certainty based on
  available information). A short description should express uncertainty through lower confidence,
  not by arbitrarily crushing the match score.
- Explain matching rationale and explicitly cite which confirmed skill(s) and track were matched.
- Respond with valid JSON only.
"""


def _build_user_prompt(
    title: str,
    description: str | None,
    platform: str,
    url: str | None,
    category: str | None,
    profile: ClientProfile,
) -> str:
    """Build the user prompt for AI analysis."""
    profile_summary = []
    for track in profile.tracks:
        keywords = track.match_keywords
        if keywords:
            profile_summary.append(
                f"- {track.label} ({track.experience_years or '?'} years): "
                f"{', '.join(keywords[:10])}"
            )

    excluded = profile.settings.excluded_keywords
    out_of_scope = profile.active_out_of_scope_domains

    return f"""\
Analyze this freelance project opportunity:

**Title:** {title}
**Platform:** {platform}
**Category:** {category or "not provided"}
**Description:** {description or "not provided"}
**URL:** {url or "not provided"}

**Freelancer Profile:**
{chr(10).join(profile_summary) if profile_summary else "No tracks defined"}

**Excluded keywords:** {", ".join(excluded) if excluded else "none"}
**Out-of-scope domains (soft penalty):** {", ".join(out_of_scope) if out_of_scope else "none"}
**Minimum match score:** {profile.settings.min_match_score}

Respond with ONLY valid JSON in this exact format:
{{
  "score": <0-100>,
  "confidence": <0.0-1.0>,
  "band": "<strong|good|moderate|below>",
  "primary_track": "<track_id or null>",
  "matching_skills": ["skill1", "skill2"],
  "missing_skills": ["skill1"],
  "summary": "<2-3 sentence summary>",
  "rationale": "<why this score>",
  "risks": ["risk1", "risk2"],
  "language": "<ar|en>",
  "recommendation": "<notify|digest|skip>"
}}
"""


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Result of an AI analysis of a project opportunity."""

    score: int
    band: ScoreBand
    primary_track: str | None
    matching_skills: list[str]
    missing_skills: list[str]
    summary: str
    rationale: str
    risks: list[str]
    language: str
    recommendation: str
    model: str
    confidence: float = 1.0
    prompt_version: str = PROMPT_VERSION
    raw_response: dict[str, Any] = field(default_factory=dict)


def _score_to_band(score: int, profile: ClientProfile) -> ScoreBand:
    """Convert a numeric score to a band using profile thresholds."""
    bands = profile.settings.score_bands
    if score >= bands.strong:
        return ScoreBand.strong
    if score >= bands.good:
        return ScoreBand.good
    moderate_threshold = getattr(bands, "moderate", getattr(bands, "weak", 65))
    if score >= moderate_threshold:
        return ScoreBand.moderate
    return ScoreBand.below


# Out-of-scope domain keywords for strict rejection
OUT_OF_SCOPE_TERMS = {
    "web_app": [
        "موقع إلكتروني",
        "موقع الكتروني",
        "موقع ويب",
        "متجر إلكتروني",
        "متجر الكتروني",
        "تطوير موقع",
        "برمجة موقع",
        "تصميم موقع",
        "تطبيق جوال",
        "تطبيق موبايل",
        "تطبيقات هواتف",
        "تطبيق أندرويد",
        "تطبيق ايفون",
        "تطبيق ios",
        "برمجة تطبيق",
        "تطوير تطبيق",
        "برمجة متجر",
        "تطوير متجر",
        "ووردبريس",
        "وردبريس",
        "wordpress",
        "react",
        "vue",
        "angular",
        "node.js",
        "django",
        "laravel",
        "flutter",
        "ios",
        "android",
        "php",
        "javascript",
        "backend",
        "frontend",
        "full stack",
        "api",
        "برمجة",
        "مبرمج",
    ],
    "graphic_logo": [
        "تصميم شعار",
        "شعار",
        "لوجو",
        "شعارات",
        "هوية بصرية",
        "هوية تجارية",
        "موشن جرافيك",
        "موشن",
        "مونتاج فيديو",
        "مونتاج",
        "سوشيال ميديا",
        "بوستات",
        "بنر",
        "بنرات",
        "فلاير",
        "بروشور",
        "بوستر",
        "بطاقة أعمال",
        "كروت عمل",
        "logo",
        "branding",
        "motion graphics",
        "video editing",
    ],
    "art": [
        "رسم شخصيات",
        "شخصيات كرتونية",
        "بورتريه",
        "كوميكس",
        "كارتون",
        "كاريكاتير",
        "رسم يدوي",
        "رسم رقمي فني",
        "رسام",
        "portrait",
        "illustration",
        "comics",
        "cartoon",
        "character design",
    ],
    "general_writing": [
        "كتابة مقالات",
        "كتابة محتوى",
        "صانع محتوى",
        "صناع محتوى",
        "تدوين",
        "بلوجر",
        "سيو",
        "seo",
        "تسويق إلكتروني",
        "تسويق رقمي",
        "حملات إعلانية",
        "إدارة صفحات",
        "إدارة حسابات",
        "ترجمة مقالات",
        "ترجمة نصوص",
        "ترجمة فورية",
        "content writing",
        "copywriting",
        "digital marketing",
    ],
}

ALL_OUT_OF_SCOPE = [term for group in OUT_OF_SCOPE_TERMS.values() for term in group]

# Technical abbreviations and synonyms mapped to tracks
TRACK_SYNONYMS: dict[str, list[str]] = {
    "engineering": [
        "hvac",
        "تكييف وتهوية",
        "تكييف و تهوية",
        "تكييف",
        "تهوية",
        "تدفئة وتكييف",
        "مخططات hvac",
        "دكتات",
        "مكافحة حريق",
        "مكافحه حريق",
        "إطفاء حريق",
        "اطفاء حريق",
        "شبكة إطفاء",
        "شبكه اطفاء",
        "إنذار حريق",
        "انذار حريق",
        "رش آلي",
        "رش الي",
        "مضخات حريق",
        "مضخات الحريق",
        "حسابات هيدروليكية",
        "حسابات هيدروليكيه",
        "أنظمة إطفاء الغاز",
        "انظمة اطفاء الغاز",
        "fm200",
        "fm-200",
        "نظام مكافحة حريق",
        "تصميم نظام مكافحة حريق",
        "تصميم أنظمة مكافحة الحريق",
        "تصميم أنظمة إنذار الحريق",
        "مخططات مكافحة الحريق",
        "مخططات إنذار الحريق",
        "شبكة رش آلي",
        "شبكة مكافحة الحريق",
        "شوب دروينج ميكانيكا",
        "شوب دروينج فاير فايتنج",
        "تصميم أنظمة التكييف hvac",
        "تصميم أنظمة التكييف",
        "تصميم أنظمة التهوية",
        "تصميم أنظمة mep",
        "رسم مخططات ميكانيكية",
        "رسم هندسي أوتوكاد",
        "رسم هندسي اوتوكاد",
        "مخططات تنفيذية",
        "حصر كميات boq",
        "حساب كميات هندسية",
        "إعداد تقارير هندسية",
        "اعداد تقارير هندسية",
        "مراجعة المخططات الهندسية",
        "مراجعه المخططات الهندسيه",
        "رسومات as built",
        "fire fighting",
        "firefighting",
        "fire protection",
        "fire fighting system",
        "fire alarm",
        "fire alarm system",
        "fire alarm system design",
        "sprinkler system",
        "sprinkler",
        "fire pump",
        "fire suppression",
        "hydraulic calculations",
        "clean agent",
        "رسومات تنفيذية",
        "رسومات تنفيذيه",
        "شوب دروينج",
        "شوب دروينغ",
        "رسومات مطابقة للواقع",
        "رسومات كما نفذت",
        "حصر كميات",
        "جدول كميات",
        "مخططات ميكانيكية",
        "مخططات ميكانيكيه",
        "أعمال صحية",
        "اعمال صحية",
        "أوتوكاد",
        "اوتوكاد",
        "ميكانيكا",
        "كهروميكانيكية",
        "أنظمة ميكانيكية",
        "مكتب فني",
        "shop drawing",
        "shop drawings",
        "mechanical shop drawings",
        "as-built",
        "as built",
        "as-built drawings",
        "as built drawings",
        "boq",
        "quantity takeoff",
        "engineering quantity calculation",
        "engineering reports",
        "engineering drawings review",
        "mep",
        "mep drawings",
        "mep system design",
        "mep coordination",
        "hvac system design",
        "ventilation system design",
        "plumbing",
        "plumbing drawings",
        "autocad",
        "autocad 2d",
        "technical office",
        "mechanical engineering",
        "mechanical drawings",
        "production engineering",
    ],
    "architectural_interior": [
        "تصميم معماري",
        "مخططات معمارية",
        "مخططات معماريه",
        "تصميم داخلي",
        "تصميم ديكور",
        "ديكور داخلي",
        "توزيع فراغات",
        "تصميم واجهات",
        "تصميم فلل",
        "تصميم ثلاثي الأبعاد",
        "تصميم ثلاثي الابعاد",
        "رندر معماري",
        "تصميم داخلي 3d",
        "تصميم خارجي 3d",
        "تصميم مطابخ",
        "تصميم أثاث",
        "تصميم اثاث",
        "موديل 3d",
        "نمذجة ثلاثية الأبعاد",
        "نمذجه ثلاثيه الابعاد",
        "إظهار معماري",
        "اظهار معماري",
        "لقطات منظور ثلاثي الأبعاد",
        "لقطات منظور ثلاثي الابعاد",
        "تصميم سكني",
        "تصميم تجاري",
        "رسم معماري",
        "مخطط معماري",
        "واجهات معمارية",
        "ديكور",
        "مخططات تنفيذية",
        "architectural design",
        "architectural drawings",
        "interior design",
        "interior decor",
        "space planning",
        "facade design",
        "villa design",
        "3d modeling",
        "3d rendering",
        "3d design",
        "interior 3d design",
        "exterior 3d design",
        "architectural visualization",
        "3d perspective",
        "residential design",
        "commercial design",
        "kitchen design",
        "furniture design",
        "3ds max",
        "sketchup",
        "revit",
        "lumion",
        "v-ray",
        "vray",
        "autocad architecture",
    ],
    "presentation_design": [
        "تصميم عروض تقديمية",
        "تصميم عرض تقديمي",
        "تصميم بوربوينت",
        "تصميم برزنتيشن",
        "برزنتيشن",
        "عروض تقديمية احترافية",
        "عرض تقديمي احترافي",
        "تصميم عروض شركات",
        "تصميم عروض أعمال",
        "تصميم عروض اعمال",
        "تصميم عروض استثمارية",
        "تصميم عروض استثماريه",
        "تصميم بروفايل شركات",
        "تصميم pitch deck",
        "عرض تقديمي",
        "عروض تقديمية",
        "عروض تقديميه",
        "بوربوينت",
        "باور بوينت",
        "باوربوينت",
        "بروفايل شركة",
        "بروفايل شركه",
        "الملف التعريفي للشركة",
        "الملف التعريفي",
        "عرض استثماري",
        "pitch deck",
        "business deck",
        "إعادة تصميم عروض تقديمية",
        "اعادة تصميم عروض تقديمية",
        "إعادة تصميم سلايدات",
        "اعادة تصميم سلايدات",
        "تحويل pdf إلى powerpoint",
        "تحويل pdf الى powerpoint",
        "تحويل ملفات إلى بوربوينت",
        "تحويل ملفات الى بوربوينت",
        "تصميم عروض تدريبية",
        "تصميم عروض تدريبيه",
        "تصميم عروض تسويقية",
        "تصميم عروض تسويقيه",
        "عروض تسويقية",
        "عروض تسويقيه",
        "تصميم عروض تفاعلية",
        "تصميم عروض تفاعليه",
        "تنسيق عروض",
        "سلايدات احترافية",
        "سلايدات احترافيه",
        "عرض احترافي",
        "سلايدات",
        "شرائح عرض",
        "powerpoint",
        "ppt",
        "pptx",
        "presentation design",
        "presentation",
        "business presentation",
        "company profile deck",
        "company profile",
        "investor deck",
        "corporate presentation",
        "technical presentation",
        "training presentation",
        "interactive presentation",
        "slide design",
        "slide redesign",
        "presentation formatting",
        "deck redesign",
        "pdf to powerpoint",
        "report presentation",
        "infographic slides",
        "marketing presentation",
    ],
    "cv_resume": [
        "سيرة ذاتية",
        "سيره ذاتيه",
        "كتابة سيرة ذاتية",
        "كتابه سيره ذاتيه",
        "تصميم سيرة ذاتية",
        "تصميم سيره ذاتيه",
        "سيرة ذاتية ats",
        "سيرة ذاتية احترافية",
        "سيره ذاتيه احترافيه",
        "cv احترافي",
        "تطوير السيرة الذاتية",
        "تطوير السيره الذاتيه",
        "سيرة ذاتية متوافقة مع ats",
        "توافق ats",
        "خطاب تغطية",
        "خطاب تغطيه",
        "تحسين linkedin",
        "سيرة ذاتية باللغة الإنجليزية",
        "سيرة ذاتية باللغة الانجليزية",
        "سيره ذاتيه باللغه الانجليزيه",
        "سيرة ذاتية باللغة العربية",
        "سيره ذاتيه باللغه العربيه",
        "سيرة ذاتية أوروبية europass",
        "سيره ذاتيه اوروبيه europass",
        "europass",
        "تعديل سيرة ذاتية",
        "تعديل سيره ذاتيه",
        "صياغة سيرة ذاتية",
        "صياغه سيره ذاتيه",
        "تحسين السيرة الذاتية",
        "تحسين السيره الذاتيه",
        "كتابة cv",
        "كتابه cv",
        "تصميم cv",
        "تعديل cv",
        "خطاب تقديم",
        "حساب لينكد إن",
        "لينكد إن",
        "لينكدإن",
        "حساب لينكدين",
        "تحسين لينكدإن",
        "cv writing",
        "resume writing",
        "ats-friendly cv",
        "ats-friendly resume",
        "ats optimization",
        "ats compliance",
        "ats cv",
        "ats resume",
        "ats",
        "cv design",
        "resume design",
        "resume editing",
        "resume rewriting",
        "resume redesign",
        "resume development",
        "linkedin optimization",
        "linkedin profile",
        "cover letter",
        "executive cv",
        "technical cv",
        "curriculum vitae",
        "professional bio",
        "professional cv",
        "europass cv",
        "english cv",
        "arabic cv",
        "cv",
        "resume",
    ],
}

# Core high-priority terms that indicate direct unambiguous match (score >= 85)
CORE_HIGH_SCORE_TERMS: dict[str, list[str]] = {
    "engineering": [
        "مكافحة حريق",
        "إطفاء حريق",
        "رش آلي",
        "شبكة إطفاء",
        "مضخات حريق",
        "حسابات هيدروليكية",
        "أنظمة إطفاء الغاز",
        "fm200",
        "fm-200",
        "نظام مكافحة حريق",
        "تصميم نظام مكافحة حريق",
        "تصميم أنظمة مكافحة الحريق",
        "تصميم أنظمة إنذار الحريق",
        "مخططات مكافحة الحريق",
        "مخططات إنذار الحريق",
        "شوب دروينج ميكانيكا",
        "شوب دروينج فاير فايتنج",
        "شوب دروينج",
        "رسومات تنفيذية",
        "مخططات تنفيذية",
        "رسومات مطابقة للواقع",
        "رسومات as built",
        "تصميم أنظمة التكييف hvac",
        "تصميم أنظمة التهوية",
        "تصميم أنظمة mep",
        "as-built",
        "as built",
        "fire fighting",
        "firefighting",
        "fire protection",
        "fire alarm",
        "sprinkler system",
        "hydraulic calculations",
        "clean agent",
        "shop drawings",
        "shop drawing",
        "mechanical shop drawings",
    ],
    "architectural_interior": [
        "رندر معماري",
        "إظهار معماري",
        "لقطات منظور ثلاثي الأبعاد",
        "موديل 3d",
        "نمذجة ثلاثية الأبعاد",
        "تصميم فلل",
        "تصميم داخلي 3d",
        "تصميم خارجي 3d",
        "تصميم ثلاثي الأبعاد",
        "3d rendering",
        "3d design",
        "interior 3d design",
        "exterior 3d design",
        "architectural visualization",
        "villa design",
        "3ds max",
        "sketchup",
        "lumion",
        "v-ray",
        "revit",
    ],
    "presentation_design": [
        "pitch deck",
        "business deck",
        "تصميم pitch deck",
        "عرض استثماري",
        "الملف التعريفي للشركة",
        "بروفايل شركة",
        "تصميم بروفايل شركات",
        "تصميم عروض استثمارية",
        "company profile deck",
        "investor deck",
        "سلايدات احترافية",
        "عرض تقديمي احترافي",
        "عروض تقديمية احترافية",
        "تصميم عروض تقديمية",
        "تصميم بوربوينت",
        "تصميم برزنتيشن",
    ],
    "cv_resume": [
        "سيرة ذاتية متوافقة مع ats",
        "سيرة ذاتية ats",
        "توافق ats",
        "ats-friendly cv",
        "ats optimization",
        "ats compliance",
        "ats",
        "كتابة سيرة ذاتية",
        "كتابة cv",
        "تحسين السيرة الذاتية",
        "سيرة ذاتية احترافية",
        "cv احترافي",
        "سيرة ذاتية أوروبية europass",
        "europass cv",
        "europass",
    ],
}

GENERIC_ONLY_TERMS = {
    "تصميم",
    "رسم",
    "كتابة",
    "كتابه",
    "3d",
    "ثلاثي الأبعاد",
    "ثلاثي الابعاد",
    "مصمم",
    "رسام",
    "كاتب",
}


class AIAnalyzer:
    """Analyzes projects using OpenAI API against the client profile.

    Respects daily analysis limits and handles API errors gracefully.
    The analyzer never makes network calls during ``__init__``; calls happen
    only when :meth:`analyze` is invoked.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._daily_count: int = 0
        self._last_reset: datetime = datetime.now(UTC).replace(tzinfo=None)
        self._profile: ClientProfile | None = None

    @property
    def profile(self) -> ClientProfile:
        """Lazily load the client profile."""
        if self._profile is None:
            self._profile = load_profile(self._settings.profile_path)
        return self._profile

    def _reset_daily_counter_if_needed(self) -> None:
        """Reset the daily counter at midnight UTC."""
        now = datetime.now(UTC).replace(tzinfo=None)
        if now.date() > self._last_reset.date():
            self._daily_count = 0
            self._last_reset = now

    def can_analyze(self) -> bool:
        """Check whether analysis is possible (enabled, has key, under limit)."""
        if not self._settings.enable_ai_analysis:
            return False
        if self._settings.openai_api_key is None:
            return False
        self._reset_daily_counter_if_needed()
        return self._daily_count < self._settings.daily_ai_analysis_limit

    def analyze(
        self,
        title: str,
        description: str | None = None,
        platform: str = "unknown",
        url: str | None = None,
        category: str | None = None,
    ) -> AnalysisResult:
        """Analyze a project and return a scored result.

        Raises:
            AIError: When the API call fails or the response cannot be parsed.
            AIError: When the daily limit is exceeded.
        """
        if not self._settings.enable_ai_analysis:
            raise AIError("AI analysis is disabled")

        api_key = self._settings.openai_api_key
        if api_key is None:
            raise AIError("OpenAI API key is not configured")

        self._reset_daily_counter_if_needed()
        if self._daily_count >= self._settings.daily_ai_analysis_limit:
            raise AIError(
                f"Daily AI analysis limit reached ({self._settings.daily_ai_analysis_limit})"
            )

        profile = self.profile
        model = self._settings.openai_model

        user_prompt = _build_user_prompt(
            title=title,
            description=description,
            platform=platform,
            url=url,
            category=category,
            profile=profile,
        )

        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(api_key=api_key.get_secret_value())
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or "{}"
            parsed = json.loads(content)
        except ImportError:
            raise AIError(
                "openai package is not installed. Install it with: pip install openai"
            ) from None
        except json.JSONDecodeError as exc:
            raise AIError(f"Failed to parse AI response as JSON: {exc}") from exc
        except Exception as exc:
            raise AIError(f"OpenAI API call failed: {exc}") from exc

        self._daily_count += 1

        score = max(0, min(100, int(parsed.get("score", 0))))
        band = _score_to_band(score, profile)
        confidence = 1.0
        if "confidence" in parsed:
            try:
                confidence = max(0.0, min(1.0, float(parsed["confidence"])))
            except (ValueError, TypeError):
                confidence = 1.0

        return AnalysisResult(
            score=score,
            band=band,
            primary_track=parsed.get("primary_track"),
            matching_skills=parsed.get("matching_skills", []),
            missing_skills=parsed.get("missing_skills", []),
            summary=parsed.get("summary", ""),
            rationale=parsed.get("rationale", ""),
            risks=parsed.get("risks", []),
            language=parsed.get("language", "ar"),
            recommendation=parsed.get("recommendation", "skip"),
            model=model,
            confidence=confidence,
            raw_response=parsed,
        )

    def analyze_without_api(
        self,
        title: str,
        description: str | None = None,
        platform: str = "unknown",
        category: str | None = None,
    ) -> AnalysisResult:
        """Specialization-grounded keyword scoring without calling the AI API.

        Strictly enforces:
        - Minimum match threshold (65%).
        - Four independent specialization tracks.
        - Primary deliverable determination for multi-deliverable projects.
        - Disqualification of generic terms alone ('تصميم', 'رسم', 'كتابة', '3D').
        - Disqualification of out-of-scope domains (web/app, graphic/logo, fine art, writing).
        """
        profile = self.profile
        min_threshold = profile.settings.min_match_score

        title_str = title or ""
        desc_str = description or ""
        title_norm = normalize_text(title_str).lower()
        desc_norm = normalize_text(desc_str).lower()
        title_raw_lower = title_str.lower()
        desc_raw_lower = desc_str.lower()

        combined_norm = f"{title_norm} {desc_norm}"
        no_al_norm = re.sub(r"\bال", "", combined_norm)
        search_corpus = f"{combined_norm} {no_al_norm}"

        # 1. Primary deliverable and out-of-scope check
        is_out_of_scope = False
        oos_matched_term: str | None = None

        # Check out of scope matches in title first
        for oos in ALL_OUT_OF_SCOPE:
            oos_norm = normalize_text(oos).lower()
            if oos_norm in title_norm or oos.lower() in title_raw_lower:
                oos_matched_term = oos
                break

        # If an out-of-scope term is found in title, check if an in-scope track term
        # precedes it as primary deliverable
        if oos_matched_term:
            oos_pos = title_norm.find(normalize_text(oos_matched_term).lower())
            if oos_pos == -1:
                oos_pos = title_raw_lower.find(oos_matched_term.lower())

            in_scope_earlier = False
            for track in profile.tracks:
                all_kws = track.match_keywords + TRACK_SYNONYMS.get(track.id, [])
                for kw in all_kws:
                    kw_n = normalize_text(kw).lower()
                    kw_pos = title_norm.find(kw_n)
                    if kw_pos == -1:
                        kw_pos = title_raw_lower.find(kw.lower())
                    if kw_pos != -1 and kw_pos < oos_pos:
                        in_scope_earlier = True
                        break
                if in_scope_earlier:
                    break

            if not in_scope_earlier:
                is_out_of_scope = True

        # If title is ambiguous/generic but description is primarily OOS, check
        # description for out-of-scope dominance. Only when no OOS found in title
        # and no strong in-scope signal is in title.
        if not oos_matched_term and not is_out_of_scope:
            title_has_track_signal = False
            for track in profile.tracks:
                all_kws = track.match_keywords + TRACK_SYNONYMS.get(track.id, [])
                for kw in all_kws:
                    kw_n = normalize_text(kw).lower()
                    if (kw_n in title_norm
                            or kw.lower() in title_raw_lower):
                        title_has_track_signal = True
                        break
                if title_has_track_signal:
                    break

            if not title_has_track_signal:
                # Title has no in-scope signal; check if description's primary
                # deliverable is out-of-scope by comparing earliest positions.
                earliest_oos_pos: int | None = None
                for oos in ALL_OUT_OF_SCOPE:
                    oos_norm = normalize_text(oos).lower()
                    pos = desc_norm.find(oos_norm)
                    if pos == -1:
                        pos = desc_raw_lower.find(oos.lower())
                    if pos != -1 and (
                        earliest_oos_pos is None or pos < earliest_oos_pos
                    ):
                        earliest_oos_pos = pos

                if earliest_oos_pos is not None:
                    earliest_inscope_pos: int | None = None
                    for track in profile.tracks:
                        all_kws = (
                            track.match_keywords
                            + TRACK_SYNONYMS.get(track.id, [])
                        )
                        for kw in all_kws:
                            kw_n = normalize_text(kw).lower()
                            pos = desc_norm.find(kw_n)
                            if pos == -1:
                                pos = desc_raw_lower.find(kw.lower())
                            if pos != -1 and (
                                earliest_inscope_pos is None
                                or pos < earliest_inscope_pos
                            ):
                                earliest_inscope_pos = pos

                    if (
                        earliest_inscope_pos is None
                        or earliest_oos_pos < earliest_inscope_pos
                    ):
                        is_out_of_scope = True

        # Check for track matches
        best_track_id: str | None = None
        best_track_score = 0
        best_track_skills: list[str] = []

        for track in profile.tracks:
            matched_in_track: list[str] = []
            title_matches: list[str] = []

            # 1. Profile keywords
            for kw in track.match_keywords:
                kw_norm = normalize_text(kw).lower()
                kw_no_al = re.sub(r"\bال", "", kw_norm)
                kw_raw = kw.lower()

                in_combined = (
                    kw_norm in search_corpus
                    or kw_no_al in search_corpus
                    or kw_raw in title_raw_lower
                    or kw_raw in desc_raw_lower
                )
                if in_combined:
                    if kw not in matched_in_track:
                        matched_in_track.append(kw)
                    in_title = (
                        kw_norm in title_norm
                        or kw_no_al in title_norm
                        or kw_raw in title_raw_lower
                    )
                    if in_title and kw not in title_matches:
                        title_matches.append(kw)

            # 2. Track synonyms
            for syn in TRACK_SYNONYMS.get(track.id, []):
                syn_norm = normalize_text(syn).lower()
                syn_no_al = re.sub(r"\bال", "", syn_norm)
                syn_raw = syn.lower()

                in_combined = (
                    syn_norm in search_corpus
                    or syn_no_al in search_corpus
                    or syn_raw in title_raw_lower
                    or syn_raw in desc_raw_lower
                )
                if in_combined:
                    if syn not in matched_in_track:
                        matched_in_track.append(syn)
                    in_title = (
                        syn_norm in title_norm
                        or syn_no_al in title_norm
                        or syn_raw in title_raw_lower
                    )
                    if in_title and syn not in title_matches:
                        title_matches.append(syn)

            # Check core high scoring terms for this track
            has_core_term = any(
                normalize_text(core).lower() in search_corpus
                or core.lower() in title_raw_lower
                or core.lower() in desc_raw_lower
                for core in CORE_HIGH_SCORE_TERMS.get(track.id, [])
            )

            # Compute track score
            if matched_in_track:
                if has_core_term:
                    if title_matches or len(matched_in_track) >= 2:
                        track_score = max(88, min(100, 85 + len(matched_in_track) * 3))
                    else:
                        track_score = 85
                elif title_matches:
                    if len(matched_in_track) >= 3:
                        track_score = 88
                    elif len(matched_in_track) == 2:
                        track_score = 80
                    else:
                        track_score = 75
                else:
                    if len(matched_in_track) >= 3:
                        track_score = 80
                    elif len(matched_in_track) == 2:
                        track_score = 72
                    else:
                        track_score = 65

                if track_score > best_track_score:
                    best_track_score = track_score
                    best_track_id = track.id
                    best_track_skills = matched_in_track

        # If no track matched and text has out-of-scope terms, confirm out-of-scope
        if best_track_score == 0:
            for oos in ALL_OUT_OF_SCOPE:
                oos_norm = normalize_text(oos).lower()
                if oos_norm in search_corpus or oos.lower() in desc_raw_lower:
                    is_out_of_scope = True
                    break

        # If project is primarily out of scope, clamp score to 0
        if is_out_of_scope:
            score = 0
            primary_track = None
            matching_skills = []
        else:
            score = best_track_score
            primary_track = best_track_id
            matching_skills = best_track_skills

        # Apply excluded keywords penalty
        for excluded in profile.settings.excluded_keywords:
            if excluded.lower() in combined_norm or excluded.lower() in desc_raw_lower:
                score = max(0, score - 30)

        band = _score_to_band(score, profile)
        confidence = (
            0.9 if description and len(matching_skills) >= 2
            else (0.6 if description else 0.55)
        )

        count_matched = len(matching_skills)
        recommendation = "notify" if score >= min_threshold else "skip"

        return AnalysisResult(
            score=score,
            band=band,
            primary_track=primary_track,
            matching_skills=matching_skills,
            missing_skills=[],
            summary=f"Specialization scoring (AI disabled): {count_matched} skill(s) matched.",
            rationale="Scored by specialization matching against profile tracks.",
            risks=[],
            language="ar",
            recommendation=recommendation,
            model="keyword-scorer",
            confidence=confidence,
        )
