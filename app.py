"""Catalogue Builder — review, edit, approve and build one-page product catalogues.
Run:  streamlit run app.py
"""
import copy
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import base64
from functools import lru_cache

import pymupdf

from core import compliance, completed
from core.docx_export import build_docx
from core.footer import FIELDS, effective_footer, save_shared_footer, shared_footer
from core.icons import BADGE_ICONS, COMPLIANCE_ICONS, TILE_ICONS, delete_logo, icon_png, logo_files, logo_options, save_logo
from core.master_pdf import MasterCatalogue
from core.merge import apply_conflict, build_record
from core.assemble import build_master_catalogue, build_cover_pdf, build_index_pdf, scan_output_dir, _prepare_logos
from core import mc_state
from core.render import DEFAULT_SIDE, MOVABLE, build_pdf, render_html, safe_name
from core.sources import Store, content_key, find_images, load_config, load_products

st.set_page_config(page_title="Catalogue Builder", page_icon="📘", layout="wide")
st.markdown("""<style>
.block-container{padding-top:3.6rem;padding-bottom:.5rem;max-width:100%}
.pill{display:inline-block;padding:1px 10px;border-radius:10px;font-size:.8rem;font-weight:600;vertical-align:middle}
.pill.approved{background:#e3f4e8;color:#1b6b34}.pill.edited{background:#e6eefc;color:#1f4fd1}
.pill.draft{background:#fff4d6;color:#8a5a00}.pill.new{background:#eceff5;color:#4a5165}
/* preview pane: stays in view while the editor column scrolls; the page is scaled to fit it */
div[data-testid="stHorizontalBlock"]:has(.st-key-preview_pane){align-items:stretch}
div[data-testid="stColumn"]:has(.st-key-preview_pane) > div{height:100%}
div[data-testid="stLayoutWrapper"]:has(> .st-key-preview_pane),
div[data-testid="stColumn"] > div > div:has(> .st-key-preview_pane){position:sticky;top:3.6rem;z-index:2}
.st-key-preview_pane [data-testid="stElementContainer"]:has(iframe){height:auto !important;min-height:0 !important;max-height:none !important;flex:0 0 auto !important}
.st-key-preview_pane iframe{height:calc(100vh - 12.4rem) !important;min-height:420px;width:100% !important}
.st-key-preview_pane [data-testid="stCheckbox"] label p{font-size:.85rem}
.st-key-locked_pane iframe{height:calc(100vh - 6.2rem) !important;min-height:420px;width:100% !important}
.st-key-locked_pane [data-testid="stElementContainer"]:has(iframe){height:auto !important;min-height:0 !important;flex:0 0 auto !important}
h3{margin-bottom:0 !important}
/* pills / segmented controls wrap onto more lines instead of scrolling sideways */
[data-testid="stButtonGroup"] > div{flex-wrap:wrap !important;overflow-x:visible !important;row-gap:.4rem}
</style>""", unsafe_allow_html=True)

cfg = load_config()
P = cfg["paths"]
store = Store(P["work_dir"])
S = st.session_state
TABLES = ["hardware", "software", "physical", "tiles", "badges", "use_cases", "compliance"]
ICON = {"approved": "✅", "edited": "✏️", "draft": "📝", "new": "⬜"}
STATUS_TEXT = {"approved": "completed 🔒", "edited": "reopened · edited", "draft": "draft", "new": "not started"}
SOURCE_TEXT = {"app": "Approved and built in this app",
               "detected": "Found in the output folder (built earlier, e.g. with the previous version of the tool)",
               "manual": "Marked as completed by hand"}
ICON_HELP = "Built-in symbols, or logo:<name> for a logo you uploaded in the Logo library."
SIDE_LABEL = {"auto": "Auto", "left": "Left", "right": "Right"}
SEC_NAMES = {"physical": "Physical specifications", "ordering": "Ordering information",
             "usecases": "Use cases", "compliance": "Compliance"}


@st.cache_resource(show_spinner="Indexing Master Catalogue…")
def master(path, mtime):
    return MasterCatalogue(path)


def get_master():
    p = Path(P["master_pdf"])
    return master(str(p), p.stat().st_mtime) if p.exists() else None


def fresh_record(j, brief=None, detailed=None):
    mc = get_master()
    how = "manual"
    if mc and brief is None:
        m = store.mappings().get(j["id"])
        if m:
            brief, detailed = m["brief"], m["detailed"]
        else:
            parts = [r[-1] for r in (j.get("variants") or {}).get("rows", [])]
            brief, how = mc.match_brief(j.get("name", ""), parts, j.get("cpu", ""))
            detailed = mc.detailed_for(brief) if brief else None
    imgs = find_images(j, P["images_dir"])
    rec = build_record(j, mc, cfg, imgs[0] if imgs else "", brief, detailed, store.use_cases())
    rec["sources"]["match"] = how
    return rec


def load(pid, rec):
    S.pid, S.rec_for, S.rec = pid, pid, compliance.normalize(rec, cfg)
    reset_editors()
    S.last_content = content_key(S.rec)
    S.build = None


def reset_editors():
    """Snapshot the record into the editors' starting values and give every widget a fresh key."""
    S.base = {k: copy.deepcopy(S.rec.get(k) or []) for k in TABLES}
    S.base["ordering"] = copy.deepcopy(S.rec.get("ordering") or {"headers": [], "rows": []})
    S.nonce = S.get("nonce", 0) + 1


def editor(section, columns, config=None):
    df = pd.DataFrame(S.base[section], columns=list(columns))
    for c, kind in columns.items():
        df[c] = df[c].fillna(True).astype(bool) if kind == "bool" else df[c].fillna("").astype(str)
    height = 35 * (len(df) + 2) + 4          # tall enough for every row + the add-row line: no inner scrolling
    out = st.data_editor(df, key=f"{section}_{S.nonce}", num_rows="dynamic", use_container_width=True,
                         hide_index=True, column_config=config or {}, height=height)
    rows = []
    for r in out.to_dict("records"):
        r = {k: (bool(v) if columns[k] == "bool" else ("" if pd.isna(v) else str(v)).strip()) for k, v in r.items()}
        if any(v for k, v in r.items() if columns[k] != "bool"):
            rows.append(r)
    S.rec[section] = rows


