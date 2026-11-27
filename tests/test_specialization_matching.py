"""Tests for Client Specialization Matching & Notification Engine (Threshold 65%).

Covers the 15 required verification cases:
1. مشروع تصميم نظام مكافحة حريق (Score >= 85, Accepted)
2. مشروع مخططات HVAC (Score >= 70, Accepted)
3. مشروع شوب دروينج ميكانيكا (Score >= 85, Accepted)
4. مشروع تصميم معماري أو داخلي (Score >= 70, Accepted)
5. مشروع رندر معماري 3D (Score >= 85, Accepted)
6. مشروع تصميم عرض تقديمي (Score >= 70, Accepted)
7. مشروع إعداد Pitch Deck (Score >= 85, Accepted)
8. مشروع كتابة سيرة ذاتية ATS (Score >= 85, Accepted)
9. مشروع تصميم موقع إلكتروني (Score < 65, Rejected)
10. مشروع برمجة تطبيق (Score < 65, Rejected)
11. مشروع تصميم شعار (Score < 65, Rejected)
12. مشروع كتابة محتوى عام (Score < 65, Rejected)
13. مشروع يحتوي على كلمة «تصميم» فقط دون سياق تخصصي (Score < 65, Rejected)
14. مشروع يستخدم مرادفًا إنجليزيًا لكلمة مفتاحية عربية (Score >= 70, Accepted)
15. مشروع يجمع بين كلمات من تخصصات مختلفة (Evaluated by primary deliverable)
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from resova.ai.analyzer import AIAnalyzer
from resova.config.settings import Settings
from resova.db.models import ScoreBand


@pytest.fixture()
def app_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token=SecretStr("mock-token-1234567890"),
        telegram_allowed_user_id=123456789,
        max_notification_age_hours=48,
    )


def test_case_1_fire_protection_system_match(app_settings: Settings) -> None:
    """1. مشروع تصميم نظام مكافحة حريق: Score >= 85, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم أنظمة مكافحة الحريق وشبكة إطفاء متكاملة لمستودع تجاري",
        description=(
            "مطلوب مهندس ميكانيكا لإعداد مخططات مكافحة حريق وشبكة رش آلي "
            "وحسابات هيدروليكية."
        ),
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.band == ScoreBand.strong
    assert result.primary_track == "engineering"
    assert result.recommendation == "notify"


def test_case_2_hvac_drawings_match(app_settings: Settings) -> None:
    """2. مشروع مخططات HVAC: Score >= 70, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="مطلوب إعداد مخططات HVAC لمبنى إداري",
        description=(
            "نحتاج مخططات دكتات وتكييف وتهوية للمبنى باستخدام أوتوكاد "
            "وحساب الأحمال."
        ),
        platform="mostaql",
    )
    assert result.score >= 70
    assert result.band in (ScoreBand.good, ScoreBand.strong)
    assert result.primary_track == "engineering"
    assert result.recommendation == "notify"


def test_case_3_shop_drawings_mechanics_match(app_settings: Settings) -> None:
    """3. مشروع شوب دروينج ميكانيكا: Score >= 85, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="إعداد رسومات تنفيذية شوب دروينج ميكانيكا لمشروع برج سكني",
        description=(
            "المشروع يشمل شوب دروينج لأنظمة مكافحة الحريق والتكييف والأعمال الصحية "
            "وحصر كميات."
        ),
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.band == ScoreBand.strong
    assert result.primary_track == "engineering"
    assert result.recommendation == "notify"


def test_case_4_architectural_interior_design_match(app_settings: Settings) -> None:
    """4. مشروع تصميم معماري أو داخلي: Score >= 70, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم معماري وتصميم داخلي لفيلا سكنية مودرن",
        description=(
            "نحتاج توزيع فراغات ومخططات معمارية كاملة مع ديكور داخلي "
            "وتصميم واجهات حديثة."
        ),
        platform="mostaql",
    )
    assert result.score >= 70
    assert result.band in (ScoreBand.good, ScoreBand.strong)
    assert result.primary_track == "architectural_interior"
    assert result.recommendation == "notify"


def test_case_5_3d_architectural_rendering_match(app_settings: Settings) -> None:
    """5. مشروع رندر معماري 3D: Score >= 85, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="رندر معماري وإظهار ثلاثي الأبعاد 3D لمشروع منتجع سياحي",
        description=(
            "مطلوب عمل موديل 3D ولقطات منظور ثلاثي الأبعاد وإظهار معماري واقعي "
            "باستخدام 3ds Max أو Lumion."
        ),
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.band == ScoreBand.strong
    assert result.primary_track == "architectural_interior"
    assert result.recommendation == "notify"


