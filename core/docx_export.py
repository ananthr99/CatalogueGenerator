"""Editable Word version of the catalogue page (same content and section order as the PDF)."""
import copy
import io
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

import segno

from . import compliance
from .footer import company_ctx, effective_footer
from .icons import icon_png, is_logo
from .render import layout, safe_name
from .sources import prepared_image

PAGE_W = 186  # usable width in mm (A4 210 - 2 x 12)


def _hex(c):
    return RGBColor.from_string(c.lstrip("#").upper())


def _shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color.lstrip("#").upper())
    tcPr.append(shd)


def _borders(table, color="E3E6EE", inside_h=True, outer=False, inside_v=False):
    tblPr = table._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        on = (edge == "insideH" and inside_h) or (edge == "insideV" and inside_v) or \
             (edge in ("top", "left", "bottom", "right") and outer) or (edge == "bottom" and inside_h)
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single" if on else "nil")
        if on:
            e.set(qn("w:sz"), "4")
            e.set(qn("w:color"), color)
        b.append(e)
    tblPr.append(b)


def _cell_margins(table, top=40, bottom=40, left=80, right=80):
    tblPr = table._tbl.tblPr
    m = OxmlElement("w:tblCellMar")
    for side, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        m.append(e)
    tblPr.append(m)


def _widths(table, mm):
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    grid = table._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), mm):
        gc.set(qn("w:w"), str(int(Mm(w).twips)))
    for row in table.rows:
        for c, w in zip(row.cells, mm):
            c.width = Mm(w)


def _para(cell_or_doc, text="", size=9, bold=False, color="2B2F3A", italic=False, align=None, space_after=0, first=False):
    if first and hasattr(cell_or_doc, "paragraphs") and cell_or_doc.paragraphs and not cell_or_doc.paragraphs[0].text:
        p = cell_or_doc.paragraphs[0]
    else:
        p = cell_or_doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    if align:
        p.alignment = align
    if text:
        _run(p, text, size, bold, color, italic)
    return p


def _run(p, text, size=9, bold=False, color="2B2F3A", italic=False, font=None):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold
    r.italic = italic
    r.font.color.rgb = _hex(color)
    if font:
        r.font.name = font
        r._element.rPr.rFonts.set(qn("w:eastAsia"), font)
    return r


def _bottom_rule(p, color):
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "10"), ("w:space", "2"), ("w:color", color.lstrip("#"))):
        b.set(qn(k), v)
    bdr.append(b)
    pPr.append(bdr)


