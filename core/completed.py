"""Completed register (work/completed.json): products whose catalogue has been generated and delivered.

A completed product is locked: the app shows the delivered PDF instead of the editor and never rebuilds it
unless it is reopened. Entries come from three places:
  - "app"       approved & built in this app
  - "detected"  a catalogue PDF already in the output folder (e.g. made with an earlier version of the tool)
  - "manual"    marked as completed by hand (files made elsewhere)
"""
import json
from datetime import datetime
from pathlib import Path

from .render import safe_name

FILE = "completed.json"


def _path(work_dir):
    return Path(work_dir) / FILE


def load(work_dir):
    f = _path(work_dir)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save(work_dir, reg):
    _path(work_dir).write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")


def mark(work_dir, pid, name, pdf="", docx="", source="app", when=None):
    reg = load(work_dir)
    reg[pid] = {"name": name, "pdf": str(pdf or ""), "docx": str(docx or ""), "source": source,
                "generated_at": when or datetime.now().isoformat(timespec="seconds"), "locked": True}
    save(work_dir, reg)


def set_locked(work_dir, pid, locked):
    reg = load(work_dir)
    if pid in reg:
        reg[pid]["locked"] = bool(locked)
        if not locked:
            reg[pid]["reopened_at"] = datetime.now().isoformat(timespec="seconds")
        save(work_dir, reg)


def remove(work_dir, pid):
    reg = load(work_dir)
    if reg.pop(pid, None) is not None:
        save(work_dir, reg)


def _find_files(output_dir, names):
    """Catalogue PDF/DOCX for any of the given product names: output/<NAME>/<NAME>_Catalogue.pdf, else anywhere below."""
    out = Path(output_dir)
    for n in names:
        p = out / n / f"{n}_Catalogue.pdf"
        if p.exists():
            return p, p.with_suffix(".docx") if p.with_suffix(".docx").exists() else None
    for n in names:
        hits = sorted(out.rglob(f"{n}_Catalogue.pdf"), key=lambda f: f.stat().st_mtime, reverse=True)
        if hits:
            d = hits[0].with_suffix(".docx")
            return hits[0], d if d.exists() else None
    return None, None


def detect(products, store, output_dir, work_dir):
    """Add every product that already has a catalogue PDF in the output folder to the register (locked).
    Products already in the register are left as they are (including reopened ones). Returns the new ids."""
    reg = load(work_dir)
    added = []
    for pid, j in products.items():
        if pid in reg:
            continue
        a = store.approved(pid)
        names = list(dict.fromkeys(n for n in (safe_name(a) if a else None,
                                               safe_name({"name": j.get("name") or pid})) if n))
        pdf, docx = _find_files(output_dir, names)
        if pdf:
            reg[pid] = {"name": (a or {}).get("name") or j.get("name") or pid, "pdf": str(pdf),
                        "docx": str(docx or ""), "source": "detected",
                        "generated_at": datetime.fromtimestamp(pdf.stat().st_mtime).isoformat(timespec="seconds"),
                        "locked": True}
            added.append(pid)
    if added:
        save(work_dir, reg)
    return added


def files(entry, output_dir=None):
    """Existing PDF/DOCX paths of an entry (None when the file has been moved or deleted)."""
    pdf = Path(entry.get("pdf") or "")
    docx = Path(entry.get("docx") or "")
    return (pdf if pdf.is_file() else None), (docx if docx.is_file() else None)