def test_case_6_presentation_design_match(app_settings: Settings) -> None:
    """6. مشروع تصميم عرض تقديمي: Score >= 70, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم عرض تقديمي احترافي وبوربوينت لخدمات الشركة",
        description=(
            "تنسيق عروض وإعادة تصميم سلايدات وتحويل محتوى وورد إلى "
            "شرائح بوربوينت أنيقة."
        ),
        platform="khamsat",
    )
    assert result.score >= 70
    assert result.band in (ScoreBand.good, ScoreBand.strong)
    assert result.primary_track == "presentation_design"
    assert result.recommendation == "notify"


def test_case_7_pitch_deck_preparation_match(app_settings: Settings) -> None:
    """7. مشروع إعداد Pitch Deck: Score >= 85, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="إعداد وتصميم Pitch Deck عرض استثماري لشركة ناشئة",
        description=(
            "نبحث عن مصمم متخصص في عروض المستثمرين Pitch Deck و Business Deck "
            "وتصميم بروفايل شركة احترافي."
        ),
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.band == ScoreBand.strong
    assert result.primary_track == "presentation_design"
    assert result.recommendation == "notify"


def test_case_8_ats_cv_writing_match(app_settings: Settings) -> None:
    """8. مشروع كتابة سيرة ذاتية ATS: Score >= 85, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="كتابة سيرة ذاتية احترافية متوافقة مع نظام ATS",
        description=(
            "مطلوب صياغة وتحسين السيرة الذاتية وكتابة CV باللغة الإنجليزية متوافق "
            "مع ATS مع خطاب تقديم ولينكدإن."
        ),
        platform="khamsat",
    )
    assert result.score >= 85
    assert result.band == ScoreBand.strong
    assert result.primary_track == "cv_resume"
    assert result.recommendation == "notify"


def test_case_9_website_design_rejected(app_settings: Settings) -> None:
    """9. مشروع تصميم موقع إلكتروني: Score < 65, Rejected."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم موقع إلكتروني متكامل للشركات",
        description="مطلوب تصميم وبرمجة موقع ويب وردبريس تفاعلي وسريع ومتوافق مع الموبايل.",
        platform="mostaql",
    )
    assert result.score < 65
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_case_10_app_development_rejected(app_settings: Settings) -> None:
    """10. مشروع برمجة تطبيق: Score < 65, Rejected."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="برمجة تطبيق جوال باستخدام Flutter",
        description="تطوير تطبيق موبايل لمتجر تجارة إلكترونية مع ربط API وقاعدة بيانات.",
        platform="mostaql",
    )
    assert result.score < 65
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_case_11_logo_design_rejected(app_settings: Settings) -> None:
    """11. مشروع تصميم شعار: Score < 65, Rejected."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم شعار وهوية بصرية لعلامة تجارية",
        description="نحتاج تصميم لوجو احترافي وهوية بصرية وبطاقات عمل وبوستات سوشيال ميديا.",
        platform="khamsat",
    )
    assert result.score < 65
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_case_12_general_content_writing_rejected(app_settings: Settings) -> None:
    """12. مشروع كتابة محتوى عام: Score < 65, Rejected."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="كتابة محتوى عام ومقالات متوافقة مع قواعد السيو SEO",
        description=(
            "مطلوب كاتب محتوى لكتابة مقالات تدوين يومية لمدونة متخصصة في التقنية "
            "والتسويق الرقمي."
        ),
        platform="mostaql",
    )
    assert result.score < 65
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_case_13_generic_design_term_rejected(app_settings: Settings) -> None:
    """13. مشروع يحتوي على كلمة «تصميم» فقط دون سياق تخصصي: Score < 65, Rejected."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="مطلوب تصميم",
        description="أحتاج إلى شخص متمكن للقيام ببعض أعمال التصميم العامة وغير المحددة.",
        platform="khamsat",
    )
    assert result.score < 65
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_case_14_english_synonym_match(app_settings: Settings) -> None:
    """14. مشروع يستخدم مرادفًا إنجليزيًا لكلمة مفتاحية عربية: Score >= 70, Accepted."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="Preparation of MEP Shop Drawings and As-Built Drawings",
        description=(
            "Looking for an engineer to deliver AutoCAD shop drawings and BOQ "
            "quantity takeoff for HVAC and fire fighting."
        ),
        platform="mostaql",
    )
    assert result.score >= 70
    assert result.band in (ScoreBand.good, ScoreBand.strong)
    assert result.primary_track == "engineering"
    assert result.recommendation == "notify"


def test_case_15_multi_deliverable_primary_evaluation(app_settings: Settings) -> None:
    """15. مشروع يجمع بين كلمات من تخصصات مختلفة: Evaluated by primary deliverable."""
    analyzer = AIAnalyzer(app_settings)

    # 15.A: Primary is in-scope (interior design) with secondary out-of-scope (logo)
    in_scope_primary = analyzer.analyze_without_api(
        title="تصميم داخلي ومعماري لمطعم فاخر",
        description=(
            "المشروع يركز على توزيع الفراغات والديكور الداخلي، ونحتاج أيضاً "
            "تصميم لوجو بسيط للمطعم."
        ),
        platform="mostaql",
    )
    assert in_scope_primary.score >= 70
    assert in_scope_primary.band in (ScoreBand.good, ScoreBand.strong)
    assert in_scope_primary.primary_track == "architectural_interior"
    assert in_scope_primary.recommendation == "notify"

    # 15.B: Primary is out-of-scope (mobile app) with secondary in-scope (presentation deck)
    out_of_scope_primary = analyzer.analyze_without_api(
        title="برمجة تطبيق متجر إلكتروني للهواتف الذكية",
        description=(
            "تطوير وبرمجة التطبيق بالكامل مع إعداد عرض تقديمي بسيط لشرح "
            "فكرة التطبيق."
        ),
        platform="mostaql",
    )
    assert out_of_scope_primary.score < 65
    assert out_of_scope_primary.band == ScoreBand.below
    assert out_of_scope_primary.recommendation == "skip"


# -----------------------------------------------------------------------------
# Additional Keyword Group & Edge-Case Verification Tests
# -----------------------------------------------------------------------------


def test_keyword_group_fire_alarm_and_mep(app_settings: Settings) -> None:
    """Verify fire alarm, MEP, engineering reports, and drawings review keywords."""
    analyzer = AIAnalyzer(app_settings)

    alarm = analyzer.analyze_without_api(
        title="تصميم أنظمة إنذار الحريق ومخططات إنذار الحريق لمبنى تجاري",
        description="إعداد مخططات مكافحة وإنذار الحريق ومراجعة المخططات الهندسية.",
        platform="mostaql",
    )
    assert alarm.score >= 85
    assert alarm.primary_track == "engineering"
    assert alarm.recommendation == "notify"

    mep = analyzer.analyze_without_api(
        title="تصميم أنظمة MEP وإعداد تقارير هندسية",
        description="مطلوب مهندس لمراجعة المخططات الهندسية وحساب كميات هندسية BOQ.",
        platform="mostaql",
    )
    assert mep.score >= 70
    assert mep.primary_track == "engineering"
    assert mep.recommendation == "notify"


def test_keyword_group_villas_kitchens_furniture(app_settings: Settings) -> None:
    """Verify villa, kitchen, furniture, and 3D interior/exterior keywords."""
    analyzer = AIAnalyzer(app_settings)

    villa = analyzer.analyze_without_api(
        title="تصميم فلل وتصميم خارجي 3D",
        description="نحتاج تصميم واجهات وتصميم داخلي 3D.",
        platform="mostaql",
    )
    assert villa.score >= 85
    assert villa.primary_track == "architectural_interior"
    assert villa.recommendation == "notify"

    kitchen_furniture = analyzer.analyze_without_api(
        title="تصميم مطابخ وتصميم أثاث وديكور داخلي",
        description="تصميم ديكور ومخططات تنفيذية لتوزيع الأثاث والمطبخ.",
        platform="khamsat",
    )
    assert kitchen_furniture.score >= 70
    assert kitchen_furniture.primary_track == "architectural_interior"
    assert kitchen_furniture.recommendation == "notify"


def test_keyword_group_presentation_subtypes(app_settings: Settings) -> None:
    """Verify training, interactive, corporate, PDF-to-PPT, and redesign presentations."""
    analyzer = AIAnalyzer(app_settings)

    for title in (
        "تصميم عروض شركات وتصميم عروض أعمال",
        "تصميم عروض تدريبية وتصميم عروض تفاعلية",
        "تحويل PDF إلى PowerPoint وإعادة تصميم عروض تقديمية",
    ):
        res = analyzer.analyze_without_api(
            title=title,
            description="مطلوب تصميم برزنتيشن باحترافية عالية.",
            platform="khamsat",
        )
        assert res.score >= 70, f"Failed for: {title}"
        assert res.primary_track == "presentation_design"
        assert res.recommendation == "notify"


def test_keyword_group_linkedin_and_europass(app_settings: Settings) -> None:
    """Verify LinkedIn optimization and Europass CV keywords."""
    analyzer = AIAnalyzer(app_settings)

    europass = analyzer.analyze_without_api(
        title="إعداد سيرة ذاتية أوروبية Europass باللغة الإنجليزية",
        description="سيرة ذاتية باللغة الإنجليزية مع خطاب تغطية وتوافق ATS.",
        platform="khamsat",
    )
    assert europass.score >= 85
    assert europass.primary_track == "cv_resume"
    assert europass.recommendation == "notify"

    linkedin = analyzer.analyze_without_api(
        title="تحسين LinkedIn وتطوير السيرة الذاتية",
        description="تحسين حساب لينكد إن وكتابة CV احترافي.",
        platform="mostaql",
    )
    assert linkedin.score >= 70
    assert linkedin.primary_track == "cv_resume"
    assert linkedin.recommendation == "notify"


def test_edge_fire_engineering_with_design_word(app_settings: Settings) -> None:
    """Edge 1: مشروع هندسة حريق يحتوي على كلمة تصميم."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم هندسي لنظام مكافحة الحريق",
        description="مطلوب تصميم شبكة مكافحة حريق لمستودع صناعي.",
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.primary_track == "engineering"
    assert result.recommendation == "notify"


