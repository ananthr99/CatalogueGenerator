"""Master Catalogue Assembly
==============================================
Builds a single merged PDF:
  Page 1  — Branded cover page
  Page 2  — Category-wise index with clickable hyperlinks
  Page 3+ — Product datasheets sorted by the page number
             printed at the top of each datasheet PDF.

Usage (standalone test):
  python -m core.assemble --output master_catalogue.pdf
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional imports — graceful failure message if library missing
# ---------------------------------------------------------------------------
try:
    import pymupdf                          # PDF reading + merging
    from pymupdf import Document as FitzDoc
except ImportError:
    pymupdf = None  # type: ignore

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as rl_canvas
except ImportError:
    rl_canvas = None  # type: ignore

try:
    import numpy as np
    from PIL import Image as PILImage
except ImportError:
    PILImage = None  # type: ignore
    np = None  # type: ignore

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------
RED   = colors.HexColor("#FF0101") if rl_canvas else None
BLUE  = colors.HexColor("#05059B") if rl_canvas else None
WHITE = colors.white if rl_canvas else None
GREY  = colors.HexColor("#F0F2F5") if rl_canvas else None
DGREY = colors.HexColor("#B0B4BE") if rl_canvas else None

W, H = A4 if rl_canvas else (595.28, 841.89)  # 595.28 x 841.89 pt

# Temp files for pre-processed logos (file paths required by ReportLab drawImage)
_TMP_DIR = tempfile.gettempdir()
_INV_LOGO_TMP = os.path.join(_TMP_DIR, "inv_catalogue_tmp.png")
_SIL_LOGO_TMP = os.path.join(_TMP_DIR, "sil_catalogue_tmp.png")

# ---------------------------------------------------------------------------
# Helper: clean logo (remove dark background → transparent) and save to disk
# ---------------------------------------------------------------------------
def _prepare_logo(src_path: str, dst_path: str, threshold: int = 60) -> bool:
    """Clean near-black pixels (make transparent) and save to *dst_path*.

    Returns True if the file was written successfully.
    """
    if PILImage is None or np is None:
        # No PIL — just copy the file as-is
        try:
            import shutil
            shutil.copy2(src_path, dst_path)
            return True
        except Exception:
            return False
    try:
        img = PILImage.open(src_path).convert("RGBA")
        data = np.array(img)
        r, g, b, a = data[:, :, 0], data[:, :, 1], data[:, :, 2], data[:, :, 3]
        mask = (r < threshold) & (g < threshold) & (b < threshold)
        data[mask, 3] = 0
        result = PILImage.fromarray(data)
        result.save(dst_path)
        return True
    except Exception:
        return False


def _prepare_logos(invendis_logo: Optional[str], silbo_logo: Optional[str]) -> tuple[str | None, str | None]:
    """Prepare both logos for ReportLab use. Returns (inv_path, sil_path)."""
    inv_path = None
    sil_path = None

    if invendis_logo and os.path.exists(invendis_logo):
        if _prepare_logo(invendis_logo, _INV_LOGO_TMP, threshold=60):
            inv_path = _INV_LOGO_TMP

    if silbo_logo and os.path.exists(silbo_logo):
        if _prepare_logo(silbo_logo, _SIL_LOGO_TMP, threshold=60):
            sil_path = _SIL_LOGO_TMP

    return inv_path, sil_path


# ---------------------------------------------------------------------------
# 1. Extract page number from a product PDF
# ---------------------------------------------------------------------------
def extract_page_number(pdf_path: str) -> int:
    """Read the first page of *pdf_path* and return the catalogue page number."""
    if pymupdf is None:
        raise RuntimeError("pymupdf is not installed — run: pip install pymupdf")
    doc = pymupdf.open(pdf_path)
    try:
        text = doc[0].get_text()
    finally:
        doc.close()
    m = re.search(r"\bPage\s+(\d+)\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# 2. Scan output directory → sorted list of (page_no, pdf_path, name, category)
# ---------------------------------------------------------------------------
def scan_output_dir(output_dir: str) -> list[tuple[int, str, str, str]]:
    """Return a list of tuples sorted by page number."""
    if pymupdf is None:
        raise RuntimeError("pymupdf is not installed — run: pip install pymupdf")

    base = Path(output_dir)
    results: list[tuple[int, str, str, str]] = []

    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        pdfs = list(folder.glob("*.pdf"))
        if not pdfs:
            continue
        pdf_path = str(pdfs[0])
        try:
            doc = pymupdf.open(pdf_path)
            text = doc[0].get_text()
            doc.close()
        except Exception:
            continue

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        m = re.search(r"\bPage\s+(\d+)\b", text, re.IGNORECASE)
        page_no = int(m.group(1)) if m else 0

        prod_name = lines[2] if len(lines) > 2 else folder.name
        category  = lines[4] if len(lines) > 4 else "Other"

        results.append((page_no, pdf_path, prod_name, category))

    results.sort(key=lambda t: (t[0] == 0, t[0]))
    return results


# ---------------------------------------------------------------------------
# 3. Cover page — professional branded design
# ---------------------------------------------------------------------------
def build_cover_pdf(out_path: str,
                    invendis_logo: Optional[str] = None,
                    silbo_logo: Optional[str] = None,
                    settings: Optional[dict] = None) -> str:
    """Generate the cover page PDF and return *out_path*.

    *settings* keys (all optional, fall back to defaults):
        year, title_top, title_main, tagline, eyebrow,
        company, address, email, phone, website, footer_tag
    """
    if rl_canvas is None:
        raise RuntimeError("reportlab is not installed — run: pip install reportlab")

    s = settings or {}
    year       = s.get("year",       "2026")
    title_top  = s.get("title_top",  "PRODUCT")
    title_main = s.get("title_main", "CATALOGUE")
    tagline    = s.get("tagline",    "Industrial Networking  ·  Edge Computing  ·  Smart Metering")
    eyebrow    = s.get("eyebrow",    "INVENDIS TECHNOLOGIES")
    company    = s.get("company",    "Invendis Technologies India Pvt. Ltd.")
    address    = s.get("address",    "No. 10, Marenahalli, Vijayanagar\nBangalore – 560 040, India")
    email      = s.get("email",      "sales@invendis.com")
    phone      = s.get("phone",      "+91 6361509463")
    website    = s.get("website",    "www.invendis.com")
    footer_tag = s.get("footer_tag", f"Invent, Discover.   |   {website}   |   {email}")

    inv_logo, sil_logo = _prepare_logos(invendis_logo, silbo_logo)
    c = rl_canvas.Canvas(out_path, pagesize=A4)

    # ── 1. Full-bleed dark-blue background ───────────────────────────────────
    c.setFillColor(BLUE)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # ── 2. White header band (top, 28 mm) ────────────────────────────────────
    HEADER_H = 28 * mm
    c.setFillColor(WHITE)
    c.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)

    if inv_logo:
        try:
            logo_w = 44 * mm
            logo_h = 14 * mm
            c.drawImage(inv_logo,
                        10 * mm,
                        H - HEADER_H + (HEADER_H - logo_h) / 2,
                        width=logo_w, height=logo_h,
                        preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    c.setFillColor(BLUE)
    c.setFont("Helvetica", 8)
    label_text = f"PRODUCT CATALOGUE  {year}"
    c.drawRightString(W - 10 * mm,
                      H - HEADER_H + (HEADER_H / 2) + 2 * mm,
                      label_text)
    label_w = c.stringWidth(label_text, "Helvetica", 8)
    c.setFillColor(RED)
    c.rect(W - 10 * mm - label_w - 7 * mm,
           H - HEADER_H + (HEADER_H / 2) - 0.5 * mm,
           5 * mm, 1.2 * mm, fill=1, stroke=0)

    # ── 3. Red accent stripe below header (3 mm) ─────────────────────────────
    STRIPE_Y = H - HEADER_H - 3 * mm
    c.setFillColor(RED)
    c.rect(0, STRIPE_Y, W, 3 * mm, fill=1, stroke=0)

    # ── 4. Geometric accent — diagonal panel on right ────────────────────────
    c.saveState()
    c.setFillColor(colors.HexColor("#0808B8"))
    p2 = c.beginPath()
    p2.moveTo(W * 0.42, STRIPE_Y)
    p2.lineTo(W,        STRIPE_Y)
    p2.lineTo(W,        STRIPE_Y - H * 0.68)
    p2.lineTo(W * 0.55, STRIPE_Y - H * 0.68)
    p2.close()
    c.drawPath(p2, fill=1, stroke=0)
    c.restoreState()

    c.saveState()
    c.setStrokeColor(colors.Color(1, 1, 1, alpha=0.07))
    c.setLineWidth(0.6)
    for i in range(0, 18):
        y = STRIPE_Y - 15 * mm - i * 18 * mm
        x_start = W * 0.50 + i * 2 * mm
        c.line(x_start, y, W - 10 * mm, y)
        if i % 3 == 0 and i > 0:
            c.line(x_start, y, x_start, y + 18 * mm)
    c.restoreState()

    # ── 5. Main text block ───────────────────────────────────────────────────
    BODY_BOT = 68 * mm
    text_centre_y = BODY_BOT + (STRIPE_Y - BODY_BOT) * 0.56

    c.setFillColor(RED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(14 * mm, text_centre_y + 42 * mm, eyebrow)
    c.setStrokeColor(RED)
    c.setLineWidth(1.0)
    c.line(14 * mm, text_centre_y + 40 * mm, 65 * mm, text_centre_y + 40 * mm)

    c.setFillColor(WHITE)
    c.setFont("Helvetica", 32)
    c.drawString(14 * mm, text_centre_y + 24 * mm, title_top)

    c.setFont("Helvetica-Bold", 54)
    c.drawString(14 * mm, text_centre_y - 4 * mm, title_main)

    c.setStrokeColor(RED)
    c.setLineWidth(3.5)
    c.line(14 * mm, text_centre_y - 8 * mm, W * 0.68, text_centre_y - 8 * mm)

    c.setFillColor(colors.Color(1, 1, 1, alpha=0.85))
    c.setFont("Helvetica", 18)
    c.drawString(14 * mm, text_centre_y - 18 * mm, year)

    c.setFillColor(colors.Color(1, 1, 1, alpha=0.55))
    c.setFont("Helvetica", 9.5)
    c.drawString(14 * mm, text_centre_y - 27 * mm, tagline)

    # ── 6. Silbo logo ────────────────────────────────────────────────────────
    if sil_logo:
        try:
            sil_w = 62 * mm
            sil_h = 18 * mm
            c.drawImage(sil_logo,
                        W - sil_w - 12 * mm,
                        BODY_BOT + 28 * mm,
                        width=sil_w, height=sil_h,
                        preserveAspectRatio=True, mask="auto")
            c.setFillColor(colors.Color(1, 1, 1, alpha=0.40))
            c.setFont("Helvetica", 7)
            c.drawRightString(W - 12 * mm, BODY_BOT + 25 * mm, "Networking Products by SILBO")
        except Exception:
            pass

    # ── 7. Contact footer band ───────────────────────────────────────────────
    FOOTER_H = 60 * mm
    c.setFillColor(colors.HexColor("#030354"))
    c.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(0, FOOTER_H - 1.5 * mm, W, 1.5 * mm, fill=1, stroke=0)

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(14 * mm, FOOTER_H - 12 * mm, company)

    c.setStrokeColor(colors.Color(1, 1, 1, alpha=0.18))
    c.setLineWidth(0.5)
    c.line(14 * mm, FOOTER_H - 15 * mm, W - 14 * mm, FOOTER_H - 15 * mm)

    items = [
        ("ADDRESS", address),
        ("EMAIL",   email),
        ("PHONE",   phone),
        ("WEBSITE", website),
    ]
    block_w = (W - 28 * mm) / 4
    for i, (lbl, val) in enumerate(items):
        bx = 14 * mm + i * block_w
        c.setFillColor(RED)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(bx, FOOTER_H - 23 * mm, lbl)
        c.setFillColor(colors.Color(1, 1, 1, alpha=0.85))
        c.setFont("Helvetica", 8)
        for j, ln in enumerate(val.split("\n")):
            c.drawString(bx, FOOTER_H - 30 * mm - j * 8, ln)

    c.setFillColor(RED)
    c.rect(0, 0, W, 7 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W / 2, 2.3 * mm, footer_tag)

    c.showPage()
    c.save()
    return out_path


# ---------------------------------------------------------------------------
# 4. Index page — category-wise TOC with clickable hyperlinks
# ---------------------------------------------------------------------------
def build_index_pdf(out_path: str,
                    products: list[tuple[int, str, str, str]],
                    page_offset: int = 2,
                    invendis_logo: Optional[str] = None,
                    settings: Optional[dict] = None) -> tuple[str, list]:
    """Generate the index page with category-wise TOC.

    Returns (out_path, link_specs) where link_specs is a list of dicts:
        {"rect": (x0,y0,x1,y1), "target_page": int}  — PDF coords (pt), 0-based target page
    """
    if rl_canvas is None:
        raise RuntimeError("reportlab is not installed — run: pip install reportlab")

    s = settings or {}
    index_title    = s.get("index_title", "Product Index")
    cat_order_str  = s.get("cat_order", "")
    name_overrides = s.get("name_overrides", {})
    cat_overrides  = s.get("cat_overrides", {})

    inv_logo = None
    if invendis_logo and os.path.exists(invendis_logo):
        if os.path.exists(_INV_LOGO_TMP):
            inv_logo = _INV_LOGO_TMP
        else:
            if _prepare_logo(invendis_logo, _INV_LOGO_TMP, threshold=60):
                inv_logo = _INV_LOGO_TMP

    c = rl_canvas.Canvas(out_path, pagesize=A4)

    # ── background ──────────────────────────────────────────────────────────
    c.setFillColor(WHITE)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # ── top header band (white, matching cover page) ─────────────────────────
    HEADER_H = 22 * mm
    c.setFillColor(WHITE)
    c.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)

    if inv_logo:
        try:
            logo_w = 36 * mm
            logo_h = 11 * mm
            c.drawImage(inv_logo,
                        10 * mm,
                        H - HEADER_H + (HEADER_H - logo_h) / 2,
                        width=logo_w, height=logo_h,
                        preserveAspectRatio=True, mask="auto")
        except Exception:
            # Fallback: text header
            c.setFillColor(BLUE)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(10 * mm, H - HEADER_H + HEADER_H / 2 - 4, "Invendis Technologies")

    c.setFillColor(DGREY)
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 10 * mm,
                      H - HEADER_H + HEADER_H / 2 - 3,
                      "www.invendis.com")

    # Red accent stripe
    c.setFillColor(RED)
    c.rect(0, H - HEADER_H - 2.5 * mm, W, 2.5 * mm, fill=1, stroke=0)

    # ── page title ───────────────────────────────────────────────────────────
    TITLE_Y = H - HEADER_H - 14 * mm
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(10 * mm, TITLE_Y, index_title)

    c.setFillColor(DGREY)
    c.setFont("Helvetica", 8.5)
    n_cats = len(set(p[3] for p in products))
    c.drawRightString(W - 10 * mm, TITLE_Y + 1 * mm,
                      f"{len(products)} products across {n_cats} categories")

    # underline
    c.setStrokeColor(RED)
    c.setLineWidth(2.0)
    c.line(10 * mm, TITLE_Y - 2.5 * mm, W - 10 * mm, TITLE_Y - 2.5 * mm)
    c.setStrokeColor(GREY)
    c.setLineWidth(0.5)
    c.line(10 * mm, TITLE_Y - 3 * mm, W - 10 * mm, TITLE_Y - 3 * mm)

    # ── build merged-page lookup ──────────────────────────────────────────────
    pg_to_merged: dict[int, int] = {}
    for idx, (pg_no, _, _, _) in enumerate(products):
        pg_to_merged[pg_no] = page_offset + idx  # 0-based merged page

    categories: dict[str, list] = defaultdict(list)
    for pg_no, _, name, cat in products:
        effective_cat = cat_overrides.get(name, cat)
        categories[effective_cat].append((pg_to_merged[pg_no], name, pg_no))

    # Category order — from settings if provided, else hard default
    if cat_order_str:
        CAT_ORDER = [c.strip() for c in cat_order_str.split(",") if c.strip()]
    else:
        CAT_ORDER = [
            "Edge Computers", "Computer", "Routers", "Gateways",
            "Switches", "Outdoor Unit", "PCB", "Other", "Energy Meter",
        ]
    ordered_cats = [cn for cn in CAT_ORDER if cn in categories]
    for cn in categories:
        if cn not in ordered_cats:
            ordered_cats.append(cn)

    # ── 2-column layout ──────────────────────────────────────────────────────
    COLS        = 2
    MARGIN      = 10 * mm
    COL_GAP     = 6 * mm
    COL_W       = (W - 2 * MARGIN - COL_GAP) / 2
    LEFT_X      = [MARGIN, MARGIN + COL_W + COL_GAP]
    ROW_H       = 6.0 * mm
    CAT_H       = 7.5 * mm
    INTER_GAP   = 3.5 * mm
    START_Y     = TITLE_Y - 7 * mm
    BOTTOM      = 14 * mm

    cur_col = 0
    cur_y   = START_Y
    link_specs: list[dict] = []

    for cat_name in ordered_cats:
        entries = sorted(categories[cat_name], key=lambda e: e[2])

        needed = CAT_H + len(entries) * ROW_H + INTER_GAP
        if cur_y - needed < BOTTOM:
            cur_col += 1
            cur_y = START_Y
            if cur_col >= COLS:
                break

        x = LEFT_X[cur_col]

        # ── Category header ─────────────────────────────────────────────────
        cur_y -= CAT_H
        c.setFillColor(BLUE)
        c.rect(x, cur_y, COL_W, CAT_H, fill=1, stroke=0)
        c.setFillColor(RED)
        c.rect(x, cur_y, 3 * mm, CAT_H, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x + 5 * mm, cur_y + 2.3 * mm, cat_name.upper())

        count_label = str(len(entries))
        pill_w = c.stringWidth(count_label, "Helvetica-Bold", 7) + 4 * mm
        c.setFillColor(RED)
        c.roundRect(x + COL_W - pill_w - 2 * mm,
                    cur_y + 1.2 * mm,
                    pill_w, 5 * mm, 1.5 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(x + COL_W - pill_w / 2 - 2 * mm,
                            cur_y + 2.4 * mm, count_label)

        # ── Product rows ────────────────────────────────────────────────────
        for row_i, (merged_pg, name, orig_pg) in enumerate(
                [(mp, name_overrides.get(nm, nm), op) for mp, nm, op in entries]):
            if cur_y - ROW_H < BOTTOM:
                break
            cur_y -= ROW_H

            if row_i % 2 == 0:
                c.setFillColor(colors.HexColor("#F5F6FA"))
                c.rect(x, cur_y, COL_W, ROW_H, fill=1, stroke=0)

            c.setStrokeColor(colors.HexColor("#E8EAF0"))
            c.setLineWidth(0.3)
            c.line(x, cur_y, x + COL_W, cur_y)

            c.setFillColor(BLUE)
            c.setFont("Helvetica", 8)
            max_name_w = COL_W - 18 * mm
            display_name = name
            while c.stringWidth(display_name, "Helvetica", 8) > max_name_w and len(display_name) > 4:
                display_name = display_name[:-2]
            c.drawString(x + 4 * mm, cur_y + 1.8 * mm, display_name)

            pg_label = f"p.{orig_pg:02d}"
            badge_w = c.stringWidth(pg_label, "Helvetica-Bold", 7.5) + 3.5 * mm
            c.setFillColor(colors.HexColor("#ECEFFE"))
            c.roundRect(x + COL_W - badge_w - 1.5 * mm,
                        cur_y + 0.8 * mm,
                        badge_w, 4.4 * mm, 1.2 * mm, fill=1, stroke=0)
            c.setFillColor(RED)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawCentredString(x + COL_W - badge_w / 2 - 1.5 * mm,
                                cur_y + 1.8 * mm, pg_label)

            link_specs.append({
                "rect": (x, cur_y, x + COL_W, cur_y + ROW_H),
                "target_page": merged_pg,
            })

        cur_y -= INTER_GAP

    # ── footer ───────────────────────────────────────────────────────────────
    c.setFillColor(BLUE)
    c.rect(0, 0, W, 10 * mm, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(0, 10 * mm - 1.5 * mm, W, 1.5 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 7)
    c.drawString(10 * mm, 3.5 * mm, "Invendis Technologies India Pvt. Ltd.  |  www.invendis.com")
    c.setFont("Helvetica-Bold", 7)
    c.drawRightString(W - 10 * mm, 3.5 * mm, "PRODUCT CATALOGUE 2026")

    c.showPage()
    c.save()
    return out_path, link_specs


# ---------------------------------------------------------------------------
# 5. Merge PDFs with working GoTo hyperlinks
# ---------------------------------------------------------------------------
def merge_catalogue(cover_pdf: str,
                    index_pdf: str,
                    products: list[tuple[int, str, str, str]],
                    out_path: str,
                    link_specs: list | None = None) -> str:
    """Merge cover + index + sorted product PDFs into one file."""
    if pymupdf is None:
        raise RuntimeError("pymupdf is not installed — run: pip install pymupdf")

    merged = pymupdf.open()

    with pymupdf.open(cover_pdf) as doc:
        merged.insert_pdf(doc)

    with pymupdf.open(index_pdf) as doc:
        merged.insert_pdf(doc)

    for _pg_no, pdf_path, _name, _cat in products:
        try:
            with pymupdf.open(pdf_path) as doc:
                merged.insert_pdf(doc)
        except Exception as e:
            print(f"Warning: could not add {pdf_path}: {e}")

    if link_specs:
        index_page = merged[1]
        ph = index_page.rect.height
        for spec in link_specs:
            x0, y0_rl, x1, y1_rl = spec["rect"]
            fitz_y0 = ph - y1_rl
            fitz_y1 = ph - y0_rl
            rect = pymupdf.Rect(x0, fitz_y0, x1, fitz_y1)
            target_pg = spec["target_page"]
            if 0 <= target_pg < len(merged):
                link = {
                    "kind": pymupdf.LINK_GOTO,
                    "from": rect,
                    "page": target_pg,
                    "to": pymupdf.Point(0, 0),
                    "zoom": 0.0,
                }
                index_page.insert_link(link)

    merged.save(out_path, garbage=4, deflate=True)
    merged.close()
    return out_path


# ---------------------------------------------------------------------------
# 6. High-level entry point
# ---------------------------------------------------------------------------
def build_master_catalogue(
    output_dir: str,
    out_pdf: str,
    invendis_logo: Optional[str] = None,
    silbo_logo: Optional[str] = None,
    work_dir: str = "work",
    progress_callback=None,
    settings: Optional[dict] = None,
) -> dict:
    """Build the complete master catalogue PDF."""
    tmp = tempfile.mkdtemp()

    if progress_callback:
        progress_callback(0.05, "Scanning product PDFs…")

    products = scan_output_dir(output_dir)
    if not products:
        raise ValueError(f"No product PDFs found in {output_dir}")

    if progress_callback:
        progress_callback(0.15, f"Found {len(products)} products — preparing logos…")

    # Pre-process logos once (saves them to temp files for ReportLab)
    _prepare_logos(invendis_logo, silbo_logo)

    if progress_callback:
        progress_callback(0.25, "Generating cover page…")

    cover_path = os.path.join(tmp, "_cover.pdf")
    build_cover_pdf(cover_path, invendis_logo, silbo_logo, settings=settings)

    if progress_callback:
        progress_callback(0.45, "Generating index page…")

    index_path = os.path.join(tmp, "_index.pdf")
    page_offset = 2
    _index_path, link_specs = build_index_pdf(
        index_path, products, page_offset=page_offset,
        invendis_logo=invendis_logo, settings=settings
    )

    if progress_callback:
        progress_callback(0.65, "Merging all pages…")

    merge_catalogue(cover_path, index_path, products, out_pdf, link_specs=link_specs)

    if progress_callback:
        progress_callback(1.0, "Done!")

    cats = sorted(set(cat for _, _, _, cat in products))
    return {
        "pdf": out_pdf,
        "page_count": len(products) + 2,
        "product_count": len(products),
        "categories": cats,
    }


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build master Invendis catalogue PDF")
    ap.add_argument("--output-dir", default="output", help="Folder containing product sub-folders")
    ap.add_argument("--output", default="master_catalogue.pdf")
    ap.add_argument("--invendis-logo", default="")
    ap.add_argument("--silbo-logo", default="")
    args = ap.parse_args()

    def progress(pct, msg):
        bar = "█" * int(pct * 30)
        print(f"\r[{bar:<30}] {pct*100:4.0f}%  {msg}", end="", flush=True)

    result = build_master_catalogue(
        output_dir=args.output_dir,
        out_pdf=args.output,
        invendis_logo=args.invendis_logo or None,
        silbo_logo=args.silbo_logo or None,
        progress_callback=progress,
    )
    print()
    print(f"\n✅  Master catalogue saved: {result['pdf']}")
    print(f"   {result['page_count']} pages  |  {result['product_count']} products")
    print(f"   Categories: {', '.join(result['categories'])}")
