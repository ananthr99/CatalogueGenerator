# Catalogue Builder

Builds a uniform one-page A4 catalogue (PDF + editable Word) for each product. It merges the **website product-selector JSON** with the **Master Catalogue PDF** and the **product image**. You review and approve every page before it is built.

## Setup (once)

```bash
pip install -r requirements.txt     # includes segno (QR codes)
playwright install chromium        # headless browser used to print the PDF
```

Put your sources here, or point `config.yaml → paths` at their existing locations:

```
data/Master Catalogue.pdf
data/json/<product>.json           # searched recursively
data/images/<product image files>  # searched recursively; matched by the JSON "images" filename, else by id prefix
```

## Run

```bash
streamlit run app.py
```

**Sidebar: pick a product**
- The filter shows counts: **All · To do · Reopened · Completed**. ✅ completed (locked) · ✏️ reopened for changes · 📝 draft · ⬜ not started.
- Use **◀ Prev / Next ▶** to move through the list. The product you have open always stays selected, even when its status or the filter changes.

**Completed products are locked (never redone by accident)**
- The completed register is `work/completed.json`. A product is added to it when:
  - you approve & build it in the app;
  - the app finds its catalogue PDF already in the output folder (`output/<NAME>/<NAME>_Catalogue.pdf`, or any `<NAME>_Catalogue.pdf` below `output/`). This happens on every start, so catalogues built with the earlier version are picked up automatically with their file date. Nothing is rebuilt.
  - you mark it by hand: sidebar → **Completed products** → "Mark as completed" (for catalogues made elsewhere).
- Opening a completed product shows the **delivered PDF** with download buttons, not the editor.
- **✏️ Reopen for editing** unlocks it. Approve again to replace the files, or click **↩ Keep delivered version** to lock it again unchanged.
- For products built with the earlier version, their edits weren't saved, so reopening starts again from the sources. If `work/approved/<id>.json` from the earlier version is present, it is used instead.
- **Batch rebuild** (and `python build.py --all`) only re-renders products approved in this app. Tick "Also rebuild products built with the earlier version" (or add `--include-earlier`) to include those that still have a saved record.

**Left: edit, one section at a time**
- Pick a section from the pills: Sources · Header · Tiles & highlights · Hardware · Software · Ordering · Physical & use cases · Compliance · Footer · Page layout. The pills wrap onto more lines; nothing scrolls sideways.
- Tables grow to fit all their rows, so they never scroll on their own.
- Edits are kept when you switch section or product, and are auto-saved as a draft (`work/drafts/`) whenever the page content changes.

**Right: preview, approve, build**
- The preview stays in view while you scroll the editor, and is scaled so the whole A4 page is always visible.
- Above it: tick the review box, then **✅ Approve & build**. This writes `output/<NAME>/<NAME>_Catalogue.pdf`, `.docx` and `.html`, stores the record in `work/approved/`, and marks the product approved.
- The product then becomes ✅ completed and locked, with download buttons and **Next to do ▶**.

## How the data is merged

| Page element | Source |
|---|---|
| Name, description, use cases, ordering table, spec tiles | Website JSON. The ordering table falls back to the PDF brief page. |
| Hardware table | Master PDF detailed page (HW grid), with any brief-page detail that the detailed rows don't already contain (Ethernet, Cellular, Wi-Fi, RAM, Storage, Interface). Falls back to the JSON fields. |
| Software & Networking table | Master PDF detailed page: every row is shown. |
| Physical specs | Dimensions, IP rating, enclosure, mounting, design, input power, weight. These rows are not repeated in the Hardware table. Input power uses the brief page when it has more detail. |
| Product → PDF page | Matched by name, then by part numbers, then by CPU model. Brief pages link to their detailed page. A manual override is saved in `work/page_mappings.json`. |

**How brief-page details are merged.** Each brief value is split into items on commas and "and". An item is skipped when at least 70% of its words and numbers already appear in the hardware rows. Any other item is appended to the matching row, or added as a new row (for example, Interface). The brief CPU, OS and Power lines are not merged this way, because the CPU/RAM/Flash rows, the Software table and Physical specs already cover them.

Values compared between the two sources: CPU, RAM, storage, cellular, dimensions, weight, power, operating temperature, IP rating, OS, and the set of ordering part numbers. Two values count as different when the numbers in them differ. Wording-only differences are not flagged.

