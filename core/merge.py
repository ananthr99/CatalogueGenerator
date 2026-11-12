"""Merge website JSON + Master Catalogue PDF into one normalized catalogue record."""
import re
from datetime import datetime

from . import compliance
from .master_pdf import clean, norm

KEY_RENAMES = {
    "WIFI": "Wi-Fi", "WIFI(HOTSPOTANDEASYMESH)": "Wi-Fi", "WIFIHOTSPOTANDEASYMESH": "Wi-Fi",
    "INGRESSPROTECTIONRATING": "IP Rating",
}
IP_KEYS = {"IP Rating"}


def _key(k):
    k = clean(k)
    return KEY_RENAMES.get(norm(k), k)


def _rows(pairs, hidden=()):
    out, seen = [], set()
    for k, v in pairs:
        k = _key(k)
        if not k or k.upper() in seen:
            continue
        seen.add(k.upper())
        out.append({"key": k, "value": clean(v), "show": k not in hidden})
    return out


def _find(rows, *keys):
    for r in rows:
        if norm(r["key"]) in {norm(k) for k in keys}:
            return r
    return None


def nums(s):
    """Numbers not glued to a preceding letter (DDR2, MT7981 are ignored; 4G, 128MB, 9-30 count)."""
    return set(re.findall(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?", s or ""))


BRIEF_EXTRAS = {"ETHERNET": "Ethernet", "CELLULAR": "Cellular", "WI-FI": "Wi-Fi", "RAM": "RAM",
                "STORAGE": "Storage", "INTERFACE": "Interface"}
_STOP = {"and", "with", "or", "the", "of", "up", "to", "x", "based", "module", "supports", "support",
         "external", "in", "dc", "vdc", "mbps"}


def _toks(s):
    s = (s or "").lower().replace("wi-fi", "wifi")
    s = re.sub(r"\b(\d+)\s*x\s*(?=[a-z])", r"\1x ", s)   # "2xRTL8125BG" == "2x RTL8125BG"
    return {t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", s) if len(t) > 1 and t not in _STOP}


def _add_brief_extras(hw, brief, min_cover=0.7):
    """Bring brief-page details into Hardware when the detailed rows don't already say them.
    Each comma/"and"-separated item of a brief value counts as covered when >= min_cover of its
    words/numbers appear in the hardware rows. Uncovered items are appended to the matching row,
    or added as a new row."""
    for bk, label in BRIEF_EXTRAS.items():
        have = _toks(" ".join(r["key"] + " " + r["value"] for r in hw))
        items = [i.strip() for i in re.split(r",\s*|\s+and\s+", clean(brief.get(bk, ""))) if _toks(i)]
        missing = [i for i in items if len(_toks(i) & have) / len(_toks(i)) < min_cover]
        if not missing:
            continue
        v = ", ".join(missing)
        row = _find(hw, label)
        if row:
            row["value"] = f"{row['value']}; {v}" if row["value"] else v
        else:
            idx = next((i for i, r in enumerate(hw) if norm(r["key"]) in ("RESET", "ANTENNA", "ENCLOSURE")), len(hw))
            hw.insert(idx, {"key": label, "value": v, "show": True})


def _yes(v):
    return (v or "").strip().lower() not in ("", "-", "—", "no", "n/a", "na", "none", "null")


def _wifi_label(w):
    m = re.search(r"(\d+)", w or "")
    return f"Wi-Fi {m.group(1)}" if m else (w or "")


# ---------------------------------------------------------------- tiles / badges
def _cell_info(j, brief, ordering):
    gen = (j.get("cellular_gen") or "").upper()
    if not _yes(j.get("cell")) and not gen:
        return None
    gen = gen or ("5G" if "5G" in (j.get("cell") or "") else "4G")
    rows_txt = " ".join(" ".join(r) for r in (ordering or {}).get("rows", []))
    dual = "Dual" in rows_txt or bool(re.search(r"dual\s+(m\.2\s+)?modem", (j.get("cell") or "") + " " + brief.get("CELLULAR", ""), re.I))
    return {"gen": gen, "dual": dual, "gens": "5G/4G" if gen == "5G" else gen}


def _ports(j, hw, brief):
    txt = " ".join([brief.get("ETHERNET", "")] + [r["value"] for r in hw if norm(r["key"]) in ("WAN", "LAN", "ETHERNET")]
                   + [s["v"] for s in j.get("additional_specs", []) if "ethernet" in s["k"].lower() or "sfp" in s["k"].lower()])
    counts = {}
    for n, kind in re.findall(r"\b(\d+)\s*[x×]?\s*(LAN|WAN|SFP)\b", txt, re.I):
        counts.setdefault(kind.upper(), int(n))
    for s in j.get("additional_specs", []):
        if "sfp" in s["k"].lower() and "SFP" not in counts:
            ns = [int(x) for x in re.findall(r"\d+", s["v"])]
            if ns:
                counts["SFP"] = max(ns)
    if counts:
        order = [k for k in ("LAN", "WAN", "SFP") if k in counts]
        return {"icon": "ports", "value": "+".join(str(counts[k]) for k in order), "label": "+".join(order)}
    if _yes(j.get("ports")):
        gig = "1000" in txt or "gigabit" in txt.lower()
        return {"icon": "ports", "value": f"{j['ports']}×", "label": "GIGABIT ETHERNET" if gig else "ETHERNET PORTS"}
    return None


def _ram(j, hw):
    src = j.get("ram") or (_find(hw, "RAM") or {}).get("value", "")
    m = re.search(r"(\d+)\s*([GM]B)", src, re.I)
    if not m:
        return None
    ddr = re.search(r"DDR\s*\d", src, re.I)
    label = (ddr.group(0).replace(" ", "").upper() + " RAM") if ddr else "RAM"
    if re.search(r"up\s*to|max", src, re.I):
        label += " (MAX)"
    return {"icon": "chip", "value": f"{m.group(1)} {m.group(2).upper()}", "label": label}


def _wifi(j):
    w = j.get("wifi") or ""
    if not _yes(w):
        return None
    m = re.search(r"(\d+)", w)
    gen = int(m.group(1)) if m else 0
    return {"icon": "wifi", "value": _wifi_label(w), "label": "DUAL BAND" if gen >= 5 else "2.4 GHz"}


def _power(j, hw):
    src = j.get("power") or (_find(hw, "Input Power") or {}).get("value", "")
    m = re.search(r"(-?\d+)\s*[-–~]\s*(\d+)", src)
    if m:
        val = f"{m.group(1)}–{m.group(2)} V"
    else:
        ns = re.findall(r"-?\d+", src)
        if not ns:
            return None
        val = "/".join(dict.fromkeys(ns)) + " V"
    kind = "AC INPUT" if re.search(r"\bAC\b", src) and "DC" not in src else "DC INPUT"
    return {"icon": "bolt", "value": val.replace("-", "−"), "label": kind}


def _cpu(j, hw):
    src = j.get("cpu") or (_find(hw, "CPU") or {}).get("value", "")
    m = re.search(r"\b([A-Z]{1,4}\d{3,5}[A-Z]?)\b", src)
    if not m:
        return None
    cores = re.search(r"(quad|dual|single|octa|hexa|\d+)[\s-]*cores?", src, re.I)
    f = re.findall(r"(\d+(?:\.\d+)?)\s*GHz", src, re.I)
    mhz = re.search(r"(\d+)\s*MHz", src, re.I)
    parts = []
    if cores:
        c = cores.group(1)
        parts.append((f"{c} CORE" if not c.isdigit() else f"{c} CORES").upper())
    if f:
        parts.append(f"{max(f, key=float)} GHz")
    elif mhz:
        parts.append(f"{mhz.group(1)} MHz")
    return {"icon": "cpu", "value": m.group(1), "label": " · ".join(parts) or "PROCESSOR"}


def _storage(j, hw):
    src = j.get("storage") or (_find(hw, "Storage", "Flash") or {}).get("value", "")
    m = re.search(r"(\d+)\s*(GB|TB|MB)", src, re.I)
    if not m:
        return None
    t = re.search(r"SSD|eMMC|NAND|Flash", src, re.I)
    label = (t.group(0).upper() if t else "STORAGE") + (" (MAX)" if re.search(r"up\s*to", src, re.I) else "")
    return {"icon": "storage", "value": f"{m.group(1)} {m.group(2).upper()}", "label": label}


def _io(j):
    have = []
    keys = {s["k"].upper(): s["v"] for s in j.get("additional_specs", [])}
    for name in ("RS485", "RS232", "DI/O", "AI"):
        if name in keys or (name.lower() in j and _yes(j.get(name.lower()))):
            have.append(name)
    if not have:
        return None
    return {"icon": "serial", "value": "Serial I/O", "label": " · ".join(have)}


def build_tiles(j, hw, brief, ordering, plan):
    cell = _cell_info(j, brief, ordering)
    gens = {
        "cellular": lambda: cell and {"icon": "signal", "value": "Dual" if cell["dual"] else "Single",
                                      "label": f"{cell['gen']} MODEM{'S' if cell['dual'] else ''}"},
        "ports": lambda: _ports(j, hw, brief),
        "ram": lambda: _ram(j, hw),
        "wifi": lambda: _wifi(j),
        "power": lambda: _power(j, hw),
        "cpu": lambda: _cpu(j, hw),
        "storage": lambda: _storage(j, hw),
        "io": lambda: _io(j),
    }
    tiles, used = [], set()
    for name in list(plan) + ["cpu", "storage", "power", "ram", "ports", "wifi", "cellular", "io"]:
        if len(tiles) == 5 or name in used:
            continue
        used.add(name)
        t = gens[name]()
        if t:
            tiles.append(t)
    return tiles


def build_badges(j, hw, cell, ip):
    b = [{"icon": "india", "label": "Made in India"}]
    if cell:
        b.append({"icon": "signal", "label": "5G Ready" if cell["gen"] == "5G" else f"{cell['gen']} LTE"})
    if _yes(j.get("wifi")):
        b.append({"icon": "wifi", "label": _wifi_label(j["wifi"])})
    if "fanless" in ((_find(hw, "Design") or {}).get("value", "").lower()):
        b.append({"icon": "fan", "label": "Fanless Design"})
    if len(b) < 4 and ip:
        b.append({"icon": "shield", "label": f"{ip} Rated"})
    if len(b) < 4 and "din" in ((_find(hw, "Mounting") or {}).get("value", "").lower()):
        b.append({"icon": "din", "label": "DIN-Rail Mount"})
    return b[:4]


# ---------------------------------------------------------------- record
def json_hardware(j):
    hidden = set(j.get("hidden_fields") or [])
    pairs = [("CPU", j.get("cpu")), ("RAM", j.get("ram")), ("Storage", j.get("storage")),
             ("Cellular", j.get("cell")), ("Wi-Fi", _wifi_label(j.get("wifi")) if _yes(j.get("wifi")) else "")]
    for f, k in (("rs485", "RS485"), ("rs232", "RS232")):
        if f not in hidden and _yes(j.get(f)):
            pairs.append((k, j.get(f)))
    pairs += [(s["k"], s["v"]) for s in j.get("additional_specs", [])]
    pairs += [("Enclosure", j.get("housing")), ("Dimensions (W × H × D)", j.get("dims")), ("Weight", j.get("weight")),
              ("Input Power", j.get("power")), ("Operating Temp", j.get("op_temp"))]
    return [(k, v) for k, v in pairs if _yes(v)]


def normalize_ordering(o):
    if not o:
        return {"headers": [], "rows": []}
    ren = {"FINALPRODUCT": "Part Number", "NOOFMODEM": "No. of Modem", "NOOFMODEMS": "No. of Modem"}
    headers = [ren.get(norm(h), h) for h in o["headers"]]
    rows = []
    for r in o["rows"]:
        rows.append([clean(re.sub(r"\s*Modem$", "", c)) if "Modem" in headers[i] else clean(c)
                     for i, c in enumerate(r)])
    return {"headers": headers, "rows": rows}


CONFLICT_FIELDS = [
    # label, json field, hardware/software key(s) in PDF detailed, brief key
    ("CPU", "cpu", ("CPU",), None),
    ("RAM", "ram", ("RAM",), None),
    ("Storage / Flash", "storage", ("Flash", "Storage"), None),
    ("Cellular", "cell", ("Cellular",), "CELLULAR"),
    ("Dimensions", "dims", ("Dimensions (W × H × D)",), None),
    ("Weight", "weight", ("Weight",), None),
    ("Input Power", "power", ("Input Power",), "POWER"),
    ("Operating Temp", "op_temp", ("Operating Temp",), None),
    ("IP Rating", "ip", ("IP Rating",), None),
    ("Operating System", "os", ("Operating System",), "OS"),
]


def find_conflicts(j, rec, brief):
    out = []
    for label, jf, keys, bkey in CONFLICT_FIELDS:
        jv = clean(str(j.get(jf) or ""))
        if not _yes(jv):
            continue
        sec, row = None, None
        for s in ("hardware", "software", "physical"):
            row = _find(rec[s], *keys)
            if row:
                sec = s
                break
        pv = row["value"] if row else (brief.get(bkey, "") if bkey else "")
        if not pv:
            continue
        if nums(jv) != nums(pv):
            targets = [[s, row["key"]] for s in ("hardware", "software", "physical") if row and _find(rec[s], row["key"])]
            if not targets and sec is None:
                targets = [["hardware", keys[0]]]
            out.append({"field": label, "json": jv, "pdf": pv, "targets": targets, "choice": None})
    j_parts = {norm(r[-1]) for r in (j.get("variants") or {}).get("rows", [])}
    p_parts = {norm(r[-1]) for r in (rec.get("_pdf_ordering") or {}).get("rows", [])}
    if j_parts and p_parts and j_parts != p_parts:
        out.append({"field": "Ordering table", "json": ", ".join(sorted(j_parts - p_parts)) or "(same parts)",
                    "pdf": ", ".join(sorted(p_parts - j_parts)) or "(same parts)", "targets": [["ordering", ""]],
                    "choice": None})
    return out


def apply_conflict(rec, c):
    """Apply a conflict choice ('json' | 'pdf') to the record."""
    if c["field"] == "Ordering table":
        rec["ordering"] = rec["_json_ordering"] if c["choice"] == "json" else rec["_pdf_ordering"]
        return
    val = c["json"] if c["choice"] == "json" else c["pdf"]
    for sec, key in c["targets"]:
        row = _find(rec[sec], key)
        if row:
            row["value"] = val
        else:
            rec[sec].append({"key": key, "value": val, "show": True})


def build_record(j, mc, cfg, image_path="", brief_no=None, detailed_no=None, use_case_lib=None):
    use_case_lib = use_case_lib or {}
    cats = cfg.get("categories", {})
    cat = cats.get(j.get("cat"), {"singular": j.get("cat", "Product"), "plural": j.get("cat", "Products"),
                                    "tiles": cfg.get("default_tiles")})
    brief = {}
    pdf_ordering = None
    det = {}
    if mc and brief_no:
        b = mc.parse_brief(brief_no)
        brief, pdf_ordering = b.specs, normalize_ordering(b.ordering)
    if mc and detailed_no:
        det = mc.parse_detailed(detailed_no).sections

    hw_hidden = set(cfg.get("hardware_default_hidden", []))
    sw_hidden = set(cfg.get("software_default_hidden", []))
    hw_pairs = det.get("HARDWARE") or det.get("SPECIFICATIONS") or json_hardware(j)
    hw = _rows(hw_pairs, hw_hidden)
    if not any(norm(r["key"]) in ("WAN", "LAN", "ETHERNET", "ETHERNETPORTS") for r in hw):
        eth = brief.get("ETHERNET", "") \
              or next((s["v"] for s in j.get("additional_specs", []) if s["k"].lower().startswith("ethernet")), "")
        if eth:
            idx = next((i for i, r in enumerate(hw) if norm(r["key"]) in ("WIFI", "USB", "SIM")), len(hw))
            hw.insert(idx, {"key": "Ethernet", "value": clean(eth), "show": True})
    if not _find(hw, "Cellular") and (brief.get("CELLULAR") or _yes(j.get("cell"))):
        idx = next((i for i, r in enumerate(hw) if norm(r["key"]) in ("SIM", "USB", "WIFI")), len(hw))
        hw.insert(idx, {"key": "Cellular", "value": clean(brief.get("CELLULAR") or j.get("cell")), "show": True})
    _add_brief_extras(hw, brief)
    sw_pairs = det.get("SOFTWARE") or ([("Operating System", j.get("os"))] if _yes(j.get("os")) else [])
    sw = _rows(sw_pairs, sw_hidden)

    ip_row = _find(hw, "IP Rating")
    ip = clean(j.get("ip") or (ip_row or {}).get("value", "")) if _yes(j.get("ip")) or ip_row else ""

    phys = []
    for key in ("Dimensions (W × H × D)", "IP Rating", "Enclosure", "Mounting", "Design", "Input Power", "Weight"):
        r = _find(hw, key)
        v = r["value"] if r else ""
        if key == "IP Rating":
            v = ip
        if key == "Input Power" and len(brief.get("POWER", "")) > len(v):
            v = brief["POWER"]
        if v:
            phys.append({"key": key, "value": v, "show": True})
    # rows shown under Physical Specifications are not repeated under Hardware
    phys_keys = {norm(p["key"]) for p in phys}
    hw_all, hw = hw, [r for r in hw if norm(r["key"]) not in phys_keys]

    json_ordering = normalize_ordering(j.get("variants"))
    ordering = json_ordering if json_ordering["rows"] else (pdf_ordering or {"headers": [], "rows": []})

    cell = _cell_info(j, brief, ordering)
    name = j.get("name") or j.get("id")
    feature = ""
    if cell:
        feature = f"{'Dual Modem' if cell['dual'] else ''} {cell['gens']}".strip()
    elif _yes(j.get("wifi")):
        feature = _wifi_label(j["wifi"])
    subtitle = " ".join(x for x in ["Industrial", cell["gens"] if cell else "",
                                    "Dual Modem" if cell and cell["dual"] else "", cat["singular"]] if x)
    rec = {
        "id": j["id"], "name": name, "order": j.get("order") or 0,
        "category": cat["singular"], "category_plural": cat["plural"],
        "eyebrow": [cat["singular"].upper()] + ([feature.upper()] if feature else []),
        "subtitle": subtitle, "bar_title": subtitle,
        "description": clean(j.get("desc", "")),
        "image": str(image_path or ""),
        "tiles": [], "badges": [],
        "hardware": hw, "software": sw, "physical": phys,
        "ordering": ordering,
        "use_cases": [{"title": u, "desc": use_case_lib.get(u, "")} for u in j.get("use_cases", [])],
        "compliance": compliance.default_rows(cfg, cat, ip),
        "ip_rating": ip,
        "density": 1.0,
        "sources": {"json": j["id"], "brief_page": brief_no, "detailed_page": detailed_no},
        "status": "draft", "created": datetime.now().isoformat(timespec="seconds"),
        "_json_ordering": json_ordering, "_pdf_ordering": pdf_ordering or {"headers": [], "rows": []},
    }
    rec["tiles"] = build_tiles(j, hw_all, brief, ordering, cat.get("tiles") or cfg.get("default_tiles", []))
    rec["badges"] = build_badges(j, hw_all, cell, ip)
    rec["conflicts"] = find_conflicts(j, rec, brief)
    return rec
