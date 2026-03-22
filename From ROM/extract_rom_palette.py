"""
Pokémon Crystal ROM Palette Extractor
======================================
Reads palette and tileset data directly from the pret/pokecrystal GitHub repo
and builds a palette dictionary keyed by 5-bit RGB values.

Each palette entry:
    key   = "R5,G5,B5"   (original 5-bit values, ground truth)
    value = {
        "RGB5":      [R5, G5, B5],      # 5-bit (0–31)
        "RGB8":      [R8, G8, B8],      # 8-bit scaled (0–255)
        "locations": [str, ...]         # e.g. "traditional_house - brown"
    }

Output: palette_rom.json

Requirements:
    pip install requests

Usage:
    python extract_rom_palette.py
    python extract_rom_palette.py --output my_palette.json
"""

import re
import json
import argparse
import requests

# ── GitHub raw content fetching ────────────────────────────────────────────────

BASE_RAW = "https://raw.githubusercontent.com/pret/pokecrystal/master"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/plain",
}

def fetch_text(path: str) -> str | None:
    """Fetch a raw text file from the pokecrystal repo. Returns None on failure."""
    url = f"{BASE_RAW}/{path}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None   # file simply doesn't exist
        print(f"  HTTP {e.response.status_code} fetching {path}")
        return None
    except Exception as e:
        print(f"  Error fetching {path}: {e}")
        return None

# ── 5-bit → 8-bit conversion ───────────────────────────────────────────────────

def scale_5_to_8(v: int) -> int:
    """Scale a 5-bit value (0–31) to 8-bit (0–255) accurately."""
    return round(v * 255 / 31)

# ── Palette dict helpers ───────────────────────────────────────────────────────

def add_color(palette: dict, r5: int, g5: int, b5: int, location: str):
    """Add a colour to the palette dict, appending location if already present."""
    key = f"{r5},{g5},{b5}"
    if key not in palette:
        palette[key] = {
            "RGB5":      [r5, g5, b5],
            "RGB8":      [scale_5_to_8(r5), scale_5_to_8(g5), scale_5_to_8(b5)],
            "locations": []
        }
    if location not in palette[key]["locations"]:
        palette[key]["locations"].append(location)

def parse_rgb_values(rgb_str: str) -> list[tuple[int,int,int]]:
    """
    Parse one or more RGB triples from a string like "28,31,16, 21,21,21".
    Returns a list of (r, g, b) tuples.
    """
    nums = [int(x.strip()) for x in re.findall(r'\d+', rgb_str)]
    return [(nums[i], nums[i+1], nums[i+2]) for i in range(0, len(nums)-2, 3)]

# ── bg_tiles.pal parser ────────────────────────────────────────────────────────
#
# Format:
#   ; morn               ← section header
#       RGB 28,31,16, 21,21,21, 13,13,13, 07,07,07 ; gray   ← 4 colours + slot name
#
# Sections: morn, day, nite, dark, indoor
# Slots in order: gray, red, green, water, yellow, brown, roof, text

BG_SLOT_NAMES = ["gray", "red", "green", "water", "yellow", "brown", "roof", "text"]
BG_SECTIONS   = ["morn", "day", "nite", "dark", "indoor"]

def parse_bg_tiles_pal(text: str, palette: dict, tileset_name: str,
                       is_interior: bool, used_slots: set[str]):
    """
    Parse bg_tiles.pal and add colours for the slots actually used by this tileset.
    Interior tilesets use only the 'indoor' section.
    Exterior tilesets use morn, day, nite.
    """
    sections_to_use = ["indoor"] if is_interior else ["morn", "day", "nite"]

    current_section = None
    slot_index = 0

    for line in text.splitlines():
        stripped = line.strip()

        # Section header e.g. "; morn" or "; day"
        if stripped.startswith(";"):
            comment = stripped[1:].strip().lower()
            if comment in BG_SECTIONS:
                current_section = comment
                slot_index = 0
            continue

        if not stripped.lower().startswith("rgb"):
            continue
        if current_section not in sections_to_use:
            slot_index += 1
            continue

        # Extract slot name from inline comment
        slot_name = None
        if ";" in stripped:
            slot_name = stripped.split(";", 1)[1].strip().lower()
        if slot_name is None and slot_index < len(BG_SLOT_NAMES):
            slot_name = BG_SLOT_NAMES[slot_index]

        if slot_name not in used_slots:
            slot_index += 1
            continue

        rgb_part = re.sub(r";.*", "", stripped[3:])   # strip "RGB" prefix and comments
        colours = parse_rgb_values(rgb_part)
        for r5, g5, b5 in colours:
            location = f"{tileset_name} - {slot_name} - {current_section}"
            add_color(palette, r5, g5, b5, location)

        slot_index += 1

