"""Generated PDFs for tests. Real lab reports hold personal data and must never be committed."""

import io
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

HEADER = ["Test", "Result", "Flag", "Units", "Reference Interval"]


def lab_report(rows, header_lines=("Date Collected: 01/15/2026",), tag=""):
    """A Labcorp-style report: a few header lines and a ruled results table.

    rows: [name, result, flag, unit, range] lists. `tag` makes otherwise identical files differ.
    """
    buffer = io.BytesIO()
    style = getSampleStyleSheet()["Normal"]
    story = [Paragraph(line, style) for line in list(header_lines) + ([tag] if tag else [])]
    story.append(Table([HEADER] + [list(r) for r in rows], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)])))
    SimpleDocTemplate(buffer).build(story)
    return buffer.getvalue()


# Column positions of the athenahealth "Data Portability" results table
_COLUMNS = [("Created", 40), ("Observation", 79), ("Name", 130), ("Description", 194), ("Value", 243), ("Unit", 273),
            ("Range", 303), ("Abnormal", 336), ("Note", 379), ("LastModifiedBy", 405), ("Organization", 467),
            ("LastModifiedTime", 519)]


def health_summary(rows, tag=""):
    """A patient-portal health summary whose results table has a date on every row.

    rows: (created "MM/DD/YYYY", observed, panel, test, value, unit, range, flag) tuples.
    Cells are placed by position with no ruling lines, like the real export, and the two dates
    print as one word ("09/25/202409/26/2024").
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    height = letter[1]
    c.setFont("Helvetica-Bold", 12)
    c.drawString(34, height - 30, "Results")
    c.setFont("Helvetica", 7)
    y = height - 45
    for word, x in _COLUMNS:
        c.drawString(x, y, word)
    y -= 8
    for word, x in (("Date", 40), ("Date", 79), ("Flag", 336), ("Detail", 467)):
        c.drawString(x, y, word)
    y -= 14
    for created, observed, panel, test, value, unit, rng, flag in rows:
        c.drawString(35, y, created + observed)
        c.drawString(125, y, panel)
        c.drawString(190, y, test)
        c.drawString(239, y, value)
        c.drawString(268, y, unit)
        c.drawString(299, y, rng)
        if flag:
            c.drawString(331, y, flag)
        c.drawString(401, y, "Not Available")
        c.drawString(462, y, "Example Lab")
        c.drawString(515, y, observed)
        c.drawString(462, y - 8, "123 Main St")  # wrapped organization text belongs to the same row
        y -= 22
    c.setFont("Helvetica-Bold", 10)
    c.drawString(34, y - 10, "Result Notes")
    c.setFont("Helvetica", 7)
    c.drawString(34, y - 22, "None recorded." + (f" {tag}" if tag else ""))
    c.showPage()
    c.save()
    return buffer.getvalue()


def visit_summary(visit="03/02/2026", vitals='BP: 128/82 T: 98.4 Pulse: 72 Resp: 16 Wt: 180lb 8.00oz Ht: 70" BMI: 25.9 O2SatR: 97',
                  new=(("Lisinopril", "10 mg, 1 by mouth every day"),), keep=(("Metformin", "500 mg, 1 by mouth twice a day"),),
                  stop=(("Amlodipine", "5 mg, 1 by mouth every day"),), tag=""):
    """A clinic's "Clinical Visit Summary": visit header, a one-line Vitals section with bold labels,
    and Medications split into New, Continue and Discontinue. Names and values are made up."""
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    body, heading = styles["Normal"], styles["Heading3"]
    bold = re.sub(r"(\b[A-Za-z0-9]+):", r"<b>\1</b>:", vitals)
    story = [Paragraph("EXAMPLE FAMILY CLINIC", styles["Title"]), Paragraph("Clinical Visit Summary of Today's Visit", styles["Heading2"]),
             Paragraph("Pat Example", body), Paragraph(f"{visit}  Visit with Alex Doe, PA-C", body),
             Paragraph("Today's Diagnosis", heading), Paragraph("Essential hypertension (I10)", body),
             Paragraph("Vitals", heading), Paragraph(bold, body), Paragraph("Medications", heading)]
    for label, meds in (("New", new), ("Continue", keep), ("Discontinue", stop)):
        if meds:
            story.append(Paragraph(label, body))
            story.append(Paragraph("&nbsp;&nbsp;Alex Doe, PA-C", body))
            story += [Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;{name} : {how}", body) for name, how in meds]
    story += [Paragraph("Follow Up", heading), Paragraph("3 months." + (f" {tag}" if tag else ""), body)]
    SimpleDocTemplate(buffer).build(story)
    return buffer.getvalue()
