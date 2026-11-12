"""Inline SVG icons (24x24, currentColor) plus uploaded logos ("logo:<name>" from assets/logos).
Names are what the record's tiles, badges and compliance rows reference."""
import base64
import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image

from .sources import ROOT

_S = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{}</svg>'
_F = '<svg viewBox="0 0 24 24" fill="currentColor">{}</svg>'

ICONS = {
    "signal": _F.format('<rect x="3" y="15" width="3.6" height="6" rx="1"/><rect x="8.2" y="11" width="3.6" height="10" rx="1"/>'
                        '<rect x="13.4" y="7" width="3.6" height="14" rx="1"/><rect x="18.6" y="3" width="3.6" height="18" rx="1"/>'),
    "ports": _S.format('<rect x="2" y="6" width="20" height="13" rx="2"/><path d="M7 19v-4h10v4"/><path d="M9 15v-2h6v2"/>'
                       '<path d="M6 9h1M10 9h1M14 9h1M18 9h1"/>'),
    "chip": _S.format('<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5"/>'
                      '<path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'),
    "cpu": _S.format('<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9z"/>'
                     '<path d="M9 1.5v3.5M15 1.5v3.5M9 19v3.5M15 19v3.5M1.5 9H5M1.5 15H5M19 9h3.5M19 15h3.5"/>'),
    "wifi": _S.format('<path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5.2 12.4a10 10 0 0 1 13.6 0"/>'
                      '<path d="M8.6 15.9a5 5 0 0 1 6.8 0"/><circle cx="12" cy="19.5" r="1.2" fill="currentColor"/>'),
    "bolt": _S.format('<circle cx="12" cy="12" r="10"/><path d="M13 5.5 8 13h4l-1 5.5 5-7.5h-4z" fill="currentColor"/>'),
    "storage": _S.format('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18"/><circle cx="17" cy="17" r="1" fill="currentColor"/>'
                         '<path d="M7 8h6"/>'),
    "serial": _S.format('<path d="M3 7h18l-2.5 10h-13z"/><circle cx="8" cy="11" r=".9" fill="currentColor"/>'
                        '<circle cx="12" cy="11" r=".9" fill="currentColor"/><circle cx="16" cy="11" r=".9" fill="currentColor"/>'
                        '<circle cx="10" cy="14" r=".9" fill="currentColor"/><circle cx="14" cy="14" r=".9" fill="currentColor"/>'),
    "fan": _S.format('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1.6"/>'
                     '<path d="M12 10.4c0-3 1-5.4 3-5.4s2 3-3 5.4zM13.6 12c3 0 5.4 1 5.4 3s-3 2-5.4-3zM12 13.6c0 3-1 5.4-3 5.4s-2-3 3-5.4zM10.4 12c-3 0-5.4-1-5.4-3s3-2 5.4 3z"/>'),
    "shield": _S.format('<path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>'),
    "din": _S.format('<path d="M2 8h20M2 16h20"/><rect x="7" y="5" width="10" height="14" rx="1.5"/>'),
    "gps": _S.format('<circle cx="12" cy="10" r="3"/><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/>'),
    "usb": _S.format('<path d="M12 2v15"/><circle cx="12" cy="19.5" r="2.5"/><path d="M12 7 8 10v3M12 11l4-3V6"/>'
                     '<path d="m10.5 4 1.5-2 1.5 2"/>'),
    "temp": _S.format('<path d="M14 14.8V4a2 2 0 0 0-4 0v10.8a4 4 0 1 0 4 0z"/>'),
    "leaf": '<svg viewBox="0 0 24 24"><path d="M20.5 3.5C9 3.5 4 9 4 15.5c0 1.6.4 3 1 4.2C8 12 13 9 16.5 8 12 10.5 8.6 14.5 6.6 20.8c1 .5 2.2.7 3.4.7 7.5 0 10.5-7 10.5-18z" fill="#2E9E3E"/></svg>',
    "ce": '<svg viewBox="0 0 40 24"><path d="M14 4.2A8 8 0 1 0 14 19.8" fill="none" stroke="currentColor" stroke-width="2.6"/>'
          '<path d="M33 4.2A8 8 0 1 0 33 19.8M23 12h8" fill="none" stroke="currentColor" stroke-width="2.6"/></svg>',
    "bharat": '<svg viewBox="0 0 24 24">'
              '<path d="M1.753 8A11 11 0 0 1 22.247 8Z" fill="#FF9933"/>'
              '<path d="M1.753 8H22.247A11 11 0 0 1 22.247 16H1.753A11 11 0 0 1 1.753 8Z" fill="#fff"/>'
              '<path d="M22.247 16A11 11 0 0 1 1.753 16Z" fill="#138808"/>'
              '<circle cx="12" cy="12" r="11" fill="none" stroke="#ddd" stroke-width=".6"/>'
              '<circle cx="12" cy="12" r="2.6" fill="none" stroke="#000080" stroke-width=".8"/><circle cx="12" cy="12" r=".7" fill="#000080"/></svg>',
    "india": None,  # filled below (same flag symbol as "bharat")
    "cert": _S.format('<circle cx="12" cy="9" r="6.5"/><path d="m9.2 9 2 2 3.6-3.8"/><path d="M8.3 14.4 6.5 22l5.5-3 5.5 3-1.8-7.6"/>'),
    "check": _S.format('<circle cx="12" cy="12" r="10"/><path d="m7.5 12.3 3 3 6-6.3"/>'),
}