## Fitting on one page

Hardware is always top-left and Software top-right. **Physical specifications, Ordering information, Use cases and Compliance** move between the two columns automatically:

1. The default arrangement is used if it fits.
2. If not, the arrangement with the fewest moved sections that fits is used.
3. Only when no arrangement fits is the table text made smaller, down to `min_density` (86% by default). Tighter row spacing is also tried, and whichever option leaves the larger text is kept.

The Word file uses the same arrangement and text size as the PDF.

In the **Page layout** section you can pin any of those four sections to Left or Right (📌 shows how many are pinned), and set the largest table text size. If the page still overflows, the preview shows a red banner; untick rows, shorten text or un-pin sections.

**Ordering image:** in the **Ordering** section, upload an image (PNG, JPG or WebP) to show under "Ordering Information". If the product also has a table, choose which one appears on the page. The image is stored as `data/images/uploads/<id>_ordering.<ext>`.

By default every row from both Master pages is shown. To start specific rows unticked, list them under `hardware_default_hidden` / `software_default_hidden` in `config.yaml`.

## Rebuild after changing the template

```bash
python build.py --all                    # completed products approved in this app
python build.py --all --include-earlier  # also those built with the earlier version (if their record exists)
python build.py rtsxx iabxx              # specific products
```

You can also use **Batch rebuild** in the sidebar. Both re-render completed products from their saved records, so the edits you approved are kept. Catalogues built with the earlier version are left untouched unless you include them.

## Customising

- **Layout and look:** `templates/catalogue.html.j2`, which is plain HTML/CSS. Brand colours are set in `config.yaml → brand`.
- **Highlights (top-right badges):** go to the **Tiles & badges** tab → Badges. Each badge has an icon and a label. Pick a built-in icon or an uploaded logo (`logo:<name>`). Up to 4 badges fit; leave the label empty to show a logo on its own.
- **Compliance marks:** go to the **Compliance** section. This is a table with show, symbol and label columns.
  - Untick **show** to hide a mark on that product; add or delete rows freely.
  - **Add a ready-made mark** offers CE, FCC, RoHS, WEEE, ISO 9001 (SGS), Made in India, IP rating, BIS, TEC and UL Listed.
  - CE, FCC, RoHS, WEEE and ISO 9001 use the official logos in `assets/logos/`, shown on their own (empty label). They are the default set for new products (`config.yaml → default_compliance`).
  - **Reset to the default marks** applies that set to an existing product.
  - BIS, TEC and UL use a generic certificate symbol until you upload their logo.
  - To replace a logo with a higher-resolution file, upload it in the Logo library under the same name (for example `rohs`), or overwrite the PNG in `assets/logos/`.
- **Logo library:** the "🖼 Logo library" expander appears in both of those tabs. Upload PNG (transparent background works best), SVG, JPG or WebP. Logos are saved in `assets/logos/` and are available to every product. The Word file uses the same icons and logos as the PDF.
- **Footer (company details + QR code):**
  - *Shared footer:* sidebar → **Footer (shared by all products)**. This covers company name, address, email, phone, websites, the QR link, and the QR title and caption. Saving writes them to `config.yaml` (comments are kept) and offers to rebuild all approved catalogues.
  - *Per-product footer:* the **Footer** tab of a product. It starts pre-filled with the shared values; any field you change applies to that product only. Custom fields are marked ✎, and the tab shows how many there are. "Use shared footer for this product" clears them. Typical uses are a product-specific QR link, a regional sales contact, or adding www.silbonetworks.com for SILBO products.
  - Leave the QR link empty to remove the QR code. Empty contact fields are skipped on the page.
- **Logo:** drop `assets/logo.svg` or `assets/logo.png` in place to replace the text logo.
- **Category labels and which 5 tiles each category shows:** `config.yaml → categories`. Tile types are `cellular, ports, ram, wifi, power, cpu, storage, io`.
- **Use-case descriptions:** they are remembered in `work/use_case_library.json` when you approve a page, and pre-filled on other products that use the same use-case title.
- **Word output:** same content and section order as the PDF, built with editable tables. Icons are replaced by text markers. The font is set by `docx_font` in `config.yaml`.

Fonts: Roboto and Roboto Condensed are bundled under the SIL Open Font License (`assets/fonts/OFL.txt`).
