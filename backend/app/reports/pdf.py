"""El informe de gastos en PDF (requerimiento 8).

Diseño para imprimir: hoja A4 blanca, texto casi negro y el azul de CyberManagement solo
como acento (títulos y líneas). Encabezado y "Página N de M" en todas las hojas.

SEGURIDAD: los textos que escriben las personas (descripciones, nombres, NIT...) pasan por
escape() antes de entrar a un Paragraph, porque ReportLab interpreta etiquetas tipo HTML
(<b>, <font>...) dentro del texto: sin escaparlos, un nombre podría alterar o romper el PDF.
"""

import io
import logging
from datetime import date
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from PIL import Image as PILImage
from PIL import ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.core.config import get_settings
from app.services.reports import ReportData
from app.storage import Storage
from app.storage.base import StorageError
from app.utils.money import format_cop

logger = logging.getLogger(__name__)

MAX_PHOTOS = 60  # anexos por informe: más fotos harían un PDF enorme
PHOTO_BOX = (8.1 * cm, 9.6 * cm)  # dos fotos por fila
PHOTO_MAX_PX = 1300  # se reducen antes de incrustarlas (el original queda intacto en el sistema)

INK = colors.HexColor("#0a1122")
MUTED = colors.HexColor("#5d6a8a")
LINE = colors.HexColor("#d5dbe8")
SOFT = colors.HexColor("#f3f6fc")
ACCENT = colors.HexColor("#2f6bff")
DANGER = colors.HexColor("#b4233a")
OK = colors.HexColor("#13795b")

STATUS = {
    "draft": "Borrador", "pending_completion": "Pendiente de completar", "submitted": "Enviado",
    "in_review": "En revisión", "approved": "Aprobado", "rejected": "Rechazado", "closed": "Cerrado",
}
SETTLEMENT = {
    "employee_must_return": ("Debe reintegrar", DANGER),
    "company_must_reimburse": ("La empresa le debe", DANGER),
    "balanced": ("Paz y salvo", OK),
}

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=8.6, leading=11, textColor=INK)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=7.4, leading=9.4, textColor=MUTED)
RIGHT = ParagraphStyle("right", parent=BASE, alignment=TA_RIGHT)
TITLE = ParagraphStyle("title", parent=BASE, fontName="Helvetica-Bold", fontSize=22, leading=26)
SUBTITLE = ParagraphStyle("subtitle", parent=BASE, fontSize=11, leading=15, textColor=MUTED)
H2 = ParagraphStyle("h2", parent=BASE, fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=14,
                    spaceAfter=6, textColor=INK)
KPI_LABEL = ParagraphStyle("kpi-label", parent=SMALL, fontSize=7.6)
KPI_VALUE = ParagraphStyle("kpi-value", parent=BASE, fontName="Helvetica-Bold", fontSize=13.5, leading=17)


def p(text, style=BASE) -> Paragraph:
    """Paragraph con el texto ESCAPADO (nunca se interpreta como etiquetas)."""
    return Paragraph(escape(str(text if text is not None else "—")), style)


def fmt_date(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "—"


def money(value) -> str:
    return format_cop(value) if value is not None else "—"


# --- Página: encabezado y "Página N de M" ---


class _NumberedCanvas(pdf_canvas.Canvas):
    """Guarda cada página hasta el final para poder escribir el total de páginas."""

    def __init__(self, *args, header=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []
        self._header = header

    def showPage(self):  # noqa: N802 (nombre de ReportLab)
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._header(self, total)
            super().showPage()
        super().save()


def _page_decorator(data: ReportData, period: str, generated: str):
    width, height = A4

    def draw(c: pdf_canvas.Canvas, total_pages: int) -> None:
        c.saveState()
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.4)
        c.line(1.8 * cm, height - 1.45 * cm, width - 1.8 * cm, height - 1.45 * cm)
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(INK)
        c.drawString(1.8 * cm, height - 1.2 * cm, data.company.name)
        c.setFont("Helvetica", 8)
        c.setFillColor(MUTED)
        c.drawRightString(width - 1.8 * cm, height - 1.2 * cm, f"Informe de gastos · {period}")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(1.8 * cm, 1.45 * cm, width - 1.8 * cm, 1.45 * cm)
        c.drawString(1.8 * cm, 1.0 * cm, generated)
        c.drawRightString(width - 1.8 * cm, 1.0 * cm, f"Página {c.getPageNumber()} de {total_pages}")
        c.restoreState()

    return draw


# --- Bloques ---


def _table(rows, widths, *, header=True, numeric_cols=(), zebra=True, total_row=False) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.6),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.8), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    for col in numeric_cols:
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    if zebra:
        for i in range(1 if header else 0, len(rows) - (1 if total_row else 0)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), SOFT))
    if total_row:
        style += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8.8), ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK)]
    table.setStyle(TableStyle(style))
    return table