# ── roofs.pal parser ───────────────────────────────────────────────────────────
#
# Format:
#   ; group 22 (Cianwood)
#       RGB 15,10,31, 07,05,15 ; morn/day
#       RGB 06,05,17, 02,02,08 ; nite

def parse_roofs_pal(text: str, palette: dict):
    current_group = None

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith(";"):
            comment = stripped[1:].strip()
            if re.match(r"group\s+\d+", comment, re.I):
                current_group = comment
            continue

        if not stripped.lower().startswith("rgb") or current_group is None:
            continue

        # Time-of-day from inline comment
        tod = ""
        if ";" in stripped:
            tod = stripped.split(";", 1)[1].strip()

        rgb_part = re.sub(r";.*", "", stripped[3:])
        colours = parse_rgb_values(rgb_part)
        for r5, g5, b5 in colours:
            location = f"roofs - {current_group} - {tod}"
            add_color(palette, r5, g5, b5, location)

# ── custom .pal parser ─────────────────────────────────────────────────────────
#
# Format (e.g. battle_tower_inside.pal):
#   ; gray
#       RGB 30, 28, 26
#       RGB 19, 19, 19
#       RGB 13, 13, 13
#       RGB 07, 07, 07
#   ; red
#       RGB 30, 28, 26
#       ...
#
# Each slot has exactly 4 RGB lines.

def parse_custom_pal(text: str, palette: dict, pal_name: str):
    current_slot  = None
    current_group = None

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith(";"):
            comment = stripped[1:].strip()
            # Group header e.g. "; group 22 (Cianwood)" or "; morn"
            if re.match(r"group\s+\d+", comment, re.I):
                current_group = comment
                current_slot  = None
            elif comment.lower() in ("morn", "day", "nite", "dark",
                                      "morn/day", "indoor", "overworld water"):
                current_slot = comment.lower()
            else:
                # Treat as slot name
                current_slot = comment.lower()
            continue

        if not stripped.lower().startswith("rgb"):
            continue

        # Build location string
        parts = [pal_name]
        if current_group:
            parts.append(current_group)
        if current_slot:
            parts.append(current_slot)
        location = " - ".join(parts)

        rgb_part = re.sub(r";.*", "", stripped[3:])
        colours = parse_rgb_values(rgb_part)
        for r5, g5, b5 in colours:
            add_color(palette, r5, g5, b5, location)

# ── Tileset palette map parser ─────────────────────────────────────────────────
#
# Returns the set of unique slot names used by this tileset.
# e.g. {"gray", "brown", "water", "red", "green"}

def parse_palette_map(text: str) -> set[str]:
    slots = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("tilepal"):
            continue
        # tilepal FLAG, SLOT, SLOT, ...
        parts = [p.strip().lower() for p in stripped.split(",")]
        # parts[0] = "tilepal FLAG", parts[1:] = slot names
        for slot in parts[1:]:
            slots.add(slot)
    return slots

# ── Interior/exterior detection ────────────────────────────────────────────────

INTERIOR_KEYWORDS = {
    "room", "inside", "facility", "game_corner", "house", "lab",
    "lighthouse", "mansion", "mart", "center", "tower", "ruins",
    "station", "underground"
}

def is_interior(tileset_name: str) -> bool:
    name_lower = tileset_name.lower()
    return any(kw in name_lower for kw in INTERIOR_KEYWORDS)