def test_edge_website_design_with_secondary_engineering_in_desc(
    app_settings: Settings,
) -> None:
    """Edge 2: مشروع تصميم موقع يحتوي على كلمة تصميم هندسي في وصف ثانوي."""
    analyzer = AIAnalyzer(app_settings)

    # Case A: Website in title, engineering keyword in description
    res_title = analyzer.analyze_without_api(
        title="تصميم موقع إلكتروني لشركة استشارات",
        description="نريد برمجة موقع ويب يعرض خدماتنا في رسم هندسي أوتوكاد ومشاريعنا.",
        platform="mostaql",
    )
    assert res_title.score < 65
    assert res_title.band == ScoreBand.below
    assert res_title.recommendation == "skip"

    # Case B: Generic title, website primary in description, engineering secondary
    res_desc = analyzer.analyze_without_api(
        title="مشروع مطلوب تنفيذه لشركة مقاولات",
        description=(
            "المطلوب الأساسي تصميم موقع إلكتروني ومتجر إلكتروني متكامل، "
            "لعرض أعمالنا في رسم هندسي أوتوكاد."
        ),
        platform="mostaql",
    )
    assert res_desc.score < 65
    assert res_desc.band == ScoreBand.below
    assert res_desc.recommendation == "skip"


def test_edge_generic_title_architectural_description(
    app_settings: Settings,
) -> None:
    """Edge 3: مشروع معماري عنوانه عام لكن وصفه يوضح المطلوب."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="مطلوب تنفيذ عمل احترافي",
        description=(
            "نحتاج تصميم معماري ومخططات معمارية وتصميم داخلي 3D "
            "ورندر معماري لفيلا سكنية."
        ),
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.primary_track == "architectural_interior"
    assert result.recommendation == "notify"


def test_edge_presentation_with_marketing_word_not_digital_marketing(
    app_settings: Settings,
) -> None:
    """Edge 4: مشروع عروض تقديمية يتضمن كلمة تسويق دون أن يكون مشروع تسويق رقمي."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم عروض تسويقية وبوربوينت لمنتجات الشركة",
        description=(
            "نحتاج عرض تقديمي احترافي لعرض خطة التسويق والمبيعات على العملاء."
        ),
        platform="khamsat",
    )
    assert result.score >= 85
    assert result.primary_track == "presentation_design"
    assert result.recommendation == "notify"


def test_edge_cv_with_writing_word_not_general_writing(
    app_settings: Settings,
) -> None:
    """Edge 5: مشروع سيرة ذاتية يتضمن كلمة كتابة دون أن يكون كتابة محتوى عام."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="كتابة سيرة ذاتية باللغة العربية والإنجليزية",
        description="مطلوب كتابة وتنسيق سيرة ذاتية احترافية وخطاب تغطية للتقديم لوظيفة.",
        platform="khamsat",
    )
    assert result.score >= 85
    assert result.primary_track == "cv_resume"
    assert result.recommendation == "notify"

