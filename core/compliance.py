"""Compliance marks: ready-made presets + per-product editable rows {show, icon, label}."""
import re

# Ready-made marks. "name" is what the app lists; "label" is the text printed next to the symbol
# (empty = the logo alone, for logos that already contain their name).
# logo:<name> refers to a file in assets/logos (Logo library); a missing logo falls back to a certificate symbol.
PRESETS = {
    "ce": {"name": "CE", "icon": "logo:ce", "label": ""},
    "fcc": {"name": "FCC", "icon": "logo:fcc", "label": ""},
    "rohs": {"name": "RoHS", "icon": "logo:rohs", "label": ""},
    "weee": {"name": "WEEE", "icon": "logo:weee", "label": ""},
    "iso9001": {"name": "ISO 9001 (SGS)", "icon": "logo:iso9001", "label": ""},
    "india": {"name": "Made in India", "icon": "india", "label": "Made in India"},
    "ip": {"name": "IP rating", "icon": "shield", "label": "{ip}"},   # product's IP rating; skipped if none
    "bis": {"name": "BIS", "icon": "cert", "label": "BIS"},
    "tec": {"name": "TEC", "icon": "cert", "label": "TEC"},
    "ul": {"name": "UL Listed", "icon": "cert", "label": "UL Listed"},
}


def presets(cfg=None):
    cfg = cfg or {}
    out = {k: dict(v) for k, v in PRESETS.items()}
    for k, v in (cfg.get("compliance_presets") or {}).items():   # add / override presets from config.yaml
        out[k] = {"name": v.get("name") or v.get("label") or k, "icon": v.get("icon", "cert"), "label": v.get("label", "")}
    return out


ALIASES = {"bharat": "india"}          # keys used in older configs / records


def preset_row(key, cfg, ip=""):
    key = ALIASES.get(key, key)
    p = presets(cfg).get(key)
    if not p:
        return {"show": True, "icon": "cert", "label": key}
    label = p["label"].replace("{ip}", ip or "")
    if "{ip}" in p["label"] and not ip:
        return None
    return {"show": True, "icon": p["icon"], "label": label}


def default_rows(cfg, category_cfg, ip=""):
    keys = (category_cfg or {}).get("compliance") or cfg.get("default_compliance", ["ce", "fcc", "rohs", "weee", "iso9001"])
    rows = []
    for k in keys:
        r = preset_row(k, cfg, ip) if isinstance(k, str) else {"show": True, "icon": k.get("icon", "cert"),
                                                                "label": k.get("label", "")}
        if r:
            rows.append(r)
    return rows


def normalize(rec, cfg=None):
    """Upgrade older records in place: compliance saved as a list of preset keys becomes editable rows,
    and "Made in Bharat" (badges + compliance) becomes "Made in India"."""
    comp = rec.get("compliance") or []
    if comp and all(isinstance(c, str) for c in comp):
        rows = [preset_row(c, cfg, rec.get("ip_rating", "")) for c in comp]
        rec["compliance"] = [r for r in rows if r]
    for section in ("compliance", "badges"):
        rows = rec.get(section) or []
        if any(isinstance(r, dict) and (r.get("icon") == "bharat" or "bharat" in (r.get("label") or "").lower())
               for r in rows):
            rec[section] = [_india(r) if isinstance(r, dict) else r for r in rows]
    return rec


def _india(r):
    r = dict(r)
    if r.get("icon") == "bharat":
        r["icon"] = "india"
    r["label"] = re.sub(r"(?i)made\s+in\s+bharat", "Made in India", r.get("label") or "")
    return r


def shown(rec):
    return [c for c in rec.get("compliance") or [] if isinstance(c, dict) and c.get("show", True)
            and (c.get("label") or c.get("icon"))]
