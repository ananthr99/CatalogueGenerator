"""Footer = shared settings (config.yaml: company + qr) with optional per-product overrides (rec["footer"])."""
import re

from .sources import ROOT

# field -> (section in config, key in that section, label)
FIELDS = {
    "name": ("company", "name", "Company name"),
    "city": ("company", "city", "Address line"),
    "email": ("company", "email", "Email"),
    "phone": ("company", "phone", "Phone"),
    "websites": ("company", "websites", "Websites (comma-separated)"),
    "qr_url": ("qr", "url", "QR code link (empty = no QR code)"),
    "qr_title": ("qr", "title", "QR title"),
    "qr_caption": ("qr", "caption", "QR caption"),
}


def _as_text(v):
    return ", ".join(v) if isinstance(v, list) else str(v or "")


def shared_footer(cfg):
    """Shared footer as flat text fields."""
    return {f: _as_text((cfg.get(sec) or {}).get(key, "")) for f, (sec, key, _) in FIELDS.items()}


def effective_footer(rec, cfg):
    """Shared footer with this product's overrides applied."""
    f = shared_footer(cfg)
    f.update({k: v for k, v in ((rec or {}).get("footer") or {}).items() if k in FIELDS})
    return f


def split_websites(s):
    return [w.strip() for w in re.split(r"[,|]", s or "") if w.strip()]


def company_ctx(f, cfg):
    return {"name": f["name"], "city": f["city"], "email": f["email"], "phone": f["phone"],
            "websites": split_websites(f["websites"]), "tagline": cfg["company"].get("tagline", "")}


def save_shared_footer(values, path=ROOT / "config.yaml"):
    """Rewrite only the company/qr values in config.yaml, keeping comments and everything else."""
    text = path.read_text(encoding="utf-8")
    for f, (sec, key, _) in FIELDS.items():
        v = values.get(f, "")
        if f == "websites":
            v = "[" + ", ".join(split_websites(v)) + "]"
        elif v and re.search(r"[:#\[\]{},&*!|>'\"%@`]", v) and not v.startswith("http"):
            v = '"' + v.replace('"', '\\"') + '"'
        # find the section block, then the key line within it
        m = re.search(rf"(?m)^{sec}:\s*\n((?:[ \t]+.*\n?|\s*\n)*)", text)
        if not m:
            text = text.rstrip("\n") + f"\n\n{sec}:\n  {key}: {v}\n"
            continue
        block = m.group(1)
        line = re.compile(rf"(?m)^([ \t]+){re.escape(key)}:.*$")
        if line.search(block):
            block2 = line.sub(lambda mm: f"{mm.group(1)}{key}: {v}", block, count=1)
        else:
            block2 = f"  {key}: {v}\n" + block
        text = text[:m.start(1)] + block2 + text[m.end(1):]
    path.write_text(text, encoding="utf-8")