# ── Tileset list ───────────────────────────────────────────────────────────────
#
# These are all the tilesets in pokecrystal. Each entry is:
#   (tileset_name, custom_pal_or_None)
# where custom_pal_or_None is the pal filename stem if the tileset has its own
# palette override, otherwise None (→ use bg_tiles.pal).
#
# Custom palettes confirmed from the wiki and repo:
#   ice_path, radio_tower, and a few others have their own .pal files.
# We discover this dynamically by trying to fetch <name>.pal.

def get_tileset_names() -> list[str]:
    """
    Return the list of tileset names by fetching the constants file.
    Falls back to a hardcoded list if the fetch fails.
    """
    text = fetch_text("constants/tileset_constants.asm")
    if text:
        names = []
        for line in text.splitlines():
            m = re.match(r'\s*const\s+TILESET_(\w+)', line)
            if m:
                names.append(m.group(1).lower())
        if names:
            print(f"  Found {len(names)} tilesets from constants file")
            return names

    # Hardcoded fallback
    print("  Using hardcoded tileset list")
    return [
        "johto", "johto_modern", "kanto", "johto_cave", "kanto_cave",
        "johto_modern_indoor", "kanto_indoor", "ruins_of_alph",
        "burned_tower", "tin_tower", "ecruteak", "goldenrod",
        "traditional_house", "pokemon_center", "gate", "mart",
        "lighthouse", "ice_path", "radio_tower", "battle_tower",
        "battle_tower_inside", "dark_cave", "whirl_islands",
        "mt_mortar", "mt_silver", "dragons_den", "safari_zone",
        "gym", "lab", "underground", "mansion", "game_corner",
    ]

# ── Main pipeline ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract ROM palette from pokecrystal repo.")
    parser.add_argument("--output", default="palette_rom.json", help="Output JSON file")
    args = parser.parse_args()

    palette: dict = {}

    # ── Fetch shared palette files ─────────────────────────────────────────────
    print("Fetching bg_tiles.pal...")
    bg_tiles_text = fetch_text("gfx/tilesets/bg_tiles.pal")
    if not bg_tiles_text:
        print("ERROR: Could not fetch bg_tiles.pal — cannot continue.")
        return

    print("Fetching roofs.pal...")
    roofs_text = fetch_text("gfx/tilesets/roofs.pal")
    if roofs_text:
        parse_roofs_pal(roofs_text, palette)
        print(f"  Parsed roofs.pal → {len(palette)} colours so far")
    else:
        print("  roofs.pal not found, skipping")

    # ── Get tileset list ───────────────────────────────────────────────────────
    print("\nFetching tileset list...")
    tileset_names = get_tileset_names()

    # ── Process each tileset ───────────────────────────────────────────────────
    for tileset in tileset_names:
        print(f"\n[{tileset}]")
        interior = is_interior(tileset)
        print(f"  Type: {'interior' if interior else 'exterior'}")

        # 1. Fetch palette map to find used slots
        pal_map_path = f"gfx/tilesets/{tileset}_palette_map.asm"
        pal_map_text = fetch_text(pal_map_path)
        if not pal_map_text:
            print(f"  No palette map found at {pal_map_path}, skipping")
            continue
        used_slots = parse_palette_map(pal_map_text)
        print(f"  Used slots: {', '.join(sorted(used_slots))}")

        # 2. Check for custom .pal override
        custom_pal_text = fetch_text(f"gfx/tilesets/{tileset}.pal")
        if custom_pal_text:
            print(f"  Using custom palette: {tileset}.pal")
            parse_custom_pal(custom_pal_text, palette, tileset)
        else:
            print(f"  Using bg_tiles.pal")
            parse_bg_tiles_pal(bg_tiles_text, palette, tileset, interior, used_slots)

        print(f"  Palette total: {len(palette)} colours")

    # ── Save ───────────────────────────────────────────────────────────────────
    with open(args.output, "w") as f:
        json.dump(palette, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Done! Total unique colours: {len(palette)}")
    print(f"Saved to: {args.output}")


if __name__ == "__main__":
    main()