"""Dashboard UI and REST API for AI Technical Proposal Generator."""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import time
from datetime import UTC
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from resova.config.profile import load_profile
from resova.config.settings import Settings
from resova.db.models import (
    AutoProposalJob,
    AutoProposalJobStatus,
    Platform,
    Project,
    ProjectAnalysis,
    ProposalDraft,
    ProposalEditorStatus,
    TelegramUser,
    TelegramUserStatus,
)
from resova.db.repositories import TelegramUserRepository
from resova.db.session import create_session_factory
from resova.services.pdf_exporter import export_proposal_pdf
from resova.services.proposal_access import (
    create_proposal_access_token,
    proposal_access_rate_limiter,
    verify_proposal_access_token,
)
from resova.services.technical_proposal import (
    SECTION_KEYS,
    SECTION_LABELS_AR,
    ProposalSections,
    TechnicalProposalService,
    UserProfessionalProfileData,
)
from resova.telegram.bot import TelegramNotifier
from resova.utils.errors import AIError, TelegramError

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "resova_session"
PROPOSAL_SCOPE_COOKIE_NAME = "resova_proposal_scope"

router = APIRouter()


# ---------------------------------------------------------------------------
# Session & Auth Helpers (HMAC-SHA256 signed cookies tied to TelegramUser)
# ---------------------------------------------------------------------------


def _signing_key(settings: Settings) -> bytes:
    token = (
        settings.telegram_bot_token.get_secret_value()
        if settings.telegram_bot_token is not None
        else "resova-local-dev-key"
    )
    return hashlib.sha256(f"resova-dashboard:{token}".encode()).digest()


def compute_access_code(chat_id: int, settings: Settings) -> str:
    """Compute a deterministic HMAC access code for a Telegram user."""
    digest = hmac.new(
        _signing_key(settings),
        f"login:{chat_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:16]


def sign_session_value(user_id: int, chat_id: int, settings: Settings) -> str:
    payload = f"{user_id}:{chat_id}"
    sig = hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_value(cookie_val: str | None, settings: Settings) -> tuple[int, int] | None:
    if not cookie_val:
        return None
    parts = cookie_val.split(":")
    if len(parts) != 3:
        return None
    user_id_str, chat_id_str, sig = parts
    try:
        user_id = int(user_id_str)
        chat_id = int(chat_id_str)
    except ValueError:
        return None
    expected = hmac.new(
        _signing_key(settings),
        f"{user_id}:{chat_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id, chat_id


def get_db(request: Request):
    """Yield a SQLAlchemy Session bound to the app engine."""
    factory = create_session_factory(request.app.state.engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DbSession,
) -> TelegramUser:
    """Resolve and authorize the logged-in active TelegramUser."""
    settings: Settings = request.app.state.settings
    parsed = verify_session_value(request.cookies.get(SESSION_COOKIE_NAME), settings)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    user_id, chat_id = parsed
    user = db.query(TelegramUser).filter_by(id=user_id, telegram_chat_id=chat_id).first()
    if user is None or user.status != TelegramUserStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive or unauthorized",
        )
    return user


CurrentUser = Annotated[TelegramUser, Depends(get_current_user)]


def _try_get_dashboard_user(request: Request, db: Session) -> TelegramUser | None:
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def sign_proposal_scope_value(
    user_id: int,
    chat_id: int,
    proposal_id: int,
    exp_ts: int,
    settings: Settings,
) -> str:
    payload = f"{user_id}:{chat_id}:{proposal_id}:{exp_ts}"
    sig = hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_proposal_scope_value(
    cookie_val: str | None,
    settings: Settings,
) -> tuple[int, int, int, int] | None:
    if not cookie_val:
        return None
    parts = cookie_val.split(":")
    if len(parts) != 5:
        return None
    user_id_str, chat_id_str, proposal_id_str, exp_ts_str, sig = parts
    try:
        user_id = int(user_id_str)
        chat_id = int(chat_id_str)
        proposal_id = int(proposal_id_str)
        exp_ts = int(exp_ts_str)
    except ValueError:
        return None
    expected = hmac.new(
        _signing_key(settings),
        f"{user_id}:{chat_id}:{proposal_id}:{exp_ts}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if exp_ts <= int(time.time()):
        return None
    return user_id, chat_id, proposal_id, exp_ts


def _resolve_proposal_scoped_user(
    request: Request,
    db: Session,
) -> tuple[TelegramUser, int] | None:
    settings: Settings = request.app.state.settings
    parsed = verify_proposal_scope_value(
        request.cookies.get(PROPOSAL_SCOPE_COOKIE_NAME), settings
    )
    if parsed is None:
        return None
    user_id, chat_id, proposal_id, _ = parsed
    user = db.query(TelegramUser).filter_by(id=user_id, telegram_chat_id=chat_id).first()
    if user is None or user.status != TelegramUserStatus.active:
        return None
    return user, proposal_id


def get_proposal_user(
    draft_id: int,
    request: Request,
    db: DbSession,
) -> TelegramUser:
    """Authorize either a full dashboard user or a proposal-scoped access session."""
    full_user = _try_get_dashboard_user(request, db)
    if full_user is not None:
        return full_user

    scoped = _resolve_proposal_scoped_user(request, db)
    if scoped is not None:
        user, scoped_proposal_id = scoped
        if scoped_proposal_id != draft_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access token is scoped to a different proposal",
            )
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


ProposalUser = Annotated[TelegramUser, Depends(get_proposal_user)]


# ---------------------------------------------------------------------------
# Pydantic Request Schemas
# ---------------------------------------------------------------------------


class GenerateProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_ai: bool = False
    language: str = Field(default="ar", pattern="^(ar|en)$")


class UpdateProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=512)
    sections: ProposalSections
    timeline_text: str | None = Field(default=None, max_length=512)
    cost_text: str | None = Field(default=None, max_length=256)
    editor_status: ProposalEditorStatus | None = None


class RegenerateSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_key: str
    require_ai: bool = False


class RegenerateFullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_ai: bool = False


def _serialize_draft(
    draft: ProposalDraft, svc: TechnicalProposalService
) -> dict[str, Any]:
    sections = svc.parse_sections(draft)
    return {
        "id": draft.id,
        "project_id": draft.project_id,
        "owner_user_id": draft.owner_user_id,
        "title": draft.title,
        "sections": sections.to_dict(),
        "content": draft.content,
        "timeline_text": draft.timeline_text,
        "cost_text": draft.cost_text,
        "editor_status": draft.editor_status.value,
        "is_ai_generated": draft.is_ai_generated,
        "ai_model": draft.ai_model,
        "version": draft.version,
        "generated_at": draft.generated_at.isoformat() if draft.generated_at else None,
    }


# ---------------------------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/profile/professional")
def api_get_professional_profile(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    prof = svc.get_professional_profile(current_user.id)
    if prof is None:
        return UserProfessionalProfileData().model_dump()
    return {
        "professional_name": prof.professional_name,
        "specialization": prof.specialization,
        "skills_services": prof.skills_services,
        "portfolio_samples": prof.portfolio_samples,
        "tools_software": prof.tools_software,
        "communication_style": prof.communication_style,
    }


@router.put("/api/profile/professional")
def api_update_professional_profile(
    payload: UserProfessionalProfileData,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    prof = svc.upsert_professional_profile(current_user.id, payload)
    return {
        "status": "saved",
        "professional_name": prof.professional_name,
        "specialization": prof.specialization,
        "skills_services": prof.skills_services,
        "portfolio_samples": prof.portfolio_samples,
        "tools_software": prof.tools_software,
        "communication_style": prof.communication_style,
    }


@router.post(
    "/api/projects/{project_id}/technical-proposals",
    status_code=status.HTTP_201_CREATED,
)
def api_generate_technical_proposal(
    project_id: int,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    payload: GenerateProposalRequest | None = None,
) -> dict[str, Any]:
    body = payload or GenerateProposalRequest()
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.generate_technical_proposal(
            project_id=project_id,
            owner_user_id=current_user.id,
            require_ai=body.require_ai,
            language=body.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    db.commit()
    db.refresh(draft)

    settings: Settings = request.app.state.settings
    telegram_notified = False
    telegram_warning: str | None = None

    chat_id = current_user.telegram_chat_id
    if not chat_id or chat_id <= 0:
        telegram_warning = "لم يتم إرسال إشعار Telegram لعدم وجود ربط مع حساب Telegram."
    elif not settings.enable_telegram or settings.telegram_bot_token is None:
        telegram_warning = "تم حفظ المسودة بنجاح، وإشعارات Telegram غير مفعّلة حاليًا."
    else:
        project = db.query(Project).filter_by(id=draft.project_id).first()
        project_title = project.title if project else f"مشروع #{draft.project_id}"
        proposal_title = draft.title or f"عرض فني v{draft.version}"
        base_url = settings.public_base_url.rstrip("/")
        raw_token, _ = create_proposal_access_token(
            db,
            proposal_id=draft.id,
            owner_user_id=current_user.id,
            settings=settings,
        )
        db.commit()
        proposal_url = f"{base_url}/proposal-access/{raw_token}"
        try:
            notifier = TelegramNotifier(settings)
            notifier.send_proposal_created_notification(
                chat_id=chat_id,
                draft_id=draft.id,
                project_title=project_title,
                proposal_title=proposal_title,
                proposal_url=proposal_url,
            )
            telegram_notified = True
        except (TelegramError, Exception) as exc:
            logger.warning(
                "Proposal %d saved but Telegram notification failed for user %d: %s",
                draft.id,
                current_user.id,
                exc,
            )
            telegram_warning = "تم حفظ المسودة بنجاح، لكن تعذر إرسال إشعار Telegram حاليًا."

    result = _serialize_draft(draft, svc)
    result["telegram_notified"] = telegram_notified
    result["telegram_warning"] = telegram_warning
    return result


@router.get("/api/projects/{project_id}/technical-proposals")
def api_list_project_proposals(
    project_id: int,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> list[dict[str, Any]]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    drafts = svc.list_project_proposals(project_id, current_user.id)
    return [_serialize_draft(d, svc) for d in drafts]


@router.get("/api/proposals/{draft_id}")
def api_get_proposal(
    draft_id: int,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
) -> dict[str, Any]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.get_proposal(draft_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _serialize_draft(draft, svc)


@router.put("/api/proposals/{draft_id}")
def api_update_proposal(
    draft_id: int,
    payload: UpdateProposalRequest,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
) -> dict[str, Any]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.update_proposal(
            draft_id=draft_id,
            owner_user_id=current_user.id,
            sections=payload.sections,
            title=payload.title,
            timeline_text=payload.timeline_text,
            cost_text=payload.cost_text,
            editor_status=payload.editor_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _serialize_draft(draft, svc)


@router.post("/api/proposals/{draft_id}/regenerate-section")
def api_regenerate_section(
    draft_id: int,
    payload: RegenerateSectionRequest,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
) -> dict[str, Any]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.regenerate_section(
            draft_id=draft_id,
            owner_user_id=current_user.id,
            section_key=payload.section_key,
            require_ai=payload.require_ai,
        )
    except ValueError as exc:
        code = (
            status.HTTP_400_BAD_REQUEST
            if "Invalid section" in str(exc)
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _serialize_draft(draft, svc)


@router.post("/api/proposals/{draft_id}/regenerate")
def api_regenerate_full(
    draft_id: int,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
    payload: RegenerateFullRequest | None = None,
) -> dict[str, Any]:
    body = payload or RegenerateFullRequest()
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.regenerate_full(
            draft_id=draft_id,
            owner_user_id=current_user.id,
            require_ai=body.require_ai,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _serialize_draft(draft, svc)


@router.post("/api/proposals/{draft_id}/duplicate", status_code=status.HTTP_201_CREATED)
def api_duplicate_proposal(
    draft_id: int,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
) -> dict[str, Any]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        copy_draft = svc.duplicate_proposal(draft_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _serialize_draft(copy_draft, svc)


@router.delete("/api/proposals/{draft_id}", status_code=status.HTTP_200_OK)
def api_delete_proposal(
    draft_id: int,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
) -> dict[str, str]:
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        svc.delete_proposal(draft_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.get("/api/proposals/{draft_id}/pdf")
def api_export_proposal_pdf(
    draft_id: int,
    request: Request,
    db: DbSession,
    current_user: ProposalUser,
) -> Response:
    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.get_proposal(draft_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    project = db.query(Project).filter_by(id=draft.project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    sections = svc.parse_sections(draft)
    prof = svc.get_professional_profile(current_user.id)
    pdf_bytes = export_proposal_pdf(draft, project, sections, prof)

    filename = f"proposal-{draft.id}-v{draft.version}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Dashboard HTML Pages (Arabic RTL)
# ---------------------------------------------------------------------------


def _page_shell(
    title: str,
    body: str,
    user: TelegramUser | None = None,
    *,
    scoped_mode: bool = False,
) -> HTMLResponse:
    nav = ""
    if user is not None:
        user_label = html.escape(user.first_name or str(user.telegram_chat_id))
        if scoped_mode:
            nav = f"""
        <nav class="topbar">
          <div class="brand">Resova | مولّد العروض الفنية</div>
          <div class="nav-links">
            <span class="user-badge">{user_label}</span>
          </div>
        </nav>
        """
        else:
            nav = f"""
        <nav class="topbar">
          <div class="brand">Resova | مولّد العروض الفنية</div>
          <div class="nav-links">
            <a href="/dashboard">المشاريع</a>
            <a href="/dashboard/profile">ملفي المهني</a>
            <span class="user-badge">{user_label}</span>
            <form method="post" action="/dashboard/logout" style="display:inline;">
              <button type="submit" class="btn-link">خروج</button>
            </form>
          </div>
        </nav>
        """
    doc = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)} - Resova</title>
<style>
  :root {{ --bg:#f8fafc; --card:#ffffff; --ink:#0f172a; --muted:#475569;
           --primary:#2563eb; --primary-d:#1d4ed8; --border:#e2e8f0;
           --danger:#dc2626; --ok:#16a34a; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: system-ui, -apple-system, Tahoma, Arial, sans-serif;
          background:var(--bg); color:var(--ink); line-height:1.6; }}
  .topbar {{ background:#0f172a; color:#fff; padding:12px 24px;
             display:flex; justify-content:space-between; align-items:center;
             flex-wrap:wrap; gap:12px; }}
  .brand {{ font-weight:700; font-size:1.1rem; }}
  .nav-links {{ display:flex; align-items:center; gap:16px; }}
  .nav-links a {{ color:#cbd5e1; text-decoration:none; font-weight:500; }}
  .nav-links a:hover {{ color:#fff; }}
  .user-badge {{ background:#1e293b; padding:4px 10px; border-radius:6px; font-size:0.85rem; }}
  .container {{ max-width:1050px; margin:24px auto; padding:0 16px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
           padding:20px; margin-bottom:16px; box-shadow:0 1px 2px rgba(0,0,0,0.03); }}
  .btn {{ display:inline-block; background:var(--primary); color:#fff; border:none;
          padding:10px 18px; border-radius:8px; cursor:pointer; font-size:0.95rem;
          text-decoration:none; font-weight:600; }}
  .btn:hover {{ background:var(--primary-d); }}
  .btn-secondary {{ background:#475569; }}
  .btn-secondary:hover {{ background:#334155; }}
  .btn-danger {{ background:var(--danger); }}
  .btn-ok {{ background:var(--ok); }}
  .btn-sm {{ padding:6px 12px; font-size:0.85rem; }}
  .btn-link {{ background:none; border:none; color:#fca5a5; cursor:pointer; font-size:0.9rem; }}
  .badge {{ display:inline-block; padding:3px 9px; border-radius:999px; font-size:0.8rem;
            background:#e2e8f0; color:#1e293b; font-weight:600; }}
  .badge-strong {{ background:#dcfce7; color:#166534; }}
  .badge-good {{ background:#dbeafe; color:#1e40af; }}
  label {{ display:block; font-weight:600; margin-bottom:6px; margin-top:12px; }}
  input[type="text"], input[type="number"], input[type="password"], select, textarea {{
    width:100%; padding:10px 12px; border:1px solid #cbd5e1; border-radius:8px;
    font-family:inherit; font-size:0.95rem; background:#fff;
  }}
  textarea {{ min-height:110px; resize:vertical; }}
  .alert {{ padding:12px 16px; border-radius:8px; margin-bottom:16px; display:none; }}
  .alert-err {{ background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; }}
  .alert-ok {{ background:#dcfce7; color:#166534; border:1px solid #86efac; }}
  .toolbar {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:16px 0; }}
  .section-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
  .preview-box {{ background:#f1f5f9; border:1px solid #cbd5e1; border-radius:8px;
                  padding:20px; display:none; margin-top:16px; white-space:pre-wrap; }}
</style>
</head>
<body>
{nav}
<div class="container">
{body}
</div>
</body>
</html>"""
    return HTMLResponse(content=doc)


@router.get("/dashboard/login", response_class=HTMLResponse)
def dashboard_login_page(request: Request) -> HTMLResponse:
    return _page_shell(
        "تسجيل الدخول",
        """
        <div class="card" style="max-width:460px; margin:48px auto;">
          <h2>تسجيل الدخول إلى لوحة تحكم Resova</h2>
          <p style="color:var(--muted);">أدخل معرّف Telegram ورمز الوصول الخاص بحسابك.</p>
          <form method="post" action="/dashboard/login">
            <label>معرّف Telegram (Chat ID)</label>
            <input type="number" name="chat_id" required />
            <label>رمز الوصول (Access Code)</label>
            <input type="password" name="access_code" required />
            <div style="margin-top:16px;">
              <button type="submit" class="btn">دخول</button>
            </div>
          </form>
        </div>
        """,
    )


@router.post("/dashboard/login")
async def dashboard_login_submit(
    request: Request,
    db: DbSession,
) -> Response:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body)
    try:
        chat_id = int((parsed.get("chat_id") or [""])[0].strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="معرّف الدردشة غير صالح",
        ) from exc
    access_code = (parsed.get("access_code") or [""])[0]
    if not access_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="رمز الوصول مطلوب",
        )
    settings: Settings = request.app.state.settings
    expected_code = compute_access_code(chat_id, settings)
    bot_token = (
        settings.telegram_bot_token.get_secret_value()
        if settings.telegram_bot_token is not None
        else None
    )
    owner_ok = (
        settings.owner_chat_id is not None
        and chat_id == settings.owner_chat_id
        and bot_token is not None
        and hmac.compare_digest(access_code, bot_token)
    )
    if not (hmac.compare_digest(access_code, expected_code) or owner_ok):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "بيانات الدخول غير صحيحة"},
        )

    repo = TelegramUserRepository(db)
    user = repo.get_by_chat_id(chat_id)
    if user is None and settings.owner_chat_id is not None and chat_id == settings.owner_chat_id:
        user = repo.ensure_owner(chat_id)
    if user is None or user.status != TelegramUserStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="الحساب غير مفعّل للوصول إلى لوحة التحكم",
        )

    cookie_val = sign_session_value(user.id, user.telegram_chat_id, settings)
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_val,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
    )
    return resp


@router.post("/dashboard/logout")
def dashboard_logout() -> RedirectResponse:
    resp = RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    resp.delete_cookie(PROPOSAL_SCOPE_COOKIE_NAME)
    return resp


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_projects_page(
    request: Request, db: DbSession
) -> Response:
    user = _try_get_dashboard_user(request, db)
    if user is None:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)

    projects = (
        db.query(Project)
        .order_by(Project.first_seen_at.desc(), Project.id.desc())
        .limit(50)
        .all()
    )
    rows: list[str] = []
    for p in projects:
        latest = (
            db.query(ProjectAnalysis)
            .filter_by(project_id=p.id)
            .order_by(ProjectAnalysis.analyzed_at.desc(), ProjectAnalysis.id.desc())
            .first()
        )
        if latest:
            track_txt = html.escape(latest.track or "-")
            score_badge = (
                f'<span class="badge badge-strong">{latest.score}% ({track_txt})</span>'
            )
        else:
            score_badge = '<span class="badge">غير محلل</span>'
        rows.append(
            f"""
            <div class="card">
              <div style="display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;">
                <div>
                  <h3 style="margin:0 0 6px 0;">
                    <a href="/dashboard/projects/{p.id}"
                       style="color:var(--ink); text-decoration:none;">
                      {html.escape(p.title)}
                    </a>
                  </h3>
                  <div style="color:var(--muted); font-size:0.9rem;">
                    المنصة: {html.escape(p.platform.value)} | {score_badge}
                  </div>
                </div>
                <div>
                  <a class="btn btn-sm" href="/dashboard/projects/{p.id}">عرض التفاصيل</a>
                </div>
              </div>
            </div>
            """
        )
    content = "".join(rows) if rows else '<div class="card">لا توجد مشاريع مرصودة بعد.</div>'
    return _page_shell("المشاريع المرصودة", f"<h1>المشاريع المرصودة</h1>{content}", user)


@router.get("/dashboard/projects/{project_id}", response_class=HTMLResponse)
def dashboard_project_detail_page(
    project_id: int,
    request: Request,
    db: DbSession,
) -> Response:
    user = _try_get_dashboard_user(request, db)
    if user is None:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)

    project = db.query(Project).filter_by(id=project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="المشروع غير موجود")

    analysis = (
        db.query(ProjectAnalysis)
        .filter_by(project_id=project.id)
        .order_by(ProjectAnalysis.analyzed_at.desc(), ProjectAnalysis.id.desc())
        .first()
    )
    svc = TechnicalProposalService(db, request.app.state.settings)
    drafts = svc.list_project_proposals(project.id, user.id)
    auto_job = (
        db.query(AutoProposalJob)
        .filter_by(project_id=project.id, owner_user_id=user.id)
        .first()
    )
    min_score = load_profile(request.app.state.settings.profile_path).settings.min_match_score
    if auto_job is not None:
        if auto_job.status in (
            AutoProposalJobStatus.pending,
            AutoProposalJobStatus.processing,
        ):
            auto_status_html = (
                '<div class="card"><strong>حالة العرض التلقائي:</strong> '
                '<span class="badge">⏳ جارٍ إنشاء العرض...</span></div>'
            )
        elif auto_job.status == AutoProposalJobStatus.generated and auto_job.draft_id:
            auto_status_html = (
                '<div class="card" style="display:flex; justify-content:space-between; '
                'align-items:center;">'
                '<div><strong>حالة العرض التلقائي:</strong> '
                '<span class="badge badge-strong">✅ تم إنشاء العرض</span></div>'
                f'<a class="btn btn-sm" href="/dashboard/proposals/{auto_job.draft_id}">'
                "فتح العرض التلقائي</a></div>"
            )
        elif auto_job.status == AutoProposalJobStatus.failed:
            err_txt = html.escape(auto_job.last_error or "خطأ غير معروف")
            auto_status_html = (
                '<div class="card"><strong>حالة العرض التلقائي:</strong> '
                f'<span class="badge">⚠️ تعذر الإنشاء: {err_txt}</span></div>'
            )
        else:
            auto_status_html = ""
    elif project.platform != Platform.mostaql:
        auto_status_html = (
            '<div class="card"><strong>حالة العرض التلقائي:</strong> '
            '<span class="badge">ℹ️ مفعّل لمشاريع مستقل المطابقة فقط</span></div>'
        )
    elif analysis is not None and analysis.score < min_score:
        auto_status_html = (
            '<div class="card"><strong>حالة العرض التلقائي:</strong> '
            f'<span class="badge">ℹ️ لا توجد مطابقة كافية (أقل من {min_score}%)</span></div>'
        )
    else:
        auto_status_html = ""

    analysis_html = (
        f"""
        <div class="card">
          <h3>نتائج تحليل الذكاء الاصطناعي</h3>
          <p><strong>التطابق:</strong> {analysis.score}% |
             <strong>المسار:</strong> {html.escape(analysis.track or 'غير محدد')}</p>
          <p><strong>الملخص:</strong> {html.escape(analysis.summary or '—')}</p>
          <p><strong>التقييم:</strong> {html.escape(analysis.rationale or '—')}</p>
        </div>
        """
        if analysis
        else '<div class="card"><p>لم يتم تحليل هذا المشروع بعد.</p></div>'
    )

    draft_links = "".join(
        f"""
        <div style="padding:10px 0; border-bottom:1px solid var(--border);
                    display:flex; justify-content:space-between; align-items:center;">
          <div>
            <strong>{html.escape(d.title or f'مسودة v{d.version}')}</strong>
            <span class="badge">{html.escape(d.editor_status.value)}</span>
          </div>
          <a class="btn btn-sm" href="/dashboard/proposals/{d.id}">فتح المحرر</a>
        </div>
        """
        for d in drafts
    ) or "<p style='color:var(--muted);'>لا توجد مسودات عروض فنية لهذا المشروع بعد.</p>"

    cat_badge = (
        f'<span class="badge">{html.escape(project.category)}</span>' if project.category else ""
    )
    desc_html = html.escape(project.description or "لا يوجد وصف متاح للمشروع.")
    body = f"""
    <div id="err-box" class="alert alert-err"></div>
    <div class="card">
      <div style="display:flex; justify-content:space-between;
                  align-items:flex-start; flex-wrap:wrap; gap:12px;">
        <div>
          <h1 style="margin:0 0 8px 0;">{html.escape(project.title)}</h1>
          <span class="badge">{html.escape(project.platform.value)}</span>
          {cat_badge}
        </div>
        <button id="btn-generate-proposal" class="btn" onclick="generateProposal({project.id})">
          إنشاء عرض فني بالذكاء الاصطناعي
        </button>
      </div>
      <hr style="border:none; border-top:1px solid var(--border); margin:16px 0;" />
      <p style="white-space:pre-wrap;">{desc_html}</p>
    </div>
    {analysis_html}
    {auto_status_html}
    <div class="card">
      <h3>مسودات العروض الفنية الخاصة بك</h3>
      {draft_links}
    </div>
    <script>
    async function generateProposal(projectId) {{
      const btn = document.getElementById('btn-generate-proposal');
      const errBox = document.getElementById('err-box');
      errBox.style.display = 'none';
      btn.disabled = true;
      btn.textContent = 'جاري إنشاء العرض الفني...';
      try {{
        const res = await fetch(`/api/projects/${{projectId}}/technical-proposals`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ require_ai: false, language: 'ar' }})
        }});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'تعذر إنشاء العرض الفني');
        const warnQ = data.telegram_warning
          ? `?tg_warn=${{encodeURIComponent(data.telegram_warning)}}`
          : '';
        window.location.href = `/dashboard/proposals/${{data.id}}${{warnQ}}`;
      }} catch (e) {{
        errBox.textContent = e.message;
        errBox.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'إنشاء عرض فني بالذكاء الاصطناعي';
      }}
    }}
    </script>
    """
    return _page_shell(project.title, body, user)


@router.get("/proposal-access/{token}")
def proposal_direct_access(
    token: str,
    request: Request,
    db: DbSession,
) -> Response:
    client_ip = request.client.host if request.client else "unknown"
    if not proposal_access_rate_limiter.allow(client_ip):
        resp = _page_shell(
            "طلبات كثيرة",
            """
            <div class="card" style="max-width:520px; margin:48px auto;">
              <h2>تم تجاوز الحد المسموح من المحاولات</h2>
              <p style="color:var(--muted);">يرجى الانتظار قليلًا ثم إعادة المحاولة.</p>
            </div>
            """,
        )
        resp.status_code = status.HTTP_429_TOO_MANY_REQUESTS
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    verified = verify_proposal_access_token(db, token)
    if verified is None:
        resp = _page_shell(
            "رابط غير صالح",
            """
            <div class="card" style="max-width:520px; margin:48px auto;">
              <h2>تعذر فتح العرض الفني</h2>
              <p style="color:var(--muted);">
                رابط الوصول المباشر غير صالح أو منتهي الصلاحية أو تم إلغاؤه.
                يمكنك فتح العرض من لوحة التحكم أو طلب إشعار جديد.
              </p>
              <a class="btn" href="/dashboard/login">تسجيل الدخول إلى لوحة التحكم</a>
            </div>
            """,
        )
        resp.status_code = status.HTTP_403_FORBIDDEN
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    record, draft, user = verified
    db.commit()

    settings: Settings = request.app.state.settings
    exp_ts = int(record.expires_at.replace(tzinfo=UTC).timestamp())
    scope_cookie = sign_proposal_scope_value(
        user.id,
        user.telegram_chat_id,
        draft.id,
        exp_ts,
        settings,
    )
    resp = RedirectResponse(
        url=f"/dashboard/proposals/{draft.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    resp.set_cookie(
        key=PROPOSAL_SCOPE_COOKIE_NAME,
        value=scope_cookie,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=max(60, exp_ts - int(time.time())),
    )
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/dashboard/proposals/{draft_id}", response_class=HTMLResponse)
def dashboard_proposal_editor_page(
    draft_id: int,
    request: Request,
    db: DbSession,
) -> Response:
    scoped_mode = False
    user = _try_get_dashboard_user(request, db)
    if user is None:
        scoped = _resolve_proposal_scoped_user(request, db)
        if scoped is None:
            return RedirectResponse(
                url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER
            )
        scoped_user, scoped_proposal_id = scoped
        if scoped_proposal_id != draft_id:
            err_resp = _page_shell(
                "غير مصرح",
                """
                <div class="card" style="max-width:520px; margin:48px auto;">
                  <h2>غير مصرح بالوصول</h2>
                  <p style="color:var(--muted);">
                    لا يمكنك استخدام رابط الوصول المباشر لفتح عرض فني آخر.
                  </p>
                </div>
                """,
                scoped_user,
                scoped_mode=True,
            )
            err_resp.status_code = status.HTTP_403_FORBIDDEN
            return err_resp
        user = scoped_user
        scoped_mode = True

    svc = TechnicalProposalService(db, request.app.state.settings)
    try:
        draft = svc.get_proposal(draft_id, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    project = db.query(Project).filter_by(id=draft.project_id).first()
    sections = svc.parse_sections(draft)

    sections_html = []
    for key in SECTION_KEYS:
        label = SECTION_LABELS_AR[key]
        val = html.escape(getattr(sections, key, ""))
        regen_btn = (
            f'<button type="button" class="btn btn-sm btn-secondary" '
            f'onclick="regenSection(\'{key}\')">إعادة توليد هذا القسم</button>'
            if key != "cost"
            else '<span style="font-size:0.8rem; color:var(--muted);">يحددها المستخدم يدويًا</span>'
        )
        sections_html.append(
            f"""
            <div class="card">
              <div class="section-head">
                <label for="sec-{key}" style="margin:0;">{html.escape(label)}</label>
                {regen_btn}
              </div>
              <textarea id="sec-{key}" data-section="{key}" style="margin-top:8px;">{val}</textarea>
            </div>
            """
        )

    status_opts = "".join(
        f'<option value="{s.value}" '
        f'{"selected" if draft.editor_status == s else ""}>{s.value.upper()}</option>'
        for s in ProposalEditorStatus
    )

    proj_title_html = html.escape(project.title if project else "")
    tg_warn_param = request.query_params.get("tg_warn")
    tg_warn_html = (
        f'<div class="alert alert-err" style="display:block;">{html.escape(tg_warn_param)}</div>'
        if tg_warn_param
        else ""
    )
    body = f"""
    {tg_warn_html}
    <div id="err-box" class="alert alert-err"></div>
    <div id="ok-box" class="alert alert-ok"></div>
    <div class="card">
      <div style="display:flex; justify-content:space-between;
                  align-items:center; flex-wrap:wrap; gap:10px;">
        <div>
          <h1 style="margin:0;">محرر العرض الفني</h1>
          <div style="color:var(--muted); font-size:0.9rem;">
            المشروع: <a href="/dashboard/projects/{draft.project_id}">{proj_title_html}</a>
            | الإصدار: v{draft.version}
          </div>
        </div>
        <div>
          <label for="editor-status" style="display:inline; margin-left:8px;">الحالة:</label>
          <select id="editor-status" style="width:auto; display:inline-block;">
            {status_opts}
          </select>
        </div>
      </div>

      <label for="prop-title">عنوان العرض</label>
      <input type="text" id="prop-title" value="{html.escape(draft.title or '')}" />

      <div class="toolbar">
        <button type="button" class="btn btn-ok" onclick="saveProposal()">حفظ المسودة</button>
        <button type="button" class="btn" onclick="regenFull()">إعادة إنشاء العرض بالكامل</button>
        <button type="button" class="btn btn-secondary" onclick="copyProposal()">
          نسخ العرض كاملًا
        </button>
        <button type="button" class="btn btn-secondary" onclick="togglePreview()">
          معاينة العرض
        </button>
        <a class="btn btn-secondary" href="/api/proposals/{draft.id}/pdf">تصدير PDF</a>
        <button type="button" class="btn btn-secondary" onclick="duplicateProposal()">
          إنشاء نسخة جديدة
        </button>
        <button type="button" class="btn btn-danger" onclick="deleteProposal()">حذف المسودة</button>
      </div>
      <div id="preview-box" class="preview-box"></div>
    </div>

    {''.join(sections_html)}

    <script>
    const DRAFT_ID = {draft.id};
    const SECTION_KEYS = {list(SECTION_KEYS)!r};
    const SECTION_LABELS = {SECTION_LABELS_AR!r};

    function showMsg(isErr, msg) {{
      const err = document.getElementById('err-box');
      const ok = document.getElementById('ok-box');
      err.style.display = isErr ? 'block' : 'none';
      ok.style.display = isErr ? 'none' : 'block';
      (isErr ? err : ok).textContent = msg;
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}

    function collectSections() {{
      const obj = {{}};
      for (const k of SECTION_KEYS) {{
        obj[k] = document.getElementById('sec-' + k).value;
      }}
      return obj;
    }}

    function applyDraft(data) {{
      document.getElementById('prop-title').value = data.title || '';
      document.getElementById('editor-status').value = data.editor_status || 'draft';
      for (const k of SECTION_KEYS) {{
        document.getElementById('sec-' + k).value = (data.sections && data.sections[k]) || '';
      }}
    }}

    async function saveProposal() {{
      try {{
        const sections = collectSections();
        const res = await fetch(`/api/proposals/${{DRAFT_ID}}`, {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            title: document.getElementById('prop-title').value,
            sections: sections,
            timeline_text: sections.timeline,
            cost_text: sections.cost,
            editor_status: document.getElementById('editor-status').value
          }})
        }});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'فشل الحفظ');
        applyDraft(data);
        showMsg(false, 'تم حفظ مسودة العرض الفني بنجاح.');
      }} catch (e) {{ showMsg(true, e.message); }}
    }}

    async function regenSection(key) {{
      try {{
        const res = await fetch(`/api/proposals/${{DRAFT_ID}}/regenerate-section`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ section_key: key, require_ai: false }})
        }});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'فشل إعادة توليد القسم');
        applyDraft(data);
        showMsg(false, 'تمت إعادة توليد القسم بنجاح.');
      }} catch (e) {{ showMsg(true, e.message); }}
    }}

    async function regenFull() {{
      try {{
        const res = await fetch(`/api/proposals/${{DRAFT_ID}}/regenerate`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ require_ai: false }})
        }});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'فشل إعادة إنشاء العرض');
        applyDraft(data);
        showMsg(false, 'تمت إعادة إنشاء العرض بالكامل.');
      }} catch (e) {{ showMsg(true, e.message); }}
    }}

    function buildFullText() {{
      const sec = collectSections();
      return SECTION_KEYS.map(k => SECTION_LABELS[k] + "\\n" + (sec[k] || "—")).join("\\n\\n");
    }}

    async function copyProposal() {{
      try {{
        await navigator.clipboard.writeText(buildFullText());
        showMsg(false, 'تم نسخ العرض الفني بالكامل إلى الحافظة.');
      }} catch (e) {{ showMsg(true, 'تعذر النسخ التلقائي؛ استخدم المعاينة للنسخ اليدوي.'); }}
    }}

    function togglePreview() {{
      const box = document.getElementById('preview-box');
      if (box.style.display === 'block') {{
        box.style.display = 'none';
      }} else {{
        box.textContent = buildFullText();
        box.style.display = 'block';
      }}
    }}

    async function duplicateProposal() {{
      try {{
        const res = await fetch(`/api/proposals/${{DRAFT_ID}}/duplicate`, {{ method: 'POST' }});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'فشل إنشاء نسخة');
        window.location.href = `/dashboard/proposals/${{data.id}}`;
      }} catch (e) {{ showMsg(true, e.message); }}
    }}

    async function deleteProposal() {{
      if (!confirm('هل أنت متأكد من حذف هذه المسودة؟')) return;
      try {{
        const res = await fetch(`/api/proposals/${{DRAFT_ID}}`, {{ method: 'DELETE' }});
        if (!res.ok) throw new Error('تعذر حذف المسودة');
        window.location.href = `/dashboard/projects/{draft.project_id}`;
      }} catch (e) {{ showMsg(true, e.message); }}
    }}
    </script>
    """
    return _page_shell("محرر العرض الفني", body, user, scoped_mode=scoped_mode)


@router.get("/dashboard/profile", response_class=HTMLResponse)
def dashboard_profile_page(
    request: Request,
    db: DbSession,
) -> Response:
    user = _try_get_dashboard_user(request, db)
    if user is None:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)

    svc = TechnicalProposalService(db, request.app.state.settings)
    prof = svc.get_professional_profile(user.id)

    def _v(attr: str) -> str:
        return html.escape(getattr(prof, attr, None) or "") if prof else ""

    body = f"""
    <div id="err-box" class="alert alert-err"></div>
    <div id="ok-box" class="alert alert-ok"></div>
    <div class="card">
      <h1>ملفي المهني لتخصيص العروض الفنية</h1>
      <p style="color:var(--muted);">
        تُستخدم هذه البيانات فقط إذا قمت بإدخالها، ولن يقوم النظام باختراع أي معلومات غير موجودة.
      </p>
      <label>الاسم المهني</label>
      <input type="text" id="p-name" value="{_v('professional_name')}" />
      <label>التخصص والخبرات</label>
      <textarea id="p-spec">{_v('specialization')}</textarea>
      <label>المهارات والخدمات</label>
      <textarea id="p-skills">{_v('skills_services')}</textarea>
      <label>البرامج والأدوات التي تجيدها</label>
      <textarea id="p-tools">{_v('tools_software')}</textarea>
      <label>نماذج الأعمال السابقة</label>
      <textarea id="p-port">{_v('portfolio_samples')}</textarea>
      <label>أسلوب التواصل المفضل</label>
      <input type="text" id="p-comm" value="{_v('communication_style')}" />
      <div style="margin-top:16px;">
        <button type="button" class="btn btn-ok" onclick="saveProfile()">حفظ الملف المهني</button>
      </div>
    </div>
    <script>
    async function saveProfile() {{
      const err = document.getElementById('err-box');
      const ok = document.getElementById('ok-box');
      try {{
        const res = await fetch('/api/profile/professional', {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            professional_name: document.getElementById('p-name').value,
            specialization: document.getElementById('p-spec').value,
            skills_services: document.getElementById('p-skills').value,
            tools_software: document.getElementById('p-tools').value,
            portfolio_samples: document.getElementById('p-port').value,
            communication_style: document.getElementById('p-comm').value
          }})
        }});
        if (!res.ok) throw new Error('تعذر حفظ البيانات');
        err.style.display = 'none';
        ok.textContent = 'تم حفظ بيانات الملف المهني بنجاح.';
        ok.style.display = 'block';
      }} catch (e) {{
        ok.style.display = 'none';
        err.textContent = e.message;
        err.style.display = 'block';
      }}
    }}
    </script>
    """
    return _page_shell("ملفي المهني", body, user)
