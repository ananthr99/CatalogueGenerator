"""Config, product JSONs, images, persistence."""
import io
import json
from pathlib import Path

import yaml
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
IMG_EXT = {".webp", ".png", ".jpg", ".jpeg"}


def load_config(path=ROOT / "config.yaml"):
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    for k, v in cfg["paths"].items():
        p = Path(v)
        cfg["paths"][k] = p if p.is_absolute() else base / p
    for k in ("output_dir", "work_dir"):
        cfg["paths"][k].mkdir(parents=True, exist_ok=True)
    for sub in ("drafts", "approved"):
        (cfg["paths"]["work_dir"] / sub).mkdir(exist_ok=True)
    return cfg


def load_products(json_dir):
    out = {}
    for f in sorted(Path(json_dir).rglob("*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(j, dict) and j.get("id") and not j.get("hidden"):
            j["_file"] = str(f)
            out[j["id"]] = j
    return dict(sorted(out.items(), key=lambda kv: (kv[1].get("order") or 999, kv[0])))


def find_images(j, images_dir):
    files = [f for f in Path(images_dir).rglob("*") if f.suffix.lower() in IMG_EXT] if Path(images_dir).exists() else []
    wanted = [Path(p).name.lower() for p in j.get("images") or []]
    hits = [f for w in wanted for f in files if f.name.lower() == w]
    pid = j["id"].lower()
    hits += [f for f in files if f.stem.lower().startswith(pid) and f not in hits]
    return [str(f) for f in hits]


def prepared_image(path, fmt="WEBP"):
    """Trim the white border around a product photo; return bytes in the given format."""
    src = Image.open(path)
    if src.mode in ("RGBA", "LA", "P"):
        src = src.convert("RGBA")
        flat = Image.new("RGB", src.size, (255, 255, 255))
        flat.paste(src, mask=src.split()[-1])
        im = flat
    else:
        im = src.convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 18 else 0)
    box = diff.getbbox()
    if box:
        pad = 12
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
        im = im.crop(box)
    buf = io.BytesIO()
    im.save(buf, fmt, quality=90) if fmt in ("WEBP", "JPEG") else im.save(buf, fmt)
    return buf.getvalue()


VOLATILE = {"updated", "status", "approved_at", "created", "density", "conflicts", "_built"}


def content_key(rec):
    """What the page shows: the record without timestamps/status, for 'has anything changed?' checks."""
    return json.dumps({k: v for k, v in (rec or {}).items() if k not in VOLATILE}, sort_keys=True, default=str)


class Store:
    def __init__(self, work_dir):
        self.dir = Path(work_dir)
        self.map_file = self.dir / "page_mappings.json"
        self.lib_file = self.dir / "use_case_library.json"

    def _read(self, f, default):
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default

    def _write(self, f, data):
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def draft(self, pid):
        return self._read(self.dir / "drafts" / f"{pid}.json", None)

    def approved(self, pid):
        return self._read(self.dir / "approved" / f"{pid}.json", None)

    def save_draft(self, rec):
        self._write(self.dir / "drafts" / f"{rec['id']}.json", rec)

    def save_approved(self, rec):
        self._write(self.dir / "approved" / f"{rec['id']}.json", rec)
        self.discard_draft(rec["id"])
        lib = self.use_cases()
        lib.update({u["title"]: u["desc"] for u in rec["use_cases"] if u.get("title") and u.get("desc")})
        self._write(self.lib_file, lib)

    def discard_draft(self, pid):
        (self.dir / "drafts" / f"{pid}.json").unlink(missing_ok=True)

    def status(self, pid):
        """approved = completed & locked (catalogue delivered; not rebuilt) · edited = reopened for changes, or
        approved with unapproved changes · draft = work in progress, never approved · new = not started."""
        reg = self._read(self.dir / "completed.json", {})
        e = reg.get(pid)
        if e:
            return "approved" if e.get("locked", True) else "edited"
        a, d = self.approved(pid), self.draft(pid)
        if a:
            return "approved" if not d or content_key(d) == content_key(a) else "edited"
        return "draft" if d else "new"

    def mappings(self):
        return self._read(self.map_file, {})

    def set_mapping(self, pid, brief, detailed):
        m = self.mappings()
        m[pid] = {"brief": brief, "detailed": detailed}
        self._write(self.map_file, m)

    def use_cases(self):
        return self._read(self.lib_file, {
            "SD-WAN & Branch Network Connectivity":
                "Multi-WAN with VPN aggregation for enterprise branch connectivity and failover",
            "Secure VPN & Surveillance Network Deployment":
                "Rugged and secure connectivity for remote site surveillance and monitoring",
        })
