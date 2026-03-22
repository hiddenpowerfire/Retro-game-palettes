```
extract_palette.py
```
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



```
plot_palette.py
```
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
