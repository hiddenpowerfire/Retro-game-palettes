"""
Retro Game Palette Extractor
=============================
Scrapes Background (Routes) and Background (Locations) map images
from The Spriters Resource and builds a color palette dictionary.

Requirements:
    pip install requests pillow beautifulsoup4

Optional (if requests gets 403):
    pip install playwright
    playwright install chromium

Usage:
    python extract_palette.py                   # default: requests
    python extract_palette.py --playwright      # use headless browser
    python extract_palette.py --debug-html      # save raw HTML and stop
"""

import re
import sys
import json
import time
import argparse
import requests
from pathlib import Path
from PIL import Image
from io import BytesIO
from bs4 import BeautifulSoup

# ── Configuration ──────────────────────────────────────────────────────────────

GAME_URL        = "https://www.spriters-resource.com/game_boy_gbc/pokemoncrystal/"
BASE_URL        = "https://www.spriters-resource.com"
TARGET_SECTIONS = ["Backgrounds (Routes)", "Backgrounds (Locations)"]
OUTPUT_DIR      = Path("images")
PALETTE_FILE    = Path("palette.json")
DELAY           = 1.5   # polite delay between requests (seconds)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer":         "https://www.spriters-resource.com/",
}

# ── Fetching ───────────────────────────────────────────────────────────────────