def _kpis(items) -> Table:
    cells = [[[p(label, KPI_LABEL), p(value, KPI_VALUE)] for label, value in items]]
    width = (A4[0] - 3.6 * cm) / len(items)
    table = Table(cells, colWidths=[width] * len(items))
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def _photo(storage: Storage, row, number: int):
    doc = row.photo
    caption = p(f"Anexo {number} · {fmt_date(row.day)} · {row.employee_name} · {money(row.expense.total)}", SMALL)
    if doc.mime_type == "application/pdf":
        return [p("Factura en PDF: se puede ver en el panel.", SMALL), caption]
    try:
        with PILImage.open(io.BytesIO(storage.read(doc.storage_key))) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")  # respeta la orientación de la foto
            image.thumbnail((PHOTO_MAX_PX, PHOTO_MAX_PX))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=72, optimize=True)
            width_px, height_px = image.size
    except (StorageError, OSError, PILImage.DecompressionBombError):
        logger.warning("No se pudo incluir la foto %s en el informe", doc.id)
        return [p("No se pudo incluir esta foto.", SMALL), caption]
    buffer.seek(0)
    scale = min(PHOTO_BOX[0] / width_px, PHOTO_BOX[1] / height_px)
    return [Image(buffer, width=width_px * scale, height=height_px * scale), Spacer(1, 3), caption]


