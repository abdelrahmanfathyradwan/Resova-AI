"""Professional Arabic RTL & English PDF exporter for Technical Proposals."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

from resova.db.models import Project, ProposalDraft, UserProfessionalProfile
from resova.services.technical_proposal import (
    SECTION_KEYS,
    SECTION_LABELS_AR,
    ProposalSections,
)

# Candidate Arabic-capable TrueType fonts across Windows & Linux production containers
_FONT_CANDIDATES: tuple[tuple[str, str | None], ...] = (
    # Windows standard fonts (native Arabic + Latin)
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    # Linux Debian/Ubuntu fonts (installed via fonts-dejavu-core / fonts-noto-core)
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ),
)


def _resolve_font_pair() -> tuple[str, str | None]:
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).is_file():
            bold_path = bold if (bold and Path(bold).is_file()) else None
            return regular, bold_path
    raise FileNotFoundError(
        "No Arabic-capable TrueType font found on system for PDF generation."
    )


class _ProposalPDF(FPDF):
    """Custom FPDF document with clean header/footer and page numbering."""

    def __init__(self, project_title: str, font_family: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._project_title = project_title
        self._font_family = font_family
        self.set_auto_page_break(auto=True, margin=18)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font(self._font_family, "", 8)
        self.set_text_color(110, 118, 129)
        self.set_text_shaping(
            use_shaping_engine=True,
            direction="rtl",
            script="arab",
            language="ara",
        )
        self.cell(
            0,
            8,
            f"صفحة {self.page_no()} من {{nb}}  |  Resova Technical Proposal",
            align="C",
        )


def export_proposal_pdf(
    draft: ProposalDraft,
    project: Project,
    sections: ProposalSections,
    prof_profile: UserProfessionalProfile | None = None,
) -> bytes:
    """Render a real, printable, Arabic RTL + English PDF for a technical proposal."""
    regular_font, bold_font = _resolve_font_pair()
    font_family = "ProposalFont"

    pdf = _ProposalPDF(project.title, font_family)
    pdf.alias_nb_pages()
    pdf.add_font(font_family, "", regular_font)
    pdf.add_font(font_family, "B", bold_font or regular_font)
    pdf.add_page()

    # Enable HarfBuzz RTL shaping for Arabic + mixed English technical terms
    pdf.set_text_shaping(
        use_shaping_engine=True,
        direction="rtl",
        script="arab",
        language="ara",
    )

    # Top Title Banner
    pdf.set_fill_color(15, 23, 42)  # Slate-900
    pdf.rect(10, 10, 190, 26, style="F")

    pdf.set_xy(15, 13)
    pdf.set_font(font_family, "B", 15)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(180, 9, "عرض فني احترافي", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(15)
    pdf.set_font(font_family, "", 11)
    pdf.set_text_color(203, 213, 225)
    pdf.multi_cell(180, 6, project.title, align="R")

    pdf.set_y(40)

    # Metadata bar (status + user provided details only)
    meta_items: list[str] = [
        f"الحالة: {draft.editor_status.value.upper()}",
        f"الإصدار: v{draft.version}",
    ]
    if prof_profile and prof_profile.professional_name:
        meta_items.insert(0, f"مقدّم العرض: {prof_profile.professional_name}")
    if prof_profile and prof_profile.specialization:
        meta_items.append(f"التخصص: {prof_profile.specialization}")

    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(203, 213, 225)
    pdf.set_font(font_family, "", 9)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(
        190,
        6,
        "  |  ".join(meta_items),
        border=1,
        fill=True,
        align="R",
    )
    pdf.ln(5)

    # 9 Structured Sections
    for key in SECTION_KEYS:
        label = SECTION_LABELS_AR[key]
        body = getattr(sections, key, "").strip()
        if key == "cost" and not body:
            body = "لم تُحدد التكلفة بعد (يحددها المستخدم)."
        elif not body:
            body = "—"

        # Section Heading
        pdf.set_font(font_family, "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.set_fill_color(226, 232, 240)
        pdf.cell(
            190,
            8,
            f"  {label}",
            fill=True,
            align="R",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(2)

        # Section Body
        pdf.set_font(font_family, "", 10)
        pdf.set_text_color(30, 41, 59)
        pdf.multi_cell(190, 6, body, align="R")
        pdf.ln(4)

    return bytes(pdf.output())