def fetch_with_requests(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.HTTPError as e:
        print(f"  HTTP error {e.response.status_code} for {url}")
        return None


def fetch_with_playwright(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=20000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"  Playwright error: {e}")
        return None


def fetch_page(url: str, use_playwright: bool = False) -> str | None:
    html = fetch_with_requests(url)
    if html is None and use_playwright:
        print("  Falling back to playwright...")
        html = fetch_with_playwright(url)
    return html


# ── Parsing: game page ─────────────────────────────────────────────────────────

def parse_sheet_links(html: str) -> dict[str, list[dict]]:
    """
    Parse the game page and return asset links grouped by section.

    The page structure looks like this:
        <div ...>Backgrounds (Locations)</div>
        <div class="icondisplay">
            <a href="/game_boy_gbc/pokemoncrystal/asset/27058/" class="iconlink">
                <div class="iconcontainer">
                    <div class="iconheader" title="Azalea Town">Azalea Town</div>
                    ...
                </div>
            </a>
            ...
        </div>

    Returns:
        { "Backgrounds (Routes)": [{"name": "Route 17", "url": "..."}, ...], ... }
    """
    soup = BeautifulSoup(html, "html.parser")
    sections: dict[str, list[dict]] = {}

    # Find every element whose text exactly matches one of our target section names
    for tag in soup.find_all(string=re.compile(r"Backgrounds \((Routes|Locations)\)")):
        section_name = tag.strip()
        matched = next((s for s in TARGET_SECTIONS if s == section_name), None)
        if not matched:
            continue

        # The icondisplay div is a sibling (or nearby) after this text node
        parent = tag.parent
        icondisplay = None

        # Walk up a couple levels and look for the next icondisplay sibling
        for _ in range(4):
            if parent is None:
                break
            sibling = parent.find_next_sibling("div", class_="icondisplay")
            if sibling:
                icondisplay = sibling
                break
            parent = parent.parent

        if not icondisplay:
            print(f"  WARNING: Found section '{matched}' but no icondisplay div nearby")
            continue

        sheets = []
        for a in icondisplay.find_all("a", class_="iconlink"):
            header = a.find("div", class_="iconheader")
            name = header["title"] if header and header.get("title") else header.get_text(strip=True)
            href = BASE_URL + a["href"]
            if name and href:
                sheets.append({"name": name, "url": href})

        if sheets:
            sections[matched] = sheets
            print(f"  Found section '{matched}' with {len(sheets)} sheets")

    return sections


# ── Parsing: asset page ────────────────────────────────────────────────────────

def parse_image_url_from_asset(html: str) -> str | None:
    """
    Parse an asset detail page and return the direct image URL.

    The download link looks like:
        <a href="/media/assets/25/27058.png?updated=..." id="download" ...>
    """
    soup = BeautifulSoup(html, "html.parser")
    download_a = soup.find("a", id="download")
    if download_a and download_a.get("href"):
        href = download_a["href"]
        if href.startswith("/"):
            href = BASE_URL + href
        return href
    return None


# ── Image download ─────────────────────────────────────────────────────────────

def download_image(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        img.save(dest, format="PNG")
        return True
    except Exception as e:
        print(f"    Download failed: {e}")
        return False


# ── Palette analysis ───────────────────────────────────────────────────────────

def analyze_image(image_path: Path, map_name: str, palette: dict) -> int:
    """
    Loop through every pixel in the image and update the palette dict.

    Palette entry:
        key   = "R,G,B"
        value = {
            "RGB":       [R, G, B],
            "locations": [str, ...] # map names where this color appears (each listed once)
        }

    K is intentionally not stored here — it depends on the ICC profile
    and is computed at plot time by plot_palette.py.

    Returns the number of new colors added.
    """
    img = Image.open(image_path).convert("RGBA")
    seen_keys: set[str] = set()   # colors already logged for this map
    new_colors = 0

    for r, g, b, a in img.getdata():
        if a == 0:
            continue  # skip fully transparent pixels
        key = f"{r},{g},{b}"
        if key not in palette:
            palette[key] = {
                "RGB":       [r, g, b],
                "locations": []
            }
            new_colors += 1
        if key not in seen_keys:
            palette[key]["locations"].append(map_name)
            seen_keys.add(key)

    return new_colors


# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ── Main pipeline ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract color palette from Spriters Resource maps.")
    parser.add_argument("--playwright",  action="store_true", help="Use headless browser instead of requests")
    parser.add_argument("--debug-html",  action="store_true", help="Save raw game page HTML to debug.html and exit")
    parser.add_argument("--local-only",  action="store_true", help="Skip all web requests; reprocess images already in the images/ folder")
    args = parser.parse_args()

    use_playwright = args.playwright
    OUTPUT_DIR.mkdir(exist_ok=True)
    palette: dict = {}

    # ── Local-only mode: reprocess existing images, no web requests ───────────
    if args.local_only:
        image_files = sorted(OUTPUT_DIR.glob("*.png"))
        if not image_files:
            print(f"ERROR: No PNG files found in {OUTPUT_DIR}/")
            sys.exit(1)
        print(f"Local-only mode: reprocessing {len(image_files)} images from {OUTPUT_DIR}/")
        for i, image_path in enumerate(image_files, 1):
            # Reconstruct map name from filename (reverse of slugify)
            map_name = image_path.stem.replace("_", " ").title()
            new_colors = analyze_image(image_path, map_name, palette)
            print(f"  [{i}/{len(image_files)}] {map_name}: +{new_colors} new colors  |  total: {len(palette)}")
        with open(PALETTE_FILE, "w") as f:
            json.dump(palette, f, indent=2)
        print(f"\n{'='*60}")
        print(f"Done! Processed {len(image_files)} images.")
        print(f"Total unique colors: {len(palette)}")
        print(f"Palette saved to:    {PALETTE_FILE}")
        return

    # ── Fetch game page ────────────────────────────────────────────────────────
    print(f"Fetching game page: {GAME_URL}")
    html = fetch_page(GAME_URL, use_playwright=use_playwright)
    if not html:
        print("ERROR: Could not fetch the game page.")
        print("Try running with --playwright flag.")
        sys.exit(1)

    if args.debug_html:
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved raw HTML to debug.html — exiting.")
        sys.exit(0)

    # ── Parse sections ─────────────────────────────────────────────────────────
    print("Parsing sections...")
    sections = parse_sheet_links(html)

    if not sections:
        print(
            "WARNING: No sections found.\n"
            "Try running with --debug-html to inspect the raw page,\n"
            "or try --playwright if you suspect JavaScript is needed."
        )
        sys.exit(1)

    # ── Process each sheet ─────────────────────────────────────────────────────
    all_sheets = [(section, s["name"], s["url"]) for section, sheets in sections.items() for s in sheets]
    print(f"\nTotal sheets to process: {len(all_sheets)}")

    for i, (section, name, asset_url) in enumerate(all_sheets, 1):
        print(f"\n[{i}/{len(all_sheets)}] {name}  ({section})")
        time.sleep(DELAY)

        asset_html = fetch_page(asset_url, use_playwright=use_playwright)
        if not asset_html:
            print("  Skipping (could not fetch asset page)")
            continue

        img_url = parse_image_url_from_asset(asset_html)
        if not img_url:
            print("  Skipping (could not find download link on asset page)")
            continue

        print(f"  Image: {img_url}")
        time.sleep(DELAY)

        filename = f"{slugify(name)}.png"
        dest = OUTPUT_DIR / filename

        if dest.exists():
            print(f"  Already exists, skipping download: {filename}")
        else:
            if not download_image(img_url, dest):
                continue
            print(f"  Saved: {filename}")

        new_colors = analyze_image(dest, name, palette)
        print(f"  +{new_colors} new colors  |  palette total: {len(palette)}")

    # ── Save palette ───────────────────────────────────────────────────────────
    with open(PALETTE_FILE, "w") as f:
        json.dump(palette, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Done! Processed {len(all_sheets)} images.")
    print(f"Total unique colors: {len(palette)}")
    print(f"Palette saved to:    {PALETTE_FILE}")
    print(f"Images saved to:     {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()