def rebuild_completed(include_earlier=False):
    """Re-render completed products from their saved records. Products built with the earlier version are
    skipped unless include_earlier is set; products without a saved record can't be rebuilt."""
    reg = completed.load(P["work_dir"])
    todo = [p for p in products if p in reg and store.approved(p)
            and (reg[p].get("source") == "app" or include_earlier)]
    bar = st.progress(0.0)
    for i, p in enumerate(todo):
        r = store.approved(p)
        od = Path(P["output_dir"]) / safe_name(r)
        info = build_pdf(r, cfg, od)
        info["docx"] = build_docx(r, cfg, od, info)
        completed.mark(P["work_dir"], p, r["name"], info["pdf"], info["docx"], reg[p].get("source", "app"))
        bar.progress((i + 1) / len(todo), text=r["name"])
    return len(todo)


@lru_cache(maxsize=32)
def pdf_page_png(path, mtime):
    doc = pymupdf.open(path)
    return doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")


def show_fitted(html_body):
    """Show content in the sticky preview frame, scaled to fit the window."""
    components.html("<html><head><style>html,body{margin:0;background:#e9ecf2;overflow:hidden;height:100%}"
                    "body{display:flex;align-items:flex-start;justify-content:center}"
                    "img{max-width:calc(100% - 8px);max-height:calc(100vh - 8px);box-shadow:0 1px 6px rgba(0,0,0,.18);"
                    "background:#fff;margin-top:4px}</style></head><body>" + html_body + "</body></html>",
                    height=900, scrolling=False)


def logo_library(where):
    """Upload / list / delete logos shared by all products (assets/logos)."""
    with st.expander(f"🖼 Logo library ({len(logo_files())}) — upload official certification or feature logos"):
        st.caption("Uploaded logos appear in the symbol lists as **logo:<name>** for every product. "
                   "PNG with a transparent background or SVG works best. Leave the label empty to show the logo alone.")
        a, b = st.columns([1, 2])
        name = a.text_input("Logo name", placeholder="e.g. bis", key=f"lname_{where}")
        up = b.file_uploader("Logo file", type=["png", "svg", "jpg", "jpeg", "webp"], key=f"lfile_{where}_{S.nonce}")
        if up and st.button("Add to library", key=f"ladd_{where}", type="primary"):
            st.toast(f"Added {save_logo(name.strip(), up.getvalue(), up.name)}")
            reset_editors()
            st.rerun()
        files = logo_files()
        if files:
            cols = st.columns(6)
            for i, (n, f) in enumerate(files.items()):
                with cols[i % 6]:
                    st.image(icon_png(f"logo:{n}"), width=60)
                    st.caption(f"logo:{n}")
                    if st.button("Delete", key=f"ldel_{where}_{n}"):
                        delete_logo(n)
                        reset_editors()
                        st.rerun()


def go_to(pid):
    S.pid_pick = pid                 # callbacks run before the widgets are drawn, so this is allowed


def next_to_review(after):
    order = list(products)
    rest = order[order.index(after) + 1:] + order[:order.index(after)]
    return next((p for p in rest if store.status(p) != "approved"), None)


