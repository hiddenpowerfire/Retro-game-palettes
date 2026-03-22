"""
ROM Palette Visualizer
======================
Loads palette_rom.json and produces a hue-column plot:
  - sqrt(n) columns, each representing an equal hue band (0–360°)
  - Within each column, colours sorted light → dark by CIELAB L*
  - Achromatic colours (grey/black/white) in a separate rightmost column

Requirements:
    pip install pillow matplotlib numpy

Usage:
    python plot_rom_palette.py
    python plot_rom_palette.py --palette palette_rom.json
    python plot_rom_palette.py --emulator gambatte
    python plot_rom_palette.py --dedup-n 1
    python plot_rom_palette.py --output palette_rom.png
"""

import json
import math
import argparse
import colorsys
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image, ImageCms


# ── 5-bit → 8-bit scaling methods ─────────────────────────────────────────────

def scale_accurate(r5: int, g5: int, b5: int) -> tuple[int, int, int]:
    """Linear: 0→0, 31→255. Matches intended hardware values."""
    return (round(r5 * 255 / 31), round(g5 * 255 / 31), round(b5 * 255 / 31))

#def scale_shift(r5: int, g5: int, b5: int) -> tuple[int, int, int]:
#    """Bit-shift ×8. Simple but 31→248. Used by older emulators."""
#    return (r5 << 3, g5 << 3, b5 << 3)
def scale_standard(r5: int, g5: int, b5: int) -> tuple[int, int, int]:
    """
    Standard emulator RGB555→RGB888 conversion: (v << 3) | (v >> 2).
    Left-shifts by 3 to fill the high 5 bits, then folds the top 3 bits
    of the original value back into the empty low 3 bits. This ensures
    0→0 and 31→255 (pure black and white are exact), with steps of 8 or 9
    distributed evenly — eliminating the banding of a plain ×8 shift.
    Used as the baseline conversion in most GBC emulator test suites.
    """
    def ch(v): return (v << 3) | (v >> 2)
    return (ch(r5), ch(g5), ch(b5))

def scale_gambatte(r5: int, g5: int, b5: int) -> tuple[int, int, int]:
    """
    Gambatte/SameBoy LCD colour correction matrix.
    Simulates the GBC screen's yellowish tint and contrast.
    Used by Gambatte, SameBoy, and the 3DS Virtual Console.
    """
    r8 = min(255, (r5 * 13 + g5 *  2             ) * 8 // 16)
    g8 = min(255, (            g5 * 12 + b5 *  4 ) * 8 // 16)
    b8 = min(255, (r5 *  3 + g5 *  2 + b5 * 11  ) * 8 // 16)
    return (r8, g8, b8)

SCALE_METHODS = {
    "accurate": scale_accurate,
    "standard": scale_standard,
    "gambatte": scale_gambatte,
}

SCALE_LABELS = {
    "accurate": "8-bit: round(v × 255/31)",
    "standard": "8-bit: (v << 3) | (v >> 2)",
    "gambatte": "8-bit: Gambatte LCD correction",
}


# ── CIELAB darkness ────────────────────────────────────────────────────────────

_srgb_to_lab = ImageCms.buildTransform(
    ImageCms.createProfile("sRGB"),
    ImageCms.createProfile("LAB"),
    "RGB", "LAB"
)

def rgb_to_darkness(r: int, g: int, b: int) -> float:
    """CIELAB L* as darkness: 0.0 = lightest, 1.0 = darkest."""
    img = Image.new("RGB", (1, 1), (r, g, b))
    lab = ImageCms.applyTransform(img, _srgb_to_lab)
    L = lab.getpixel((0, 0))[0]
    return 1.0 - L / 255.0


# ── Load ───────────────────────────────────────────────────────────────────────

def load_palette(path: str, n: int = 0,
                 scale_fn=scale_accurate) -> list[dict]:
    with open(path, "r") as f:
        raw = json.load(f)
    result = []
    for key, v in raw.items():
        r5, g5, b5 = v["RGB5"]
        r8, g8, b8 = scale_fn(r5, g5, b5)
        h, s, _ = colorsys.rgb_to_hsv(r8 / 255, g8 / 255, b8 / 255)
        result.append({
            "RGB":       (r8, g8, b8),   # display RGB — varies by scale_fn
            "RGB5":      (r5, g5, b5),   # ground truth, always from JSON
            "H":         h,
            "S":         s,
            "darkness":  rgb_to_darkness(r8, g8, b8),
            "locations": v["locations"],
        })
    print(f"  Deduplication (n={n}): ", end="")
    result = deduplicate_palette(result, n=n)
    return result


# ── Deduplication ──────────────────────────────────────────────────────────────

def deduplicate_palette(colors: list[dict], n: int = 0) -> list[dict]:
    """Merge colours within Chebyshev distance n in 5-bit space."""
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

    quantised = [c["RGB5"] for c in colors]   # already 5-bit, no need to re-quantise
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

    print(f"{len(colors)} → {len(result)} colours ({n_collapsed} removed)")
    return result


# ── Plot: hue-column grid ──────────────────────────────────────────────────────

def plot_hue_columns(colors: list[dict], ax: plt.Axes, subtitle: str = ""):
    n      = len(colors)
    n_cols = math.ceil(math.sqrt(n))

    achromatic = [c for c in colors if c["S"] == 0.0]
    chromatic  = [c for c in colors if c["S"] >  0.0]

    n_hue_cols = n_cols - 1
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
    ax.set_title(subtitle, fontsize=9, pad=8)
    #ax.axvline(n_cols - 1.5, color="gray", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.axis("off")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Visualize ROM palette JSON.")
    parser.add_argument("--palette",  default="palette_rom.json", help="Path to palette JSON")
    parser.add_argument("--emulator", default="accurate",
                        choices=["accurate", "shift", "gambatte"],
                        help="Display colour scaling: "
                             "'accurate' = round(v*255/31) (default), "
                             "'shift' = v<<3 (older emulators), "
                             "'gambatte' = LCD colour correction")
    parser.add_argument("--dedup-n",  type=int, default=0,        help="Chebyshev distance for deduplication in 5-bit space (default: 0)")
    parser.add_argument("--output",   default=None,               help="Save to file (e.g. palette_rom.png)")
    args = parser.parse_args()

    scale_fn = SCALE_METHODS[args.emulator]
    print(f"Loading {args.palette} (scaling: {args.emulator})...")
    colors = load_palette(args.palette, n=args.dedup_n, scale_fn=scale_fn)
    print(f"  {len(colors)} colours loaded")

    fig, ax = plt.subplots(figsize=(10, 8))
    subtitle = f"{len(colors)} colours  —  {SCALE_LABELS[args.emulator]}"
    plot_hue_columns(colors, ax, subtitle=subtitle)
    plt.suptitle("Pokémon Crystal Palette", fontsize=13, fontfamily="Andale Mono", y=1.01)
    plt.tight_layout()

    if args.output:
        plt.savefig(args.output, dpi=150, bbox_inches="tight")
        print(f"Saved to {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()