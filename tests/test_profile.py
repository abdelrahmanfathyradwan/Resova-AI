"""Tests for Phase 1 / Step 2.3 - client profile configuration."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from resova.config.profile import ArabicKeywordProposal, ClientProfile, Track, load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "profile.yaml"


@pytest.fixture()
def profile() -> ClientProfile:
    return load_profile(PROFILE_PATH)


def _data(profile: ClientProfile) -> dict:
    return copy.deepcopy(profile.model_dump(mode="json"))


def _sample_track() -> Track:
    return Track(
        id="sample",
        label="Sample",
        keywords_en=["alpha"],
        keywords_ar_proposed=[ArabicKeywordProposal(text="بيتا", approved=False)],
    )


# --- shipped profile -----------------------------------------------------------


def test_shipped_profile_loads(profile: ClientProfile) -> None:
    assert profile.version >= 1
    assert len(profile.tracks) == 4
    assert {track.id for track in profile.tracks} == {
        "engineering",
        "architectural_interior",
        "presentation_design",
        "cv_resume",
    }
    assert profile.settings.min_match_score == 65
    assert profile.settings.score_bands.strong == 85
    assert profile.settings.score_bands.good == 70
    assert profile.settings.score_bands.moderate == 65
    assert profile.settings.score_bands.weak == 65
    assert profile.settings.notify_language == "ar"
    assert profile.settings.excluded_keywords == []
    assert profile.settings.min_budget is None
    assert profile.settings.preferred_duration is None


def test_titles_and_languages_present(profile: ClientProfile) -> None:
    assert "Mechanical Engineer" in profile.titles
    assert "Professional Presentation & CV Designer" in profile.titles
    assert set(profile.languages) == {"ar", "en"}


# --- Arabic keyword approval gate ---------------------------------------------


def test_unapproved_arabic_keywords_are_excluded_from_matching(profile: ClientProfile) -> None:
    proposals = [item for track in profile.tracks for item in track.keywords_ar_proposed]
    assert proposals, "expected proposed Arabic keywords in the shipped profile"
    unapproved = [item for item in proposals if not item.approved]
    assert unapproved, "expected at least one unapproved keyword (e.g. مقايسة)"
    for track in profile.tracks:
        unapproved_in_track = {
            item.text.lower() for item in track.keywords_ar_proposed if not item.approved
        }
        for unapproved_kw in unapproved_in_track:
            assert unapproved_kw not in [kw.lower() for kw in track.match_keywords]


def test_approved_arabic_keyword_is_included_in_matching() -> None:
    track = _sample_track()
    assert track.match_keywords == ["alpha"]
    track.keywords_ar_proposed[0].approved = True
    assert track.match_keywords == ["alpha", "بيتا"]


# --- unconfirmed skills gate ---------------------------------------------------


def test_unconfirmed_skills_are_excluded_until_confirmed(profile: ClientProfile) -> None:
    assert len(profile.unconfirmed_skills) == 6
    assert all(skill.confirmed is False for skill in profile.unconfirmed_skills)
    assert profile.confirmed_skills == []
    assert len(profile.pending_skills) == 6


def test_confirming_a_skill_moves_it_out_of_pending(profile: ClientProfile) -> None:
    data = _data(profile)
    data["unconfirmed_skills"][0]["confirmed"] = True
    other = ClientProfile.model_validate(data)
    assert other.confirmed_skills == [data["unconfirmed_skills"][0]["label"]]
    assert len(other.pending_skills) == 5


# --- versioning / hashing ------------------------------------------------------


def test_same_profile_yields_same_hash(profile: ClientProfile) -> None:
    assert profile.content_hash() == load_profile(PROFILE_PATH).content_hash()


def test_profile_hash_is_deterministic(profile: ClientProfile) -> None:
    assert profile.content_hash() == profile.content_hash()


def test_keyword_change_alters_hash(profile: ClientProfile) -> None:
    data = _data(profile)
    data["tracks"][0]["keywords_en"].append("brand-new-keyword")
    other = ClientProfile.model_validate(data)
    assert other.content_hash() != profile.content_hash()


def test_threshold_change_alters_hash(profile: ClientProfile) -> None:
    data = _data(profile)
    data["settings"]["min_match_score"] = 75
    other = ClientProfile.model_validate(data)
    assert other.content_hash() != profile.content_hash()


def test_approving_arabic_keyword_alters_hash(profile: ClientProfile) -> None:
    data = _data(profile)
    # Toggle an unapproved keyword (e.g. مقايسة which is the last in engineering track)
    unapproved_idx = [
        i for i, item in enumerate(data["tracks"][0]["keywords_ar_proposed"])
        if not item["approved"]
    ][0]
    data["tracks"][0]["keywords_ar_proposed"][unapproved_idx]["approved"] = True
    other = ClientProfile.model_validate(data)
    assert other.content_hash() != profile.content_hash()


def test_snapshot_shape(profile: ClientProfile) -> None:
    snapshot = profile.snapshot()
    assert set(snapshot) == {"version", "content_hash", "payload"}
    assert snapshot["version"] == profile.version
    assert snapshot["content_hash"] == profile.content_hash()
    assert snapshot["payload"]["version"] == profile.version


# --- validation ----------------------------------------------------------------


def test_score_bands_must_be_ordered(profile: ClientProfile) -> None:
    data = _data(profile)
    data["settings"]["score_bands"] = {"strong": 70, "good": 85, "weak": 50}
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


def test_min_match_score_out_of_range_rejected(profile: ClientProfile) -> None:
    data = _data(profile)
    data["settings"]["min_match_score"] = 150
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


def test_duplicate_track_ids_rejected(profile: ClientProfile) -> None:
    data = _data(profile)
    data["tracks"].append(copy.deepcopy(data["tracks"][0]))
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


def test_unknown_key_rejected(profile: ClientProfile) -> None:
    data = _data(profile)
    data["unexpected_field"] = 1
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


def test_missing_profile_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_profile(ROOT / "config" / "does-not-exist.yaml")


def test_empty_tracks_rejected(profile: ClientProfile) -> None:
    data = _data(profile)
    data["tracks"] = []
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


def test_duplicate_unconfirmed_skill_ids_rejected(profile: ClientProfile) -> None:
    data = _data(profile)
    data["unconfirmed_skills"].append(copy.deepcopy(data["unconfirmed_skills"][0]))
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


def test_match_keywords_uses_english_plus_approved_only(profile: ClientProfile) -> None:
    mapping = profile.match_keywords()
    assert set(mapping) == {
        "engineering",
        "architectural_interior",
        "presentation_design",
        "cv_resume",
    }
    for track in profile.tracks:
        approved_ar = [
            item.text for item in track.keywords_ar_proposed if item.approved
        ]
        expected = track.keywords_en + approved_ar
        assert mapping[track.id] == expected


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="top-level mapping"):
        load_profile(bad)


def test_get_track_by_id(profile: ClientProfile) -> None:
    assert profile.track("cv_resume").experience_years == 8
    with pytest.raises(KeyError):
        profile.track("nope")


# --- proposed tracks -----------------------------------------------------------


def test_proposed_tracks_are_not_used_by_matching(profile: ClientProfile) -> None:
    assert [track.id for track in profile.proposed_tracks] == ["architectural_cad_boq"]
    assert all(track.approved is False for track in profile.proposed_tracks)
    assert profile.approved_proposed_tracks == []
    assert "architectural_cad_boq" not in profile.match_keywords()


def test_approving_a_proposed_track_flips_the_property(profile: ClientProfile) -> None:
    data = _data(profile)
    data["proposed_tracks"][0]["approved"] = True
    other = ClientProfile.model_validate(data)
    assert other.approved_proposed_tracks == ["architectural_cad_boq"]


def test_proposed_track_id_cannot_clash_with_active_track(profile: ClientProfile) -> None:
    data = _data(profile)
    data["proposed_tracks"][0]["id"] = data["tracks"][0]["id"]
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


# --- out-of-scope domains ------------------------------------------------------


def test_out_of_scope_domains_are_soft_and_unapproved(profile: ClientProfile) -> None:
    ids = [domain.id for domain in profile.out_of_scope_domains]
    assert ids == [
        "software_web_app_development",
        "graphic_design_logos_branding",
        "fine_art_freehand_drawing",
        "general_writing_translation_marketing",
        "weapons_firearms",
    ]
    assert all(domain.approved is False for domain in profile.out_of_scope_domains)
    assert profile.active_out_of_scope_domains == []


def test_duplicate_out_of_scope_domain_ids_rejected(profile: ClientProfile) -> None:
    data = _data(profile)
    data["out_of_scope_domains"].append(copy.deepcopy(data["out_of_scope_domains"][0]))
    with pytest.raises(ValidationError):
        ClientProfile.model_validate(data)


# --- exact Arabic keyword lists (the list sent to the client) -------------------


def test_arabic_keyword_lists_are_exactly_as_proposed(profile: ClientProfile) -> None:
    expected = {
        "engineering": [
            "تصميم أنظمة مكافحة الحريق",
            "تصميم أنظمة إنذار الحريق",
            "مخططات مكافحة الحريق",
            "مخططات إنذار الحريق",
            "مكافحة حريق",
            "إنذار حريق",
            "رش آلي",
            "شبكة إطفاء",
            "مضخات حريق",
            "حسابات هيدروليكية",
            "أنظمة إطفاء الغاز",
            "FM200",
            "شوب دروينج ميكانيكا",
            "شوب دروينج فاير فايتنج",
            "شوب دروينج",
            "تصميم أنظمة التكييف HVAC",
            "تصميم أنظمة التهوية",
            "تصميم أنظمة MEP",
            "تكييف وتهوية",
            "HVAC",
            "أعمال صحية",
            "رسم مخططات ميكانيكية",
            "رسم هندسي أوتوكاد",
            "مخططات تنفيذية",
            "رسومات تنفيذية",
            "حصر كميات BOQ",
            "حساب كميات هندسية",
            "حصر كميات",
            "إعداد تقارير هندسية",
            "مراجعة المخططات الهندسية",
            "رسومات مطابقة للواقع",
            "رسومات As Built",
            "مخططات ميكانيكية",
            "أوتوكاد",
            "مقايسة",
        ],
        "architectural_interior": [
            "تصميم معماري",
            "مخططات معمارية",
            "تصميم داخلي",
            "تصميم ديكور",
            "ديكور داخلي",
            "تصميم واجهات",
            "تصميم فلل",
            "توزيع فراغات",
            "مخططات تنفيذية",
            "تصميم ثلاثي الأبعاد",
            "رندر معماري",
            "تصميم داخلي 3D",
            "تصميم خارجي 3D",
            "موديل 3D",
            "نمذجة ثلاثية الأبعاد",
            "إظهار معماري",
            "لقطات منظور ثلاثي الأبعاد",
            "تصميم سكني",
            "تصميم تجاري",
            "تصميم مطابخ",
            "تصميم أثاث",
        ],
        "presentation_design": [
            "تصميم عروض تقديمية",
            "تصميم عرض تقديمي",
            "تصميم بوربوينت",
            "تصميم برزنتيشن",
            "عروض تقديمية احترافية",
            "عرض تقديمي احترافي",
            "تصميم عروض شركات",
            "تصميم عروض أعمال",
            "تصميم عروض استثمارية",
            "تصميم بروفايل شركات",
            "بروفايل شركة",
            "الملف التعريفي للشركة",
            "تصميم Pitch Deck",
            "Pitch Deck",
            "Business Deck",
            "بوربوينت",
            "باور بوينت",
            "عرض استثماري",
            "إعادة تصميم عروض تقديمية",
            "إعادة تصميم سلايدات",
            "تحويل PDF إلى PowerPoint",
            "تحويل ملفات إلى بوربوينت",
            "تصميم عروض تدريبية",
            "تصميم عروض تسويقية",
            "عروض تسويقية",
            "تصميم عروض تفاعلية",
            "تنسيق عروض",
            "سلايدات احترافية",
            "عرض احترافي",
        ],
        "cv_resume": [
            "كتابة سيرة ذاتية",
            "تصميم سيرة ذاتية",
            "سيرة ذاتية ATS",
            "سيرة ذاتية",
            "سيرة ذاتية احترافية",
            "CV احترافي",
            "تطوير السيرة الذاتية",
            "تحسين السيرة الذاتية",
            "سيرة ذاتية متوافقة مع ATS",
            "توافق ATS",
            "خطاب تغطية",
            "خطاب تقديم",
            "تحسين LinkedIn",
            "سيرة ذاتية باللغة الإنجليزية",
            "سيرة ذاتية باللغة العربية",
            "سيرة ذاتية أوروبية Europass",
            "كتابة CV",
            "تصميم CV",
            "تعديل سيرة ذاتية",
            "صياغة سيرة ذاتية",
            "حساب لينكد إن",
            "لينكدإن",
        ],
    }
    for track in profile.tracks:
        assert [item.text for item in track.keywords_ar_proposed] == expected[track.id]