def build_pdf(data: ReportData, storage: Storage) -> bytes:
    tz = ZoneInfo(get_settings().timezone)
    f = data.filters
    period = f"{fmt_date(f.date_from)} al {fmt_date(f.date_to)}"
    generated_at = data.generated_at.astimezone(tz)
    generated = f"Generado el {generated_at:%d/%m/%Y a las %H:%M} por {data.generated_by}"
    content_width = A4[0] - 3.6 * cm

    def partial_label(label: str, attribute: str) -> str:
        """No todas las facturas traen subtotal e impuestos: se dice de cuántas sale la suma."""
        with_value = sum(1 for r in data.rows if getattr(r.expense, attribute) is not None)
        return label if with_value == len(data.rows) else f"{label} ({with_value} de {len(data.rows)} facturas)"

    story = [
        p("Informe de gastos", TITLE),
        Spacer(1, 4),
        p(f"{data.company.name} · {period}", SUBTITLE),
        Spacer(1, 2),
        p(f"Empleado: {data.employee_name or 'Todos'} · Tipo de gasto: {data.category_name or 'Todos'} · "
          f"Estados: {', '.join(STATUS[s.value] for s in f.effective_statuses())}", SMALL),
        p(generated, SMALL),
        Spacer(1, 12),
        p("Resumen general", H2),
        _kpis([("Facturas", str(len(data.rows))), ("Total de gastos", money(data.total)),
               (partial_label("Subtotal", "subtotal"), money(data.subtotal)),
               (partial_label("Impuestos", "tax_total"), money(data.tax_total))]),
        Spacer(1, 8),
        _kpis([("Viáticos del período", str(len(data.viaticos))), ("Total entregado", money(data.viaticos_delivered)),
               ("Gastado de viáticos", money(data.viaticos_spent)), ("Saldo restante", money(data.viaticos_remaining))]),
    ]
    if data.truncated:
        story += [Spacer(1, 6), p("Atención: el período tiene demasiadas facturas; el informe incluye las primeras "
                                  f"{len(data.rows)}. Usa un rango de fechas más corto.", SMALL)]

    # --- Desgloses ---
    def breakdown(title, rows):
        if not rows:
            return []
        table_rows = [["", "Facturas", "Total"]] + [[p(name), str(count), money(total)] for name, count, total in rows]
        return [p(title, H2), _table(table_rows, [content_width * 0.6, content_width * 0.15, content_width * 0.25],
                                     numeric_cols=(1, 2))]

    status_rows = [(STATUS[k], c, t) for k, (c, t) in sorted(data.by_status.items(), key=lambda x: -x[1][1])]
    story += breakdown("Por estado", status_rows)
    story += breakdown("Por tipo de gasto", data.by_category)
    story += breakdown("Por empleado", data.by_employee)

    # --- Viáticos ---
    if data.viaticos:
        rows = [["Empleado", "Viático", "Entregado", "Gastado", "Saldo", "Liquidación"]]
        for v in data.viaticos:
            b = v.balance
            label, color = SETTLEMENT[b.settlement.value]
            status = "abierto" if v.viatico.status.value == "open" else f"cerrado {fmt_date(v.viatico.closed_at.date())}"
            rows.append([p(v.employee_name), [p(v.viatico.purpose or "Viático"), p(status, SMALL)],
                         money(b.delivered - b.refunded), money(b.spent), money(b.remaining),
                         p(label, ParagraphStyle("settle", parent=BASE, textColor=color))])
        widths = [0.2, 0.22, 0.15, 0.14, 0.13, 0.16]
        story += [CondPageBreak(4 * cm), p("Viáticos del período", H2),
                  _table(rows, [content_width * w for w in widths], numeric_cols=(2, 3, 4))]

    # --- Gastos individuales ---
    photos = [r for r in data.rows if r.photo][:MAX_PHOTOS]
    annex = {id(r): i + 1 for i, r in enumerate(photos)}
    story += [CondPageBreak(5 * cm), p("Gastos individuales", H2)]
    if data.rows:
        rows = [["Fecha", "Empleado", "Establecimiento y gasto", "Factura", "Tipo", "Estado", "Total", "Foto"]]
        for r in data.rows:
            e = r.expense
            if r.supplier:
                nit = f"NIT {r.supplier.nit}" + (f"-{r.supplier.dv}" if r.supplier.dv else "")
                place = [p(r.supplier.name), p(nit, SMALL)]
            else:
                place = [p("Sin NIT", SMALL)]
            place.append(p(e.description or "", SMALL))  # qué se compró
            rows.append([fmt_date(r.day), p(r.employee_name), place,
                         p(e.invoice_number or "—", SMALL), p(r.category or "—", SMALL), p(STATUS[e.status.value], SMALL),
                         money(e.total), f"A{annex[id(r)]}" if id(r) in annex else "—"])
        rows.append(["", "", "", "", "", "Total", money(data.total), ""])
        widths = [0.1, 0.14, 0.25, 0.09, 0.13, 0.11, 0.12, 0.06]  # "Tipo" cabe sin partir palabras
        story.append(_table(rows, [content_width * w for w in widths], numeric_cols=(6,), total_row=True))
    else:
        story.append(p("No hay facturas con estos filtros en el período.", SUBTITLE))

    # --- Anexos: fotos ---
    if photos:
        story += [PageBreak(), p("Anexos: fotos de las facturas", H2),
                  p("Las fotos se reducen para que el informe pese menos. El original queda guardado en el sistema.", SMALL),
                  Spacer(1, 8)]
        cells = [_photo(storage, r, annex[id(r)]) for r in photos]
        grid = [cells[i:i + 2] + ([""] if len(cells[i:i + 2]) == 1 else []) for i in range(0, len(cells), 2)]
        for row in grid:
            table = Table([row], colWidths=[content_width / 2] * 2)
            table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 14)]))
            story.append(KeepTogether(table))
        extra = sum(1 for r in data.rows if r.photo) - len(photos)
        if extra > 0:
            story.append(p(f"Hay {extra} foto(s) más que no se incluyeron. Se pueden ver en el panel.", SMALL))

    buffer = io.BytesIO()
    decorate = _page_decorator(data, period, generated)
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                            topMargin=2.0 * cm, bottomMargin=2.0 * cm,
                            title=f"Informe de gastos {period}", author=data.company.name, subject="CyberManagement",
                            creator="CyberManagement")
    doc.build(story, canvasmaker=lambda *a, **k: _NumberedCanvas(*a, header=decorate, **k))
    return buffer.getvalue()
