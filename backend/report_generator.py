"""
report_generator.py
-------------------
Generates a PDF exam-proctoring report from a completed session summary dict.

Output:
  reports/report_<session_id[:8]>.pdf
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fpdf import FPDF, XPos, YPos

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

VTYPE_META = {
    "NO_FACE":             ("No Face Detected",           (220, 50,  50)),
    "MULTIPLE_FACES":      ("Multiple Faces Detected",    (230, 120, 20)),
    "HAND_OVER_FACE":      ("Hand Over Face",             (180, 50, 200)),
    "SUSPICIOUS_GESTURE":  ("Suspicious Gesture",         (200, 60,  60)),
    "PHONE_DETECTED":      ("Phone / Device Detected",    (220, 20,  40)),
    "CHEATING_OBJECT":     ("Cheating Object",            (210, 80,  10)),
    "EARPHONE_DETECTED":   ("Earphone / Headphone",       (180, 120,  0)),
}


class ProctorReport(FPDF):
    def __init__(self, student_id: str, session_id: str):
        super().__init__()
        self.student_id = student_id
        self.session_id = session_id
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.set_fill_color(30, 30, 50)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, "  AI Exam Proctoring System - Session Report",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Session {self.session_id[:8]} | Page {self.page_no()}/{{nb}}", align="C")


def generate_report(summary: dict) -> str:
    sid        = summary["session_id"]
    student    = summary["student_id"]
    started    = summary["started_at"]
    ended      = summary.get("ended_at", "N/A")
    duration   = summary.get("duration_sec", 0)
    violations = summary.get("violations", [])
    total_v    = summary.get("total_violations", len(violations))

    pdf = ProctorReport(student_id=student, session_id=sid)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(30, 30, 80)
    pdf.cell(0, 8, "Session Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(0, 0, 0)
    rows = [
        ("Student ID",       _safe(student)),
        ("Session ID",       sid[:8]),
        ("Started",          _fmt_ts(started)),
        ("Ended",            _fmt_ts(ended)),
        ("Duration",         f"{int(duration // 60)}m {int(duration % 60)}s"),
        ("Total Violations", str(total_v)),
    ]
    col_w = [55, 125]
    for label, value in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(col_w[0], 7, label + ":", border="B", fill=False)
        pdf.set_font("Helvetica", "", 10)
        color = (180, 30, 30) if label == "Total Violations" and total_v > 0 else (0, 0, 0)
        pdf.set_text_color(*color)
        pdf.cell(col_w[1], 7, value, border="B", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
    pdf.ln(8)

    counts: dict[str, int] = {}
    for v in violations:
        counts[v["type"]] = counts.get(v["type"], 0) + 1

    if counts:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(30, 30, 80)
        pdf.cell(0, 8, "Violation Breakdown", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(220, 220, 235)
        for header, w in [("Type", 90), ("Label", 60), ("Count", 30)]:
            pdf.cell(w, 7, header, border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_font("Helvetica", "", 10)
        for vtype, count in counts.items():
            label, rgb = VTYPE_META.get(vtype, (vtype, (80, 80, 80)))
            pdf.set_text_color(*rgb)
            pdf.cell(90, 6, vtype, border=1)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(60, 6, label, border=1)
            pdf.cell(30, 6, str(count), border=1, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(8)

    if violations:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(30, 30, 80)
        pdf.cell(0, 8, "Violation Timeline", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
        headers = [("#", 10), ("Timestamp", 45), ("Type", 55), ("Duration", 30), ("Snapshot", 50)]
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(220, 220, 235)
        for h, w in headers:
            pdf.cell(w, 7, h, border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for idx, v in enumerate(violations, 1):
            _, rgb = VTYPE_META.get(v["type"], (v["type"], (80, 80, 80)))
            snap = os.path.basename(v["snapshot_path"]) if v.get("snapshot_path") else "-"
            snap_short = snap[:22] + "..." if len(snap) > 24 else snap
            pdf.set_text_color(0, 0, 0)
            pdf.cell(10, 6, str(idx), border=1, align="C")
            pdf.cell(45, 6, _fmt_ts(v["timestamp"], short=True), border=1)
            pdf.set_text_color(*rgb)
            pdf.cell(55, 6, v["type"], border=1)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(30, 6, f"{v['duration_sec']:.1f}s", border=1, align="C")
            pdf.cell(50, 6, snap_short, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(8)

    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0, 5,
        "PRIVACY NOTE: All video processing occurred on the backend server. "
        "Only violation metadata and JPEG snapshots are stored.",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT
    )

    pdf_path = str(REPORTS_DIR / f"report_{sid[:8]}.pdf")
    pdf.output(pdf_path)
    return pdf_path


def _fmt_ts(iso_str: str, short: bool = False) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M:%S") if short else dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(iso_str)


def _safe(text: str) -> str:
    replacements = {
        "\u2014": "-", "\u2013": "-", "\u2026": "...",
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")