# ------------------------------------------------------------------ sidebar: product selection
products = load_products(P["json_dir"])
with st.sidebar:
    st.header("📘 Catalogue Builder")
    if not products:
        st.error(f"No product JSONs found in `{P['json_dir']}`. Set paths in config.yaml.")
        st.stop()
    if not S.get("detected"):                 # once per session: register catalogues already in the output folder
        found = completed.detect(products, store, P["output_dir"], P["work_dir"])
        S.detected = True
        if found:
            st.toast(f"Found {len(found)} catalogue(s) already generated — marked as completed and locked.")
    statuses = {pid: store.status(pid) for pid in products}
    n = {k: sum(v == k for v in statuses.values()) for k in ICON}
    st.progress(n["approved"] / len(products), text=f"{n['approved']} / {len(products)} completed")
    FILTERS = {"All": lambda s: True, "To do": lambda s: s in ("new", "draft"),
               "Reopened": lambda s: s == "edited", "Completed": lambda s: s == "approved"}
    counts = {"All": len(products), "To do": n["new"] + n["draft"], "Reopened": n["edited"], "Completed": n["approved"]}
    # Product, filter and section controls are keyed: Streamlit then identifies them by key alone, so their
    # live labels (status icons, counts) can change without the selection being reset.
    flt = st.segmented_control("Show", list(FILTERS), default="All", key="flt_pick",
                               format_func=lambda f: f"{f} {counts[f]}", label_visibility="collapsed") or "All"
    if S.get("pid_pick") not in products:
        S.pid_pick = S.get("pid") if S.get("pid") in products else next(iter(products))
    cur = S.pid_pick
    ids = [p for p in products if FILTERS[flt](statuses[p]) or p == cur]   # the open product always stays listed
    if len(ids) == 1 and not FILTERS[flt](statuses[cur]):
        st.caption("Nothing else in this filter.")
    pid = st.selectbox("Product", ids, key="pid_pick",
                       format_func=lambda p: f"{ICON[statuses[p]]}  {products[p].get('name', p)}  ·  {products[p].get('cat', '')}")
    i = ids.index(pid)
    a, b = st.columns(2)
    a.button("◀ Prev", use_container_width=True, disabled=i == 0, on_click=go_to, args=(ids[max(i - 1, 0)],))
    b.button("Next ▶", use_container_width=True, disabled=i == len(ids) - 1,
             on_click=go_to, args=(ids[min(i + 1, len(ids) - 1)],))
    st.caption("✅ completed (locked) · ✏️ reopened · 📝 draft · ⬜ not started")
    st.divider()
    with st.expander("Sources & folders"):
        st.caption(f"{'✅' if Path(P['master_pdf']).exists() else '❌'} Master PDF: `{Path(P['master_pdf']).name}`  \n"
                   f"JSON: `{P['json_dir']}`  \nImages: `{P['images_dir']}`  \nOutput: `{P['output_dir']}`")
    reg = completed.load(P["work_dir"])
    with st.expander(f"Completed products ({len(reg)})"):
        st.caption("Products whose catalogue is done. They are locked and never rebuilt unless you reopen them. "
                   "Catalogues already in the output folder are added automatically.")
        for p_, e in sorted(reg.items(), key=lambda kv: kv[1].get("generated_at", ""), reverse=True):
            tag = {"app": "built here", "detected": "earlier version", "manual": "marked by hand"}.get(e.get("source"), "")
            st.caption(f"{'🔒' if e.get('locked', True) else '✏️'} **{e.get('name', p_)}** · "
                       f"{(e.get('generated_at') or '')[:10]} · {tag}")
        if st.button("🔍 Scan output folder again", use_container_width=True):
            found = completed.detect(products, store, P["output_dir"], P["work_dir"])
            st.toast(f"{len(found)} more found." if found else "Nothing new found.")
            st.rerun()
        others = [p_ for p_ in products if p_ not in reg]
        pick = st.multiselect("Mark as completed (catalogue made elsewhere)", others,
                              format_func=lambda p_: products[p_].get("name", p_), key="mark_done")
        if pick and st.button("Mark selected as completed", use_container_width=True):
            for p_ in pick:
                completed.mark(P["work_dir"], p_, products[p_].get("name", p_), source="manual")
            S.pop("mark_done", None)
            st.rerun()
    with st.expander("Batch rebuild"):
        st.caption("Re-render completed products from their saved content (e.g. after a template change).")
        earlier = st.checkbox("Also rebuild products built with the earlier version", value=False,
                              help="Off by default, so catalogues you already delivered are left untouched.")
        if st.button("Rebuild completed products", use_container_width=True):
            st.success(f"Rebuilt {rebuild_completed(earlier)} catalogues.")
    with st.expander("Footer (shared by all products)"):
        st.caption("Used on every catalogue unless a product overrides a field in its own Footer section. "
                   "Saved to config.yaml.")
        shared = shared_footer(cfg)
        with st.form("shared_footer"):
            vals = {f: st.text_input(label, shared[f]) for f, (_, _, label) in FIELDS.items()}
            if st.form_submit_button("Save shared footer", use_container_width=True):
                save_shared_footer(vals)
                S.footer_saved = True
                st.rerun()
        if S.get("footer_saved"):
            st.success("Saved. The preview uses it now; completed PDFs/Word files update only when rebuilt.")
            if st.button("Rebuild completed products now", use_container_width=True,
                         help="Products built with the earlier version are not touched."):
                st.success(f"Rebuilt {rebuild_completed()} catalogues.")
                S.footer_saved = False

    # ---- Master Catalogue assembly ----------------------------------------
    st.divider()
    st.subheader("📚 Master Catalogue")
    st.caption("Design cover & index, preview, then merge all datasheets into one PDF.")

    if st.button("✏️ Edit Cover & Index", use_container_width=True, key="btn_mc_edit"):
        S.mc_page = "edit"
        st.rerun()

