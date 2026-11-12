"""Record -> HTML (Jinja) -> PDF (Playwright/Chromium in a subprocess)."""
import base64
import copy
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import segno
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .footer import company_ctx, effective_footer
from . import compliance
from .icons import icon, is_logo
from .sources import ROOT, prepared_image

ENV = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html", "j2"]))


@lru_cache(maxsize=1)
def _fonts():
    f = ROOT / "assets" / "fonts"
    return {"roboto": base64.b64encode((f / "Roboto.woff2").read_bytes()).decode(),
            "condensed": base64.b64encode((f / "RobotoCondensed.woff2").read_bytes()).decode()}


@lru_cache(maxsize=64)
def _image_uri(path, mtime):
    return "data:image/webp;base64," + base64.b64encode(prepared_image(path, "WEBP")).decode()


@lru_cache(maxsize=8)
def qr_svg_uri(url, color="#0B1F7A"):
    """Vector QR (data URI) for the HTML/PDF page."""
    return segno.make(url, error="m").svg_data_uri(scale=4, border=2, dark=color)


def qr_display_url(url):
    return url.split("://", 1)[-1].rstrip("/")


def _logo_uri():
    for ext, mime in ((".svg", "image/svg+xml"), (".png", "image/png")):
        p = ROOT / "assets" / f"logo{ext}"
        if p.exists():
            return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    return None


MOVABLE = ["physical", "ordering", "usecases", "compliance"]          # canonical order within a column
DEFAULT_SIDE = {"physical": "left", "ordering": "left", "usecases": "right", "compliance": "right"}


def layout(r, arrangement=None):
    """Two columns of sections. Hardware is always top-left and Software top-right; the other sections
    start on their default side and are re-arranged on the page (fitPage) to fit one A4 sheet.
    arrangement = {"left": [ids], "right": [ids]} (from the PDF build) applies a known arrangement."""
    shown = lambda rows: [x for x in rows if x.get("show", True) and (x.get("key") or x.get("value"))]
    o = r.get("ordering") or {}
    secs = {"hardware": ("Hardware Specifications", "kv", shown(r["hardware"])),
            "software": ("Software & Networking Specifications", "kv", shown(r["software"])),
            "physical": ("Physical Specifications", "kv", shown(r["physical"]))}
    oimg = ordering_image(r)
    secs["ordering"] = ("Ordering Information", "ordering_image", [oimg]) if oimg else \
        ("Ordering Information", "ordering", o.get("rows", []))
    secs["usecases"] = ("Typical Applications / Use Cases", "usecases", [u for u in r["use_cases"] if u.get("title")])
    secs["compliance"] = ("Compliance", "compliance",
                          [dict(c, cls="ce" if c.get("icon") == "ce" else ("mk" if is_logo(c.get("icon")) else ""))
                           for c in compliance.shown(compliance.normalize(dict(r)))])
    pins = r.get("placement") or {}
    if arrangement:
        cols_ids = [list(arrangement.get("left", [])), list(arrangement.get("right", []))]
    else:
        side = {m: pins.get(m) if pins.get(m) in ("left", "right") else DEFAULT_SIDE[m] for m in MOVABLE}
        cols_ids = [["hardware"] + [m for m in MOVABLE if side[m] == "left"],
                    ["software"] + [m for m in MOVABLE if side[m] == "right"]]
    placed = {i for c in cols_ids for i in c}
    for i in ["hardware", "physical", "ordering"]:          # anything missing from a stale arrangement
        if i not in placed:
            cols_ids[0].append(i)
    for i in ["software", "usecases", "compliance"]:
        if i not in placed:
            cols_ids[1].append(i)
    cols = []
    for ids in cols_ids:
        out = []
        for sid in ids:
            title, kind, rows = secs[sid]
            if not rows:
                continue
            sec = {"id": sid, "title": title, "kind": kind, "rows": rows,
                   "pin": pins.get(sid, "auto") if sid in MOVABLE else "fixed"}
            if kind == "ordering":
                sec["headers"] = o.get("headers", [])
            out.append(sec)
        cols.append(out)
    return cols


def ordering_image(r):
    """Path of the product's ordering-information image when it should replace the table."""
    p = r.get("ordering_image")
    if not p or not Path(p).exists():
        return None
    has_table = bool((r.get("ordering") or {}).get("rows"))
    mode = r.get("ordering_mode") or ("table" if has_table else "image")
    return p if mode == "image" else None


def render_html(r, cfg, preview=False):
    r = compliance.normalize(copy.deepcopy(r), cfg)          # older records: e.g. Made in Bharat -> Made in India
    foot = effective_footer(r, cfg)
    img = r.get("image")
    image = _image_uri(img, Path(img).stat().st_mtime) if img and Path(img).exists() else None
    return ENV.get_template("catalogue.html.j2").render(
        r=r, columns=layout(r), image=image, ordering_img=_ordering_img_uri(r), logo=_logo_uri(), fonts=_fonts(), icon=icon,
        company=company_ctx(foot, cfg), brand=cfg["brand"], preview=preview,
        qr=qr_ctx(foot, cfg),
        min_density=cfg.get("min_density", 0.86))


def _ordering_img_uri(r):
    p = ordering_image(r)
    if not p:
        return None
    return "data:image/png;base64," + base64.b64encode(_ordimg_png(p, Path(p).stat().st_mtime)).decode()


@lru_cache(maxsize=16)
def _ordimg_png(path, mtime):
    return prepared_image(path, "PNG")


def qr_ctx(foot, cfg):
    url = (foot.get("qr_url") or "").strip()
    if not url:
        return None
    return {"img": qr_svg_uri(url, cfg["brand"].get("primary_dark", "#0B1F7A")), "url": url,
            "display": qr_display_url(url), "title": foot.get("qr_title", ""), "caption": foot.get("qr_caption", "")}


def safe_name(r):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in r["name"])


def build_pdf(r, cfg, out_dir):
    """Write HTML + PDF. Auto-reduces density (down to cfg min_density) if the page overflows."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{safe_name(r)}_Catalogue"
    html_path = base.with_suffix(".html")
    html_path.write_text(render_html(r, cfg), encoding="utf-8")
    pdf_path = base.with_suffix(".pdf")
    res = subprocess.run([sys.executable, "-m", "core.pdf_worker", str(html_path), str(pdf_path),
                          str(r.get("density") or 1.0), str(cfg.get("min_density", 0.86))],
                         cwd=ROOT, capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-1500:] or "PDF rendering failed")
    info = json.loads(res.stdout.strip().splitlines()[-1])
    info.update(html=str(html_path), pdf=str(pdf_path))
    return info