ICONS["india"] = ICONS["bharat"]

TILE_ICONS = ["signal", "ports", "chip", "cpu", "wifi", "bolt", "storage", "serial", "gps", "usb", "temp", "shield", "din", "fan"]
BADGE_ICONS = ["india", "signal", "ports", "wifi", "fan", "shield", "din", "bolt", "cpu", "chip", "storage", "serial",
               "usb", "gps", "temp", "cert", "check", "leaf"]
COMPLIANCE_ICONS = ["leaf", "ce", "india", "shield", "cert", "check"]

LOGO_DIR = ROOT / "assets" / "logos"
LOGO_EXT = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


# ---------------------------------------------------------------- uploaded logos ("logo:<name>")
def logo_files():
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    return {f.stem: f for f in sorted(LOGO_DIR.iterdir()) if f.suffix.lower() in LOGO_EXT}


def logo_options():
    return [f"logo:{n}" for n in logo_files()]


def save_logo(name, data, filename):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or Path(filename).stem).lower()).strip("-") or "logo"
    ext = Path(filename).suffix.lower()
    if ext not in LOGO_EXT:
        raise ValueError(f"Unsupported logo type {ext}")
    for old in logo_files().values():          # replace a logo of the same name in another format
        if old.stem == slug:
            old.unlink()
    (LOGO_DIR / f"{slug}{ext}").write_bytes(data)
    return f"logo:{slug}"


def delete_logo(name):
    f = logo_files().get(name.removeprefix("logo:"))
    if f:
        f.unlink()


@lru_cache(maxsize=64)
def _logo_uri(path, mtime):
    p = Path(path)
    return f"data:{LOGO_EXT[p.suffix.lower()]};base64," + base64.b64encode(p.read_bytes()).decode()


@lru_cache(maxsize=64)
def _logo_size(path, mtime):
    if Path(path).suffix.lower() == ".svg":
        return 0, 0
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


def is_logo(name):
    return (name or "").startswith("logo:")


def icon(name):
    if is_logo(name):
        f = logo_files().get(name[5:])
        if f:
            w, h = _logo_size(str(f), f.stat().st_mtime)
            size = f' width="{w}" height="{h}"' if w and h else ""     # lets the layout know the shape up front
            return f'<img class="mk" src="{_logo_uri(str(f), f.stat().st_mtime)}"{size} alt="{f.stem}">'
        return ICONS["cert"]
    return ICONS.get(name, ICONS["chip"])


# ---------------------------------------------------------------- PNG versions for the Word file
def _svg_png(svg, px):
    import pymupdf
    if "xmlns" not in svg:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    doc = pymupdf.open(stream=svg.encode(), filetype="svg")
    pg = doc[0]
    z = px / max(pg.rect.width, pg.rect.height)
    return pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=True).tobytes("png")


@lru_cache(maxsize=128)
def _icon_png(name, color, px, mtime):
    if is_logo(name):
        f = logo_files().get(name[5:])
        if f and f.suffix.lower() == ".svg":
            return _svg_png(f.read_text(encoding="utf-8"), px * 3)
        if f:
            im = Image.open(f)
            im = im.convert("RGBA")
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
        name = "cert"
    return _svg_png(ICONS.get(name, ICONS["chip"]).replace("currentColor", color), px)


def icon_png(name, color="#111111", px=96):
    """PNG bytes for an icon or uploaded logo; aspect ratio is preserved."""
    f = logo_files().get(name[5:]) if is_logo(name) else None
    return _icon_png(name, color, px, f.stat().st_mtime if f else 0)
