"""Command-line rebuild of completed catalogues (no UI).

    python build.py --all                    # completed products approved in this app
    python build.py --all --include-earlier  # ... plus those built with the earlier version (if their record exists)
    python build.py rtsxx iabxx              # specific product ids (must have a saved approved record)

Catalogues built with the earlier version are left untouched unless --include-earlier is given.
"""
import argparse
from pathlib import Path

from core import completed
from core.docx_export import build_docx
from core.render import build_pdf, safe_name
from core.sources import Store, load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-earlier", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    work = cfg["paths"]["work_dir"]
    store = Store(work)
    reg = completed.load(work)
    if a.ids:
        ids = a.ids
    elif a.all:
        ids = [p for p, e in reg.items() if e.get("source") == "app" or a.include_earlier]
    else:
        ap.error("give product ids or --all")
    for pid in ids:
        r = store.approved(pid)
        if not r:
            print(f"skip {pid}: no saved approved record (built with the earlier version or never approved)")
            continue
        out = Path(cfg["paths"]["output_dir"]) / safe_name(r)
        info = build_pdf(r, cfg, out)
        info["docx"] = build_docx(r, cfg, out, info)
        completed.mark(work, pid, r["name"], info["pdf"], info["docx"], (reg.get(pid) or {}).get("source", "app"))
        warn = f"  OVERFLOW {info['overflow_px']}px" if info["overflow_px"] else ""
        print(f"{r['name']:<20} density {info['density']}{warn} -> {out}")


if __name__ == "__main__":
    main()