def build_docx(r, cfg, out_dir, fit=None):
    """fit: the result of build_pdf (density, compact) so the Word tables shrink the same way the PDF did."""
    fit = fit or {}
    r = compliance.normalize(copy.deepcopy(r), cfg)
    dens = max(float(fit.get("density") or 1), 0.8)
    compact = bool(fit.get("compact"))
    fs = lambda size: round(size * dens * 2) / 2          # table text size, in 0.5pt steps
    pad = 0 if compact else 12                              # table row padding (twips)
    navy, red, blue = cfg["brand"]["primary_dark"], cfg["brand"]["accent"], cfg["brand"]["primary"]
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Mm(12))
    sec.top_margin, sec.bottom_margin, sec.footer_distance = Mm(10), Mm(10), Mm(4)
    st = doc.styles["Normal"]
    st.font.name = cfg.get("docx_font", "Arial")
    st.element.rPr.rFonts.set(qn("w:eastAsia"), st.font.name)
    st.font.size = Pt(9)
    st.paragraph_format.space_after = Pt(0)

    # header: logo | navy bar
    t = doc.add_table(rows=1, cols=2)
    _widths(t, [40, PAGE_W - 40])
    _borders(t, inside_h=False)
    lc, bc = t.rows[0].cells
    p = _para(lc, first=True)
    _run(p, "Inven", 22, color=blue, font="Times New Roman")
    _run(p, "dis", 22, color=red, italic=True, font="Times New Roman")
    _para(lc, cfg["company"]["tagline"], 7.5, color="444444", align=WD_ALIGN_PARAGRAPH.RIGHT)
    _shade(bc, navy)
    bc.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = _para(bc, first=True)
    p.paragraph_format.tab_stops.add_tab_stop(Mm(PAGE_W - 40 - 4), WD_TAB_ALIGNMENT.RIGHT)
    _run(p, f"{r['name']}  ·  {r['bar_title']}", 10.5, True, "FFFFFF")
    _run(p, f"\t{r['category_plural']}  |  Page {int(r.get('order') or 0):02d}", 9, color="C9D1F3")
    _para(doc, size=4)

    # hero: image | intro | badges
    t = doc.add_table(rows=1, cols=3)
    _widths(t, [62, 88, 36])
    _borders(t, inside_h=False)
    ic, tc, gc = t.rows[0].cells
    for c in (ic, tc, gc):
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    if r.get("image") and Path(r["image"]).exists():
        p = _para(ic, first=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        p.add_run().add_picture(io.BytesIO(prepared_image(r["image"], "PNG")), width=Mm(58))
    _para(tc, "  ·  ".join(e for e in r["eyebrow"] if e), 8, True, red, first=True)
    _para(tc, r["name"], 26, True, navy)
    _para(tc, r["subtitle"], 13, True, navy, space_after=4)
    _para(tc, r["description"], 8.5, color="3D4250")
    if r["badges"]:
        bt = gc.add_table(rows=0, cols=2)
        _borders(bt, inside_h=False)
        _cell_margins(bt, 50, 50, 0, 40)
        for b in r["badges"]:
            icn, lab = bt.add_row().cells
            icn.vertical_alignment = lab.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _icon_pic(_para(icn, first=True, align=WD_ALIGN_PARAGRAPH.CENTER), b.get("icon"), navy, 6.5, 11)
            if b.get("label"):
                _para(lab, b["label"], 8.5, True, navy, first=True)
        _widths(bt, [12, 22])
    _para(doc, size=4)

    # tiles
    if r["tiles"]:
        n = len(r["tiles"])
        t = doc.add_table(rows=1, cols=n)
        _widths(t, [PAGE_W / n] * n)
        _borders(t, inside_h=False, outer=True, inside_v=True)
        _cell_margins(t, 90, 90)
        for c, tile in zip(t.rows[0].cells, r["tiles"]):
            _shade(c, "FBFCFE")
            _para(c, tile["value"], 13, True, navy, first=True, align=WD_ALIGN_PARAGRAPH.CENTER)
            _para(c, tile["label"], 7.5, color="5D6475", align=WD_ALIGN_PARAGRAPH.CENTER)
        _para(doc, size=6)

    # two columns
    col_w = (PAGE_W - 6) / 2
    t = doc.add_table(rows=1, cols=3)
    _widths(t, [col_w, 6, col_w])
    _borders(t, inside_h=False)
    _cell_margins(t, 0, 0, 0, 0)
    for cell, sections in zip((t.rows[0].cells[0], t.rows[0].cells[2]), layout(r, fit.get("arrangement"))):
        first = True
        for s in sections:
            p = _para(cell, first=first, space_after=3)
            if not first:
                p.paragraph_format.space_before = Pt(6 if compact else 9)
            first = False
            _run(p, s["title"].upper(), 10, True, navy)
            _bottom_rule(p, navy)
            if s["kind"] == "kv":
                kt = cell.add_table(rows=0, cols=2)
                _borders(kt)
                _cell_margins(kt, pad, pad, 60, 60)
                for row in s["rows"]:
                    k, v = kt.add_row().cells
                    _shade(k, "F4F6FA")
                    _para(k, row["key"], fs(7.5), True, "2A3150", first=True)
                    _para(v, row["value"], fs(7.5), first=True)
                _widths(kt, [col_w * 0.36, col_w * 0.64])
            elif s["kind"] == "ordering":
                hdr = s["headers"]
                ot = cell.add_table(rows=1, cols=len(hdr))
                _borders(ot)
                _cell_margins(ot, pad, pad, 30, 30)
                for c, h in zip(ot.rows[0].cells, hdr):
                    _shade(c, navy)
                    _para(c, h, fs(7), True, "FFFFFF", first=True, align=WD_ALIGN_PARAGRAPH.CENTER)
                for i, row in enumerate(s["rows"]):
                    cells = ot.add_row().cells
                    for j, (c, v) in enumerate(zip(cells, row)):
                        if i % 2:
                            _shade(c, "F4F6FA")
                        last = j == len(row) - 1
                        _para(c, v, fs(7.5), last or v == "✓", blue if last else "2B2F3A", first=True,
                              align=WD_ALIGN_PARAGRAPH.CENTER)
                last_w = col_w * 0.3
                _widths(ot, [(col_w - last_w) / max(1, len(hdr) - 1)] * (len(hdr) - 1) + [last_w])
                _para(cell, "✓ = Included     — = Not included", 7, color="5D6475").paragraph_format.space_before = Pt(2)
            elif s["kind"] == "ordering_image":
                ip = _para(cell, first=False, align=WD_ALIGN_PARAGRAPH.CENTER)
                ip.paragraph_format.space_before = Pt(3)
                ip.add_run().add_picture(io.BytesIO(prepared_image(s["rows"][0], "PNG")), width=Mm(col_w - 1))
            elif s["kind"] == "usecases":
                for u in s["rows"]:
                    ut = cell.add_table(rows=1, cols=1)
                    _borders(ut, inside_h=False)
                    _cell_margins(ut, 10 if compact else 30, 10 if compact else 30, 110, 80)
                    c = ut.rows[0].cells[0]
                    _shade(c, "F1F4FB")
                    tcPr = c._tc.get_or_add_tcPr()
                    tb = OxmlElement("w:tcBorders")
                    left = OxmlElement("w:left")
                    for k, v in (("w:val", "single"), ("w:sz", "24"), ("w:color", red.lstrip("#"))):
                        left.set(qn(k), v)
                    tb.append(left)
                    tcPr.append(tb)
                    _para(c, u["title"], 9, True, navy, first=True)
                    if u.get("desc"):
                        _para(c, u["desc"], 7.5, color="5D6475")
                    _widths(ut, [col_w])
                    _para(cell, size=2)
            elif s["kind"] == "compliance":
                # marks side by side from the left: a narrow slot for a logo on its own, a wider one when
                # there is a label; a new row starts when the column is full
                marks = s["rows"]
                width = lambda m: 25 if m.get("label") else 16
                rows, cur, used = [], [], 0
                for m in marks:
                    if cur and used + width(m) > col_w:
                        rows.append(cur)
                        cur, used = [], 0
                    cur.append(m)
                    used += width(m)
                if cur:
                    rows.append(cur)
                for row_marks in rows:
                    ct = cell.add_table(rows=1, cols=len(row_marks))
                    _borders(ct, inside_h=False)
                    _cell_margins(ct, 70, 20, 20, 20)
                    for c, m in zip(ct.rows[0].cells, row_marks):
                        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
                        _icon_pic(_para(c, first=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=1),
                                  m.get("icon"), "#111111", 6 if m.get("label") else 9, width(m) - 3)
                        if m.get("label"):
                            _para(c, m["label"], 7.5, True, "333333", align=WD_ALIGN_PARAGRAPH.CENTER)
                    _widths(ct, [width(m) for m in row_marks])
                    ct.alignment = WD_TABLE_ALIGNMENT.LEFT

    _compact_empty_paragraphs(doc)

    # footer: contact details left, QR code right
    foot = effective_footer(r, cfg)
    co = company_ctx(foot, cfg)
    q = {"url": foot["qr_url"].strip(), "title": foot["qr_title"], "caption": foot["qr_caption"]}
    ft = sec.footer.add_table(rows=1, cols=2, width=Mm(PAGE_W))
    _borders(ft, inside_h=False)
    _cell_margins(ft, 60, 0, 0, 0)
    _widths(ft, [PAGE_W - 70, 70])
    left, right = ft.rows[0].cells
    for c in (left, right):
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    lines = [(co["name"], 7.5, True, navy),
             ("  ·  ".join(x for x in (co["city"], co["email"], co["phone"]) if x), 7, False, "7A8090"),
             (" | ".join(co["websites"]), 7, False, "7A8090")]
    first = True
    for text, size, bold, color in lines:
        if text:
            _para(left, text, size, bold, color, first=first, space_after=2)
            first = False
    if q.get("url"):
        qt = right.add_table(rows=1, cols=2)
        _borders(qt, color="FFFFFF", inside_h=False)
        _cell_margins(qt, 0, 0, 40, 0)
        _widths(qt, [52, 18])
        tc, ic = qt.rows[0].cells
        tc.vertical_alignment = ic.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        _para(tc, q.get("title", ""), 8, True, navy, align=WD_ALIGN_PARAGRAPH.RIGHT, first=True, space_after=1)
        _para(tc, q.get("caption", ""), 7, color="7A8090", align=WD_ALIGN_PARAGRAPH.RIGHT)
        buf = io.BytesIO()
        segno.make(q["url"], error="m").save(buf, kind="png", scale=10, border=2,
                                              dark=cfg["brand"].get("primary_dark", "#0B1F7A"))
        ip = ic.paragraphs[0]
        ip.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        ip.add_run().add_picture(io.BytesIO(buf.getvalue()), width=Mm(15), height=Mm(15))
        right.paragraphs[0].paragraph_format.space_after = Pt(0)
    # the footer's default empty paragraph sits above the table; move it below (Word needs one after a table)
    # and collapse it to a hairline so it adds no height
    fpara = sec.footer.paragraphs[0]._p
    ft._tbl.addnext(fpara)
    pPr = fpara.get_or_add_pPr()
    sp = OxmlElement("w:spacing")
    for k, v in (("w:before", "0"), ("w:after", "0"), ("w:line", "20"), ("w:lineRule", "exact")):
        sp.set(qn(k), v)
    pPr.append(sp)
    _top_border_table(ft)

    out = Path(out_dir) / f"{safe_name(r)}_Catalogue.docx"
    doc.save(out)
    return str(out)


def _compact_empty_paragraphs(doc):
    """python-docx leaves an empty paragraph after every nested table; shrink them so they add no height."""
    for p in doc.element.body.iter(qn("w:p")):
        if not "".join(t.text or "" for t in p.iter(qn("w:t"))) and not list(p.iter(qn("w:drawing"))):
            pPr = p.get_or_add_pPr()
            sp = OxmlElement("w:spacing")
            for k, v in (("w:before", "0"), ("w:after", "0"), ("w:line", "40"), ("w:lineRule", "exact")):
                sp.set(qn(k), v)
            pPr.append(sp)


def _bottom_rule_top(p):
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:top")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "4"), ("w:color", "CFD3DC")):
        b.set(qn(k), v)
    bdr.append(b)
    pPr.append(bdr)


def _top_border_table(table, color="CFD3DC"):
    tblPr = table._tbl.tblPr
    b = tblPr.find(qn("w:tblBorders"))
    if b is None:
        b = OxmlElement("w:tblBorders")
        tblPr.append(b)
    for e in b.findall(qn("w:top")):
        b.remove(e)
    t = OxmlElement("w:top")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "0"), ("w:color", color)):
        t.set(qn(k), v)
    b.insert(0, t)


def _icon_pic(p, name, color, h_mm, max_w_mm):
    """Icon/logo picture h_mm tall, narrowed to max_w_mm if the image is wide (keeps aspect ratio)."""
    if not name:
        return
    try:
        png = icon_png(name, color, 96)
        from PIL import Image
        w, h = Image.open(io.BytesIO(png)).size
    except Exception:
        return
    width = min(h_mm * w / h, max_w_mm)
    p.add_run().add_picture(io.BytesIO(png), width=Mm(width))
