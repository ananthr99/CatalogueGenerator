"""Run as a subprocess: python -m core.pdf_worker in.html out.pdf start_density min_density
Kept out of the Streamlit process because Playwright's sync API conflicts with Streamlit's event loop on Windows."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright



def main(html, pdf, start, minimum):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 794, "height": 1123})
        page.goto(Path(html).resolve().as_uri())
        page.evaluate("document.fonts.ready")
        fit = page.evaluate(f"fitPage({float(start)}, {float(minimum)})")
        page.emulate_media(media="print")
        page.pdf(path=pdf, format="A4", print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                 prefer_css_page_size=True)
        browser.close()
    print(json.dumps(fit))


if __name__ == "__main__":
    main(*sys.argv[1:5])