# ------------------------------------------------------------------ Master Catalogue edit/preview page
if S.get("mc_page") == "edit":
    _mcs = mc_state.load(P.get("work_dir", "work"))

    st.markdown("## 📚 Master Catalogue — Cover & Index")
    st.caption("Edit the fields below, preview the pages, then approve and merge.")

    if st.button("← Back to products", key="mc_back"):
        S.mc_page = None
        st.rerun()

    st.divider()

    # ── Logos & output ───────────────────────────────────────────────────────
    _logo_dir = Path("data/images")
    _default_inv = str(next(_logo_dir.glob("invendis*"), "")) if _logo_dir.exists() else ""
    _default_sil = str(next(_logo_dir.glob("silbo*"), "")) if _logo_dir.exists() else ""

    mc_col1, mc_col2, mc_col3 = st.columns(3)
    inv_logo_val = mc_col1.text_input("Invendis logo path", value=_default_inv, key="mc_inv_logo")
    sil_logo_val = mc_col2.text_input("SILBO logo path",    value=_default_sil, key="mc_sil_logo")
    out_dir_val  = mc_col3.text_input("Output folder", value=P.get("output_dir", "output"), key="mc_out_dir")

    st.divider()

    # ── Cover page fields ────────────────────────────────────────────────────
    st.markdown("### Cover Page")
    cc1, cc2 = st.columns(2)
    with cc1:
        _mcs["eyebrow"]    = st.text_input("Eyebrow label (red text above title)",
                                           value=_mcs["eyebrow"],    key="mc_eyebrow")
        _mcs["title_top"]  = st.text_input("Title line 1 (large, light)",
                                           value=_mcs["title_top"],  key="mc_title_top")
        _mcs["title_main"] = st.text_input("Title line 2 (large, bold)",
                                           value=_mcs["title_main"], key="mc_title_main")
        _mcs["year"]       = st.text_input("Year",
                                           value=_mcs["year"],       key="mc_year")
        _mcs["tagline"]    = st.text_input("Tagline (below year)",
                                           value=_mcs["tagline"],    key="mc_tagline")
    with cc2:
        _mcs["company"]    = st.text_input("Company name",
                                           value=_mcs["company"],    key="mc_company")
        _mcs["address"]    = st.text_area("Address",
                                          value=_mcs["address"],     key="mc_address", height=68)
        _mcs["email"]      = st.text_input("Email",
                                           value=_mcs["email"],      key="mc_email")
        _mcs["phone"]      = st.text_input("Phone",
                                           value=_mcs["phone"],      key="mc_phone")
        _mcs["website"]    = st.text_input("Website",
                                           value=_mcs["website"],    key="mc_website")
        _mcs["footer_tag"] = st.text_input("Bottom bar tagline",
                                           value=_mcs["footer_tag"], key="mc_footer_tag")

    st.divider()

    # ── Index page fields ────────────────────────────────────────────────────
    st.markdown("### Index Page")
    ix1, ix2 = st.columns([1, 2])
    with ix1:
        _mcs["index_title"] = st.text_input("Index page heading",
                                            value=_mcs["index_title"], key="mc_index_title")
        _mcs["cat_order"]   = st.text_input(
            "Category display order (comma-separated)",
            value=_mcs["cat_order"], key="mc_cat_order",
            help="Sections appear in this order. Products not matching any listed category go at the end.")

    # ── Scan output folder for products ──────────────────────────────────────
    try:
        _scanned = scan_output_dir(out_dir_val) if Path(out_dir_val).exists() else []
    except Exception:
        _scanned = []

    # ── Per-product mapping table ─────────────────────────────────────────────
    st.markdown("#### Product → Category & Name mapping")
    st.caption("Edit Display Name or Category for any row. Changes apply only to the index page, not the datasheet PDFs.")

    if _scanned:
        import pandas as _pd

        _overrides     = dict(_mcs.get("name_overrides") or {})
        _cat_overrides = dict(_mcs.get("cat_overrides")  or {})

        # Build the editable dataframe
        _rows = []
        for _pg_no, _pdf_path, _prod_name, _cat_from_pdf in sorted(_scanned, key=lambda x: x[0]):
            _rows.append({
                "Page": _pg_no,
                "Product (from PDF)": _prod_name,
                "Display Name": _overrides.get(_prod_name, _prod_name),
                "Category": _cat_overrides.get(_prod_name, _cat_from_pdf),
            })

        # Collect unique categories (from PDFs + any already in cat_order) for the selectbox
        _known_cats = []
        for _c in _mcs["cat_order"].split(","):
            _c = _c.strip()
            if _c and _c not in _known_cats:
                _known_cats.append(_c)
        for _r in _rows:
            if _r["Category"] not in _known_cats:
                _known_cats.append(_r["Category"])

        _df_in = _pd.DataFrame(_rows)
        _df_out = st.data_editor(
            _df_in,
            column_config={
                "Page":               st.column_config.NumberColumn("Page", disabled=True, width="small"),
                "Product (from PDF)": st.column_config.TextColumn("Product (from PDF)", disabled=True),
                "Display Name":       st.column_config.TextColumn("Display Name"),
                "Category":           st.column_config.SelectboxColumn(
                                          "Category",
                                          options=_known_cats,
                                          required=True,
                                      ),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key="mc_prod_table",
        )

        # Write back overrides from edited dataframe
        _new_name_ov = {}
        _new_cat_ov  = {}
        for _, _row in _df_out.iterrows():
            _orig = _row["Product (from PDF)"]
            if _row["Display Name"] != _orig:
                _new_name_ov[_orig] = _row["Display Name"]
            # find original category from PDF to compare
            _orig_cat = next((r[3] for r in _scanned if r[2] == _orig), None)
            if _orig_cat and _row["Category"] != _orig_cat:
                _new_cat_ov[_orig] = _row["Category"]
        _mcs["name_overrides"] = _new_name_ov
        _mcs["cat_overrides"]  = _new_cat_ov
    else:
        st.caption("(Output folder not found or empty — set it above to load products.)")

    st.divider()

    # ── Preview & Approve ────────────────────────────────────────────────────
    _btn_col1, _btn_col2 = st.columns(2)
    _do_preview  = _btn_col1.button("👁 Preview Cover & Index", use_container_width=True, type="primary", key="mc_preview_btn")
    _do_save     = _btn_col2.button("💾 Save settings",         use_container_width=True, key="mc_save_btn")

    if _do_save or _do_preview:
        mc_state.save(P.get("work_dir", "work"), _mcs)
        if _do_save:
            st.toast("Settings saved.")

    if _do_preview or S.get("mc_previewed"):
        import tempfile as _tf, base64 as _b64

        # Build preview PDFs → rasterise to PNG
        _prev_tmp = _tf.mkdtemp()
        _cover_pdf = str(Path(_prev_tmp) / "prev_cover.pdf")
        _index_pdf = str(Path(_prev_tmp) / "prev_index.pdf")

        with st.spinner("Rendering preview…"):
            try:
                _prepare_logos(inv_logo_val or None, sil_logo_val or None)
                build_cover_pdf(_cover_pdf, inv_logo_val or None, sil_logo_val or None, settings=_mcs)

                # For index preview use scanned products if available, else fake 5 items
                _prev_prods = _scanned if _scanned else [
                    (i+1, "", f"Product {i+1}", cat)
                    for i, cat in enumerate(["Edge Computers"]*3 + ["Routers"]*2)
                ]
                build_index_pdf(_index_pdf, _prev_prods, page_offset=2,
                                invendis_logo=inv_logo_val or None, settings=_mcs)

                import pymupdf as _fitz
                def _pdf_to_b64(path):
                    doc = _fitz.open(path)
                    pix = doc[0].get_pixmap(matrix=_fitz.Matrix(1.8, 1.8))
                    return _b64.b64encode(pix.tobytes("png")).decode()

                _cover_b64 = _pdf_to_b64(_cover_pdf)
                _index_b64 = _pdf_to_b64(_index_pdf)
                S.mc_previewed = True
                S.mc_cover_b64 = _cover_b64
                S.mc_index_b64 = _index_b64
            except Exception as _e:
                st.error(f"Preview failed: {_e}")
                S.mc_previewed = False

    if S.get("mc_previewed") and S.get("mc_cover_b64"):
        st.markdown("#### Preview")
        _pv1, _pv2 = st.columns(2)
        _pv1.markdown("**Cover page**")
        _pv1.image(f"data:image/png;base64,{S.mc_cover_b64}", use_container_width=True)
        _pv2.markdown("**Index page**")
        _pv2.image(f"data:image/png;base64,{S.mc_index_b64}", use_container_width=True)

        st.divider()
        st.markdown("#### Approve & Merge")
        _ok = st.checkbox("I have reviewed the cover and index pages and approve them", key="mc_approve_chk")
        mc_out_name = st.text_input("Output filename", value="master_catalogue.pdf", key="mc_out_name")

        if st.button("🔨 Build Master Catalogue", use_container_width=True,
                     type="primary", disabled=not _ok, key="btn_build_mc"):
            _out_pdf_path = str(Path(out_dir_val).parent / mc_out_name)
            _prog = st.progress(0.0)
            _stxt = st.empty()

            def _prog_cb(pct, msg):
                _prog.progress(pct, text=msg)
                _stxt.caption(msg)

            try:
                _result = build_master_catalogue(
                    output_dir=out_dir_val,
                    out_pdf=_out_pdf_path,
                    invendis_logo=inv_logo_val or None,
                    silbo_logo=sil_logo_val or None,
                    work_dir=P.get("work_dir", "work"),
                    progress_callback=_prog_cb,
                    settings=_mcs,
                )
                _prog.progress(1.0, text="Done!")
                st.success(f"✅ {_result['page_count']} pages · {_result['product_count']} products")
                st.caption("Categories: " + ", ".join(_result["categories"]))
                st.download_button(
                    "⬇ Download Master Catalogue PDF",
                    Path(_result["pdf"]).read_bytes(),
                    Path(_result["pdf"]).name,
                    "application/pdf",
                    use_container_width=True,
                    key="dl_master_cat",
                )
            except Exception as _exc:
                _prog.empty()
                st.error(f"Build failed: {_exc}")

    st.stop()

# ------------------------------------------------------------------ completed (locked) product: show what was delivered
entry = completed.load(P["work_dir"]).get(pid)
if entry and entry.get("locked", True):
    S.pid, S.rec_for = pid, None                  # keep the selection; reload the record if it is reopened
    pdf, docx = completed.files(entry)
    left, right = st.columns([0.5, 0.5], gap="medium")
    with left:
        st.markdown(f"### {entry.get('name') or products[pid].get('name', pid)} &nbsp;"
                    f"<span class='pill approved'>{STATUS_TEXT['approved']}</span>", unsafe_allow_html=True)
        info = S.get("build") if S.get("build_pid") == pid else None
        if info:
            st.success("Approved & built ✓" + (f" · text {int(info['density'] * 100)}%" if info["density"] < 1 else ""))
        with st.container(border=True):
            st.markdown(f"**Completed {(entry.get('generated_at') or '')[:16].replace('T', ' at ')}**")
            st.caption(SOURCE_TEXT.get(entry.get("source"), ""))
            if pdf:
                st.caption(f"PDF: `{pdf}`" + (f"  \nWord: `{docx}`" if docx else "  \nWord: not found"))
            elif entry.get("source") != "manual":
                st.warning("The PDF is no longer at its saved location. Scan the output folder again, or reopen to rebuild.")
            a, b = st.columns(2)
            if pdf:
                a.download_button("⬇ PDF", pdf.read_bytes(), pdf.name, "application/pdf", use_container_width=True)
            if docx:
                b.download_button("⬇ Word", docx.read_bytes(), docx.name,
                                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                  use_container_width=True)
        st.caption("This product is locked, so it won't be rebuilt or changed by accident. Reopen it only if the "
                   "catalogue needs changes; you'll then approve it again to produce new files.")
        a, b = st.columns(2)
        if a.button("✏️ Reopen for editing", use_container_width=True):
            completed.set_locked(P["work_dir"], pid, False)
            S.build = None
            st.rerun()
        nxt = next_to_review(pid)
        if nxt:
            b.button(f"Next to do: {products[nxt].get('name', nxt)} ▶", on_click=go_to, args=(nxt,),
                     use_container_width=True, type="primary")
        if entry.get("source") == "detected" and not store.approved(pid):
            st.info("Built with the earlier version: its edits weren't saved, so reopening starts again from the "
                    "Master Catalogue and JSON.", icon="ℹ️")
    with right:
        with st.container(key="locked_pane"):
            if pdf:
                b64 = base64.b64encode(pdf_page_png(str(pdf), pdf.stat().st_mtime)).decode()
                show_fitted(f'<img src="data:image/png;base64,{b64}" alt="Delivered catalogue">')
            else:
                st.info("No PDF to preview.")
    st.stop()

if S.get("rec_for") != pid:
    load(pid, store.draft(pid) or store.approved(pid) or fresh_record(products[pid]))

rec, j = S.rec, products[pid]
mc = get_master()

# shared footer changed (sidebar or config.yaml): refresh the footer inputs
_shared = shared_footer(cfg)
if S.get("shared_footer_seen") != _shared:
    S.shared_footer_seen = _shared
    reset_editors()
# per-product footer: read this run's inputs first so the section label is current
if any(f"ft_{f}_{S.nonce}" in S for f in FIELDS):
    over = {}
    for f in FIELDS:
        v = S.get(f"ft_{f}_{S.nonce}", effective_footer(rec, cfg)[f]).strip()
        if v != _shared[f].strip():
            over[f] = v
    if over:
        rec["footer"] = over
    else:
        rec.pop("footer", None)

status = store.status(pid)
src = rec["sources"]
edit_col, prev_col = st.columns([0.5, 0.5], gap="medium")
unresolved = [c for c in rec["conflicts"] if not c.get("choice")]

with edit_col:
    # ---- header (inside the editor column so the preview can use the full window height)
    st.markdown(f"### {rec['name']} &nbsp;<span class='pill {status}'>{STATUS_TEXT[status]}</span>", unsafe_allow_html=True)
    st.caption(f"Website JSON `{Path(j['_file']).name}` · Master PDF brief p.{src.get('brief_page') or '—'} → detailed "
               f"p.{src.get('detailed_page') or '—'} ({src.get('match', 'saved')}) · Image "
               f"{'✅ ' + Path(rec['image']).name if rec.get('image') and Path(rec['image']).exists() else '❌ missing'}")
    n_custom = len(rec.get("footer") or {})
    pins = {k: v for k, v in (rec.get("placement") or {}).items() if v in ("left", "right")}
    SECTIONS = {
        "sources": f"Sources{' ⚠️' + str(len(unresolved)) if unresolved else ''}",
        "header": "Header", "tiles": "Tiles & highlights", "hardware": "Hardware", "software": "Software",
        "ordering": "Ordering", "physical": "Physical & use cases", "compliance": "Compliance",
        "footer": f"Footer{' ✎' + str(n_custom) if n_custom else ''}",
        "layout": f"Page layout{' 📌' + str(len(pins)) if pins else ''}",
    }
    if "section_pick" not in S:                # e.g. after viewing a locked product: come back to the last section
        S.section_pick = S.get("section", "sources")
    sec = st.pills("Section", list(SECTIONS), format_func=SECTIONS.get, default="sources", key="section_pick",
                   label_visibility="collapsed") or S.get("section", "sources")
    if sec != S.get("section"):
        S.section = sec
        reset_editors()          # tables of the section we left keep their edits (already in the record)

    kv_cfg = {"show": st.column_config.CheckboxColumn("show", width="small"),
              "key": st.column_config.TextColumn("spec", width="medium"),
              "value": st.column_config.TextColumn("value", width="large")}

    # ---- sources & conflicts
    if sec == "sources":
        if rec["conflicts"]:
            st.markdown("**Website JSON vs Master Catalogue** — pick the value to use for each difference.")
            for i, c in enumerate(rec["conflicts"]):
                with st.container(border=True):
                    a, b = st.columns([1, 2], vertical_alignment="center")
                    a.markdown(f"**{c['field']}**")
                    opts = ["json", "pdf"]
                    choice = b.radio("Use", opts, key=f"cf_{i}_{S.nonce}", horizontal=True,
                                     index=opts.index(c["choice"]) if c.get("choice") in opts else None,
                                     format_func=lambda o: "Use website JSON" if o == "json" else "Use Master PDF",
                                     label_visibility="collapsed")
                    a, b = st.columns(2)
                    a.caption("Website JSON")
                    a.markdown(c["json"])
                    b.caption("Master PDF")
                    b.markdown(c["pdf"])
                    if choice and choice != c.get("choice"):
                        c["choice"] = choice
                        apply_conflict(rec, c)
                        reset_editors()
                        st.rerun()
        else:
            st.success("No differences between the website JSON and the Master Catalogue for the compared fields.")

        st.markdown("**Source pages**")
        if mc:
            briefs = {p.number: p.title for p in mc.briefs()}
            opts = [None] + list(briefs)
            a, b, c = st.columns([2, 1, 1])
            bsel = a.selectbox("Brief page", opts, index=opts.index(src.get("brief_page")) if src.get("brief_page") in opts else 0,
                               format_func=lambda n: "— none —" if n is None else f"p.{n} · {briefs[n]}", key=f"bp_{S.nonce}")
            dsel = b.number_input("Detailed page (0 = none)", 0, len(mc.pages), int(src.get("detailed_page") or 0), key=f"dp_{S.nonce}")
            c.write("")
            if c.button("Re-import", help="Rebuilds the record from these pages; your edits are discarded.",
                        use_container_width=True):
                d = int(dsel) or None
                if bsel != src.get("brief_page") and bsel and not int(dsel):
                    d = mc.detailed_for(bsel)
                store.set_mapping(pid, bsel, d)
                load(pid, fresh_record(j, bsel, d))
                st.rerun()
            if bsel != src.get("brief_page") and bsel:
                st.caption(f"Linked detailed page for p.{bsel}: p.{mc.detailed_for(bsel) or '—'}")
        else:
            st.error("Master Catalogue PDF not found — set `paths.master_pdf` in config.yaml.")

        st.markdown("**Product image**")
        imgs = find_images(j, P["images_dir"])
        if rec.get("image") and rec["image"] not in imgs:
            imgs.insert(0, rec["image"])
        a, b = st.columns([1, 1])
        if imgs:
            rec["image"] = a.radio("Image", imgs, index=imgs.index(rec["image"]) if rec.get("image") in imgs else 0,
                                   format_func=lambda x: Path(x).name, label_visibility="collapsed", key=f"img_{S.nonce}")
        up = b.file_uploader("Upload a different image", type=["webp", "png", "jpg", "jpeg"], key=f"up_{S.nonce}")
        if up:
            dest = Path(P["images_dir"]) / "uploads" / f"{pid}{Path(up.name).suffix.lower()}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(up.getvalue())
            rec["image"] = str(dest)
            reset_editors()
            st.rerun()
        if st.button("↺ Discard my edits and rebuild from sources", type="secondary"):
            store.discard_draft(pid)
            load(pid, fresh_record(j))
            st.rerun()

    # ---- header / hero
    elif sec == "header":
        k = S.nonce
        a, b = st.columns(2)
        rec["name"] = a.text_input("Product name", rec["name"], key=f"name_{k}")
        rec["subtitle"] = b.text_input("Subtitle (under the name)", rec["subtitle"], key=f"sub_{k}")
        a, b = st.columns(2)
        rec["bar_title"] = a.text_input("Header bar title", rec["bar_title"], key=f"bar_{k}")
        rec["category_plural"] = b.text_input("Category shown in header bar", rec["category_plural"], key=f"cat_{k}")
        a, b, c = st.columns([2, 2, 1])
        e1 = a.text_input("Eyebrow (red label) 1", rec["eyebrow"][0] if rec["eyebrow"] else "", key=f"e1_{k}")
        e2 = b.text_input("Eyebrow 2", rec["eyebrow"][1] if len(rec["eyebrow"]) > 1 else "", key=f"e2_{k}")
        rec["eyebrow"] = [e for e in (e1.upper(), e2.upper()) if e]
        rec["order"] = c.number_input("Page no.", 0, 999, int(rec.get("order") or 0), key=f"ord_{k}")
        rec["description"] = st.text_area("Description", rec["description"], height=130, key=f"desc_{k}")
        st.caption(f"{len(rec['description'])} characters — about 350 fits the hero area comfortably.")

    # ---- tiles & highlights
    elif sec == "tiles":
        st.markdown("**Spec tiles** (the strip under the hero — 5 recommended)")
        editor("tiles", {"icon": "str", "value": "str", "label": "str"},
               {"icon": st.column_config.SelectboxColumn("icon", options=TILE_ICONS + logo_options(), required=True,
                                                         help=ICON_HELP)})
        if len(rec["tiles"]) > 5:
            st.warning("More than 5 tiles will be cramped.")
        st.markdown("**Highlights** (right of the hero — up to 4)")
        editor("badges", {"icon": "str", "label": "str"},
               {"icon": st.column_config.SelectboxColumn("icon", options=BADGE_ICONS + logo_options(), required=True,
                                                         help=ICON_HELP)})
        if len(rec["badges"]) > 4:
            st.warning("Only 4 highlights fit the space beside the description.")
        logo_library("badges")

    elif sec == "hardware":
        st.caption("Untick rows to leave them off the page. Add rows at the bottom; to reorder, delete and re-add.")
        editor("hardware", {"show": "bool", "key": "str", "value": "str"}, kv_cfg)
    elif sec == "software":
        st.caption("Untick rows to leave them off the page. Add rows at the bottom; to reorder, delete and re-add.")
        editor("software", {"show": "bool", "key": "str", "value": "str"}, kv_cfg)

    # ---- ordering: table and/or image
    elif sec == "ordering":
        o = S.base["ordering"]
        has_img = bool(rec.get("ordering_image")) and Path(rec["ordering_image"]).exists()
        if has_img:
            a, b = st.columns([1, 1])
            mode = a.radio("Show on the page", ["table", "image"], horizontal=True, key=f"om_{S.nonce}",
                           index=1 if (rec.get("ordering_mode") or ("table" if o.get("rows") else "image")) == "image" else 0,
                           format_func={"table": "Ordering table", "image": "Ordering image"}.get)
            rec["ordering_mode"] = mode
            b.image(rec["ordering_image"], width=220)
            if b.button("Remove image"):
                rec.pop("ordering_image", None)
                rec.pop("ordering_mode", None)
                reset_editors()
                st.rerun()
        up = st.file_uploader("Ordering image (used in place of the table, e.g. for products without one)",
                              type=["png", "jpg", "jpeg", "webp"], key=f"oimg_{S.nonce}")
        if up:
            dest = Path(P["images_dir"]) / "uploads" / f"{pid}_ordering{Path(up.name).suffix.lower()}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            for old in dest.parent.glob(f"{pid}_ordering.*"):
                old.unlink()
            dest.write_bytes(up.getvalue())
            rec["ordering_image"] = str(dest)
            rec["ordering_mode"] = "image"
            reset_editors()
            st.rerun()
        st.divider()
        a, b = st.columns([3, 1])
        hdr_txt = a.text_input("Table columns (comma-separated)", ", ".join(o.get("headers", [])), key=f"oh_{S.nonce}")
        new_hdr = [h.strip() for h in hdr_txt.split(",") if h.strip()]
        if new_hdr != o.get("headers", []):
            o["headers"] = new_hdr
            o["rows"] = [(r + [""] * len(new_hdr))[:len(new_hdr)] for r in o.get("rows", [])]
            S.rec["ordering"] = copy.deepcopy(o)
            reset_editors()
            st.rerun()
        src_pick = b.selectbox("Load table from", ["—", "Website JSON", "Master PDF"], key=f"os_{S.nonce}")
        if src_pick != "—":
            rec["ordering"] = copy.deepcopy(rec["_json_ordering"] if src_pick == "Website JSON" else rec["_pdf_ordering"])
            reset_editors()
            st.rerun()
        if new_hdr:
            df = pd.DataFrame(o.get("rows", []), columns=new_hdr).fillna("")
            out = st.data_editor(df, key=f"ordering_{S.nonce}", num_rows="dynamic", use_container_width=True,
                                 hide_index=True, height=35 * (len(df) + 2) + 4)
            rec["ordering"] = {"headers": new_hdr,
                               "rows": [[("" if pd.isna(v) else str(v)) for v in r] for r in out.values.tolist()
                                        if any(str(v).strip() for v in r if not pd.isna(v))]}
            st.caption("Use ✓ for included and — for not included. Last column is shown as the part number.")
        elif not has_img:
            st.info("No ordering table — the section is hidden. Type column names above, or upload an image.")

    # ---- physical & use cases
    elif sec == "physical":
        st.markdown("**Physical specifications**")
        editor("physical", {"show": "bool", "key": "str", "value": "str"}, kv_cfg)
        st.markdown("**Use cases**")
        editor("use_cases", {"title": "str", "desc": "str"},
               {"title": st.column_config.TextColumn("title", width="medium"),
                "desc": st.column_config.TextColumn("one-line description", width="large")})
        if any(not u.get("desc") for u in rec["use_cases"]):
            st.caption("💡 Use cases without a description show as a title only. Descriptions you approve are remembered "
                       "and pre-filled for other products with the same use case.")

    # ---- compliance
    elif sec == "compliance":
        editor("compliance", {"show": "bool", "icon": "str", "label": "str"},
               {"show": st.column_config.CheckboxColumn("show", width="small"),
                "icon": st.column_config.SelectboxColumn("symbol", options=COMPLIANCE_ICONS + logo_options(),
                                                         required=True, help=ICON_HELP),
                "label": st.column_config.TextColumn("label", width="medium")})
        pre = compliance.presets(cfg)
        a, b = st.columns([3, 1])
        pick = a.selectbox("Add a ready-made mark", ["—"] + list(pre), key=f"cp_{S.nonce}",
                           format_func=lambda k: "—" if k == "—" else pre[k]["name"])
        b.write("")
        if b.button("Add", use_container_width=True, disabled=pick == "—"):
            row = compliance.preset_row(pick, cfg, rec.get("ip_rating", "")) or \
                  {"show": True, "icon": "shield", "label": "IP rating"}
            rec["compliance"] = rec["compliance"] + [row]
            reset_editors()
            st.rerun()
        st.caption("Untick **show** to hide a mark on this product; delete or add rows freely. Marks with an empty "
                   "label show the logo alone. BIS / TEC / UL use a generic certificate symbol until you upload their "
                   "official logo below and pick it as the symbol.")
        cat_cfg = (cfg.get("categories") or {}).get(j.get("cat"), {})
        if st.button("↺ Reset to the default marks", help="Replaces this product's marks with the default set "
                     "from config.yaml (default_compliance, or the category's own list)."):
            rec["compliance"] = compliance.default_rows(cfg, cat_cfg, rec.get("ip_rating", ""))
            reset_editors()
            st.rerun()
        logo_library("compliance")

    # ---- footer (per-product overrides on top of the shared footer)
    elif sec == "footer":
        shared = shared_footer(cfg)
        eff = effective_footer(rec, cfg)
        st.caption("Pre-filled with the shared footer. Change a field to override it for this product only; "
                   "fields marked ✎ are custom. Edit the shared footer in the sidebar.")
        items = list(FIELDS.items())
        for row in range(0, len(items), 2):
            cols = st.columns(2)
            for c, (f, (_, _, label)) in zip(cols, items[row:row + 2]):
                k = f"ft_{f}_{S.nonce}"
                if k not in S:
                    S[k] = eff[f]
                custom = f in (rec.get("footer") or {})
                c.text_input(label + ("  ✎" if custom else ""), key=k,
                             help=f"Custom for this product. Shared value: {shared[f] or '(empty)'}" if custom else None)
        if rec.get("footer") and st.button("↺ Use shared footer for this product"):
            rec.pop("footer", None)
            reset_editors()
            st.rerun()

    # ---- page layout
    elif sec == "layout":
        st.markdown("**Section placement**")
        st.caption("Hardware is always top-left and Software top-right. On **Auto**, the other sections move between "
                   "the columns automatically so the page fits on one A4 sheet before any text is made smaller. "
                   "Pin a section to keep it on one side.")
        cur = rec.get("placement") or {}
        new = {}
        cols = st.columns(len(MOVABLE))
        for c, m in zip(cols, MOVABLE):
            opts = ["auto", "left", "right"]
            new[m] = c.selectbox(SEC_NAMES[m], opts, index=opts.index(cur.get(m, "auto")), key=f"pin_{m}_{S.nonce}",
                                 format_func=lambda v, m=m: "Auto" if v == "auto" else
                                 f"{SIDE_LABEL[v]}{' (default)' if DEFAULT_SIDE[m] == v else ''}")
        new = {k: v for k, v in new.items() if v != "auto"}
        if new:
            rec["placement"] = new
        else:
            rec.pop("placement", None)
        st.markdown("**Text size**")
        rec["density"] = st.select_slider(
            "Largest table text size", options=[round(0.80 + i * 0.02, 2) for i in range(11)],
            value=min(1.0, max(0.8, round(float(rec.get("density") or 1.0), 2))),
            format_func=lambda v: f"{int(v * 100)}%",
            help=f"Starting size of the specification tables. If the page still overflows after re-arranging, "
                 f"it is reduced automatically (down to {int(cfg.get('min_density', 0.86) * 100)}%).")

# ------------------------------------------------------------------ preview + approval (sticky, fits the window)
with prev_col:
    with st.container(key="preview_pane"):
        notes = []
        if unresolved:
            notes.append(f"⚠️ {len(unresolved)} source difference(s) to resolve in Sources")
        if not rec.get("image") or not Path(rec["image"]).exists():
            notes.append("⚠️ no product image")
        missing_uc = [u["title"] for u in rec["use_cases"] if not u.get("desc")]
        if missing_uc:
            notes.append(f"ℹ️ {len(missing_uc)} use case(s) without description")
        info = S.get("build") if S.get("build_pid") == pid else None
        if status == "edited" and pid in completed.load(P["work_dir"]):
            a, b = st.columns([3, 2], vertical_alignment="center")
            a.caption("✏️ Reopened — approve again to replace the delivered files, or keep them as they are.")
            if b.button("↩ Keep delivered version", use_container_width=True,
                        help="Locks the product again without building anything. Your edits stay saved as a draft."):
                completed.set_locked(P["work_dir"], pid, True)
                st.rerun()
        a, b = st.columns([3, 2], vertical_alignment="center")
        ok = a.checkbox("I have reviewed this catalogue and approve it" if status != "edited" else
                        "I have reviewed the changes and approve them", key=f"ok_{pid}_{S.nonce}")
        if b.button("✅ Approve & build", type="primary", disabled=bool(unresolved) or not ok,
                    use_container_width=True):
            out_dir = Path(P["output_dir"]) / safe_name(rec)
            with st.spinner("Rendering PDF and Word…"):
                info = build_pdf(rec, cfg, out_dir)
                info["docx"] = build_docx(rec, cfg, out_dir, info)
            rec["status"] = "approved"
            rec["approved_at"] = rec["updated"] = datetime.now().isoformat(timespec="seconds")
            store.save_approved(rec)
            completed.mark(P["work_dir"], pid, rec["name"], info["pdf"], info["docx"], "app")
            S.last_content = content_key(rec)
            S.build, S.build_pid = info, pid
            st.rerun()
        if info:
            a, b, c = st.columns([2, 1, 1])
            if info["overflow_px"] > 0:
                a.error(f"Overflows by {info['overflow_px']}px — untick rows, shorten text or un-pin sections.")
            else:
                a.success("Approved & built ✓" + (f" · text {int(info['density'] * 100)}%" if info["density"] < 1 else ""))
            b.download_button("⬇ PDF", Path(info["pdf"]).read_bytes(), Path(info["pdf"]).name, "application/pdf",
                              use_container_width=True)
            c.download_button("⬇ Word", Path(info["docx"]).read_bytes(), Path(info["docx"]).name,
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                              use_container_width=True)
            nxt = next_to_review(pid)
            if nxt:
                st.button(f"Next to review: {products[nxt].get('name', nxt)} ▶", on_click=go_to, args=(nxt,),
                          use_container_width=True)
        if notes:
            st.caption(" · ".join(notes))
        fit = ("<style>html,body{margin:0;background:#e9ecf2;overflow:hidden}"
               ".page{box-shadow:0 1px 6px rgba(0,0,0,.18)}</style>"
               "<script>(function(){function z(){var w=innerWidth-8,h=innerHeight-8;"
               "document.documentElement.style.zoom=Math.min(w/794,h/1123);}z();addEventListener('resize',z);})();</script>")
        html = render_html(rec, cfg, preview=True).replace("</head>", fit + "</head>", 1)
        components.html(html, height=900, scrolling=False)

# ------------------------------------------------------------------ autosave draft when the page content changes
if content_key(rec) != S.get("last_content"):
    rec["updated"] = datetime.now().isoformat(timespec="seconds")
    rec["status"] = "draft"
    store.save_draft(rec)
    S.last_content = content_key(rec)
    S.build = None
