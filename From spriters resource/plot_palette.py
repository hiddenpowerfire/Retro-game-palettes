"""
Palette Visualizer
==================
Loads palette.json and produces two plots:

  1. Square grid     — colors sorted by CIELAB L* lightness (light → dark)
  2. Hue-column grid — sqrt(n) columns by hue band, each sorted light → dark.
                       Achromatic colors in a separate rightmost column.

Requirements:
    pip install pillow matplotlib numpy

Usage:
    python plot_palette.py
    python plot_palette.py --palette my_palette.json
    python plot_palette.py --dedup-n 1
    python plot_palette.py --output palette.png
"""

import json
import math
import argparse
import colorsys
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image, ImageCms


# ── CIELAB darkness ────────────────────────────────────────────────────────────

# Build sRGB → LAB transform once at module level (no external file needed)
_srgb_to_lab = ImageCms.buildTransform(
    ImageCms.createProfile("sRGB"),
    ImageCms.createProfile("LAB"),
    "RGB", "LAB"
)

def rgb_to_darkness(r: int, g: int, b: int) -> float:
    """
    Return perceptual darkness via CIELAB L*, range 0.0 (lightest) – 1.0 (darkest).
    PIL encodes L* as 0–255 where 255 = L* 100 (white).
    """
    img = Image.new("RGB", (1, 1), (r, g, b))
    lab = ImageCms.applyTransform(img, _srgb_to_lab)
    L = lab.getpixel((0, 0))[0]   # 0–255
    return 1.0 - L / 255.0


# ── Load ───────────────────────────────────────────────────────────────────────

def load_palette(path: str, n: int = 0) -> list[dict]:
    with open(path, "r") as f:
        raw = json.load(f)
    result = []
    for v in raw.values():
        r, g, b = v["RGB"]
        h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        result.append({
            "RGB":       (r, g, b),
            "H":         h,                       # hue 0.0–1.0
            "S":         s,                       # saturation 0.0–1.0
            "darkness":  rgb_to_darkness(r, g, b),# CIELAB L*: 0.0=light, 1.0=dark
            "locations": v["locations"],
        })
    print("Checking for 15-bit duplicates...")
    result = deduplicate_palette(result, n=n)
    return result


# ── Deduplication ──────────────────────────────────────────────────────────────

def deduplicate_palette(colors: list[dict], n: int = 0) -> list[dict]:
    """
    Merge colors whose 5-bit-per-channel (15-bit) representations differ by
    at most n in every channel (Chebyshev distance). Uses union-find so
    transitive chains are fully collapsed. Representative is the color closest
    to the group mean; locations lists are merged.
    """
    def quantise(r, g, b):
        return (r >> 3, g >> 3, b >> 3)

    def chebyshev(q1, q2):
        return max(abs(a - b) for a, b in zip(q1, q2))

    parent = list(range(len(colors)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    quantised = [quantise(*c["RGB"]) for c in colors]
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            if chebyshev(quantised[i], quantised[j]) <= n:
                union(i, j)

    from collections import defaultdict
    groups = defaultdict(list)
    for i in range(len(colors)):
        groups[find(i)].append(i)

    result = []
    n_collapsed = 0
    for indices in groups.values():
        group = [colors[i] for i in indices]
        if len(group) > 1:
            n_collapsed += len(group) - 1
            print(f"  Merged: {', '.join(str(c['RGB']) for c in group)}")
        merged_locations = []
        for c in group:
            for loc in c["locations"]:
                if loc not in merged_locations:
                    merged_locations.append(loc)
        mean_r = sum(c["RGB"][0] for c in group) / len(group)
        mean_g = sum(c["RGB"][1] for c in group) / len(group)
        mean_b = sum(c["RGB"][2] for c in group) / len(group)
        rep = min(group, key=lambda c:
                  (c["RGB"][0]-mean_r)**2 + (c["RGB"][1]-mean_g)**2 + (c["RGB"][2]-mean_b)**2)
        rep = dict(rep)
        rep["locations"] = merged_locations
        result.append(rep)

    print(f"  Deduplication (n={n}): {len(colors)} → {len(result)} colors ({n_collapsed} removed)")
    return result


# ── Hue-column grid ───────────────────────────────────────────────────────────

def plot_hue_columns(colors: list[dict], ax: plt.Axes):
    n      = len(colors)
    n_cols = math.ceil(math.sqrt(n))

    achromatic = [c for c in colors if c["S"] == 0.0]
    chromatic  = [c for c in colors if c["S"] >  0.0]

    n_hue_cols = n_cols - 1   # reserve last column for neutrals
    bins = [[] for _ in range(n_hue_cols)]
    for c in chromatic:
        idx = min(int(c["H"] * n_hue_cols), n_hue_cols - 1)
        bins[idx].append(c)

    for b in bins:
        b.sort(key=lambda c: c["darkness"])
    achromatic.sort(key=lambda c: c["darkness"])

    max_rows = max((len(b) for b in bins), default=0)
    max_rows = max(max_rows, len(achromatic))

    grid = np.ones((max_rows, n_cols, 3), dtype=np.uint8) * 255
    for col_idx, col_colors in enumerate(bins):
        for row_idx, c in enumerate(col_colors):
            grid[row_idx, col_idx] = c["RGB"]
    for row_idx, c in enumerate(achromatic):
        grid[row_idx, n_cols - 1] = c["RGB"]

    ax.imshow(grid, interpolation="nearest", aspect="equal")
    #ax.set_title(
    #    f"{n_hue_cols} hue columns × up to {max_rows} rows  "
    #    f"(+1 neutral column, {len(achromatic)} colors)",
    #    fontsize=9, pad=8
    #)
    #ax.axvline(n_cols - 1.5, color="gray", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.axis("off")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Visualize a palette JSON file.")
    parser.add_argument("--palette", default="palette.json", help="Path to palette JSON")
    parser.add_argument("--dedup-n", type=int, default=0,    help="Chebyshev distance for duplicate merging in 5-bit color space (default: 0)")
    parser.add_argument("--output",  default=None,           help="Save to file (e.g. palette.png)")
    args = parser.parse_args()

    print(f"Loading palette from {args.palette} (dedup n={args.dedup_n})...")
    colors = load_palette(args.palette, n=args.dedup_n)
    print(f"  {len(colors)} colors loaded")
    n_cols = math.ceil(math.sqrt(len(colors)))
    print(f"  sqrt(n) = {n_cols}  →  {n_cols - 1} hue bands + 1 neutral column")

    fig, ax = plt.subplots(figsize=(10, 8))
    plot_hue_columns(colors, ax)

    plt.suptitle("Pokemon Crystal — Map Color Palette", fontsize=13, y=1.01)
    plt.tight_layout()

    if args.output:
        plt.savefig(args.output, dpi=150, bbox_inches="tight")
        print(f"Saved to {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()