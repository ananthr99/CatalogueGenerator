"""Persist and load Master Catalogue settings (cover + index editable fields)."""
import json
from pathlib import Path

_FILENAME = "mc_settings.json"

DEFAULTS = {
    # Cover page
    "year":        "2026",
    "title_top":   "PRODUCT",
    "title_main":  "CATALOGUE",
    "tagline":     "Industrial Networking  ·  Edge Computing  ·  Smart Metering",
    "eyebrow":     "INVENDIS TECHNOLOGIES",
    # Contact footer
    "company":     "Invendis Technologies India Pvt. Ltd.",
    "address":     "No. 10, Marenahalli, Vijayanagar\nBangalore – 560 040, India",
    "email":       "sales@invendis.com",
    "phone":       "+91 6361509463",
    "website":     "www.invendis.com",
    "footer_tag":  "Invent, Discover.   |   www.invendis.com   |   sales@invendis.com",
    # Index page
    "index_title": "Product Index",
    # Category order (comma-separated); empty = auto
    "cat_order":   "Edge Computers,Computer,Routers,Gateways,Switches,Outdoor Unit,PCB,Other,Energy Meter",
    # Product name overrides: {"prod_name": "Display Name"}
    "name_overrides": {},
    # Per-product category overrides: {"prod_name": "Category"}
    "cat_overrides": {},
}


def _path(work_dir: str) -> Path:
    return Path(work_dir) / _FILENAME


def load(work_dir: str) -> dict:
    p = _path(work_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return {**DEFAULTS, **data}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(work_dir: str, settings: dict) -> None:
    p = _path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
