"""
Image Palette Remapper
=======================
Takes an input image and a palette dictionary, and produces:

  1. A remapped .png where every pixel is replaced by the closest palette colour
     (Euclidean distance in RGB space, via KDTree for speed).
  2. A hue-column palette plot of the input image's own colours,
     named after the input image.
  3. A palette .json of the input image's own colours.

Reuses functions from extract_palette.py and plot_palette.py directly.

Requirements:
    pip install pillow matplotlib numpy scipy

Usage:
    python remap_image.py --image photo.png --palette palette_rom.json
    python remap_image.py --image photo.png --palette palette_rom.json --emulator gambatte
    python remap_image.py --image photo.png --palette palette.json --dedup-n 1
"""
import os
import sys
import json
import math
import argparse
import colorsys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from PIL import Image, ImageCms
from scipy.spatial import KDTree

# ── Import reusable functions from existing scripts ────────────────────────────
# Add the directory containing our scripts to the path if needed
sys.path.insert(0, os.path.join(os.getcwd(), os.pardir, 'From spriters resource')) #str(Path(__file__).parent))

from extract_palette import analyze_image
from plot_palette    import (rgb_to_darkness, deduplicate_palette,
                              plot_hue_columns)
from plot_rom_palette import (SCALE_METHODS, SCALE_LABELS)

# ── Load palette JSON → list of 8-bit RGB tuples ───────────────────────────────

def load_palette_colours(path: str, scale_fn) -> list[tuple[int,int,int]]:
    """
    Load a palette JSON and return a list of (R, G, B) tuples in 8-bit.
    Handles both formats:
      - sprite palette  (keys "R,G,B", values have "RGB")
      - ROM palette     (keys "R5,G5,B5", values have "RGB5" and "RGB8")
    """
    with open(path) as f:
        raw = json.load(f)

    colours = []
    for v in raw.values():
        if "RGB5" in v:
            r8, g8, b8 = scale_fn(*v["RGB5"])
        else:
            r8, g8, b8 = v["RGB"]
        colours.append((int(r8), int(g8), int(b8)))
    return colours

# ── Pixel remapping ────────────────────────────────────────────────────────────

def remap_image(img: Image.Image,
                palette_colours: list[tuple[int,int,int]]) -> Image.Image:
    """
    Replace every pixel in img with the closest colour from palette_colours,
    using Euclidean distance in RGB space via KDTree.
    """
    arr     = np.array(img.convert("RGB"), dtype=np.float32)   # H×W×3
    h, w, _ = arr.shape

    palette_arr = np.array(palette_colours, dtype=np.float32)  # K×3
    tree        = KDTree(palette_arr)

    pixels_flat = arr.reshape(-1, 3)                            # (H*W)×3
    _, indices  = tree.query(pixels_flat)                       # nearest neighbour
    remapped    = palette_arr[indices].astype(np.uint8)
    remapped    = remapped.reshape(h, w, 3)

    return Image.fromarray(remapped, mode="RGB")

# ── Build palette dict from image (reuses extract_palette.analyze_image) ───────

def build_image_palette(image_path: Path, image_name: str) -> dict:
    """Extract unique colours from image into a palette dict."""
    palette: dict = {}
    analyze_image(image_path, image_name, palette)
    return palette

# ── Build color list for plotting (mirrors plot_palette.load_palette) ──────────

def palette_dict_to_color_list(palette: dict, dedup_n: int = 0) -> list[dict]:
    result = []
    for v in palette.values():
        r, g, b = v["RGB"]
        h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        result.append({
            "RGB":       (r, g, b),
            "H":         h,
            "S":         s,
            "darkness":  rgb_to_darkness(r, g, b),
            "locations": v["locations"],
        })
    if dedup_n >= 0:
        result = deduplicate_palette(result, n=dedup_n)
    return result

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Remap image to palette colours.")
    parser.add_argument("--image",    required=True,           help="Input image (.png)")
    parser.add_argument("--palette",  required=True,           help="Palette JSON file")
    parser.add_argument("--emulator", default="accurate",
                        choices=["accurate", "standard", "gambatte"],
                        help="8-bit scaling for ROM palettes (default: accurate)")
    parser.add_argument("--dedup-n",  type=int, default=0,     help="Dedup threshold for extracted image palette (default: 0)")
    parser.add_argument("--output-dir", default=None,          help="Output directory (default: same as input image)")
    args = parser.parse_args()

    image_path   = Path(args.image)
    palette_path = Path(args.palette)
    stem         = image_path.stem
    out_dir      = Path(args.output_dir) if args.output_dir else image_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    scale_fn = SCALE_METHODS[args.emulator]

    # ── 1. Remap image ─────────────────────────────────────────────────────────
    print(f"Loading palette from {palette_path}  (scaling: {args.emulator})...")
    palette_colours = load_palette_colours(str(palette_path), scale_fn)
    print(f"  {len(palette_colours)} palette colours")

    print(f"Loading image {image_path}...")
    img = Image.open(image_path)
    print(f"  Size: {img.size}")

    print("Remapping pixels...")
    remapped = remap_image(img, palette_colours)
    remap_out = out_dir / f"{stem}_remapped.png"
    remapped.save(remap_out)
    print(f"  Saved: {remap_out}")

    # ── 2. Extract palette from original image ─────────────────────────────────
    print(f"\nExtracting palette from {image_path}...")
    image_palette = build_image_palette(image_path, stem)
    print(f"  {len(image_palette)} unique colours found")

    palette_json_out = out_dir / f"{stem}_palette.json"
    with open(palette_json_out, "w") as f:
        json.dump(image_palette, f, indent=2)
    print(f"  Saved: {palette_json_out}")

    # ── 3. Plot palette of original image ──────────────────────────────────────
    print("Plotting image palette...")
    colors = palette_dict_to_color_list(image_palette, dedup_n=args.dedup_n)
    n_cols = math.ceil(math.sqrt(len(colors)))

    fig, ax = plt.subplots(figsize=(10, 8))
    subtitle = (f"{len(colors)} colours  —  {SCALE_LABELS[args.emulator]}"
                if palette_path.name != "palette.json"
                else f"{len(colors)} colours")
    plot_hue_columns(colors, ax)
    ax.set_title(subtitle, fontsize=9, pad=8)
    plt.suptitle(stem, fontsize=13, y=1.01)
    plt.tight_layout()

    palette_plot_out = out_dir / f"{stem}_palette_plot.png"
    plt.savefig(palette_plot_out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {palette_plot_out}")

    print(f"\nDone! Outputs in {out_dir}/")
    print(f"  {remap_out.name}")
    print(f"  {palette_json_out.name}")
    print(f"  {palette_plot_out.name}")


if __name__ == "__main__":
    main()