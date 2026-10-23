"""Local one-off helper for the A2 demo: copy the bundled sample PDFs next to the UI and render a small PNG of page 1 of each.

Run from the demo-api folder, inside its venv:   python tools\\a2_assets.py
Reads  ..\\a2-extraction-api\\web\\public\\samples   (samples.json + the PDFs it lists)
Writes static\\a2\\samples\\                         (same files, plus <id>.png)
pdfplumber and Pillow are already installed as dependencies of a2-extraction.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pdfplumber
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "a2-extraction-api" / "web" / "public" / "samples"
DST = ROOT / "static" / "a2" / "samples"
ID_RE = re.compile(r"^[a-z0-9_]+$")
DPI = 90
STEPS = [(760, 48), (600, 24), (500, 16), (420, 16)]  # (max width, colours): try smaller until the PNG is under the limit
MAX_PNG_KB = 120


def main() -> int:
    manifest = SRC / "samples.json"
    if not manifest.exists():
        print(f"ERROR: {manifest} not found")
        return 1
    items = json.loads(manifest.read_text(encoding="utf-8"))
    problems = []
    for it in items:
        stem = Path(it.get("file", "")).stem
        if not ID_RE.match(stem) or not (SRC / it["file"]).exists() or not {"title", "shows", "expected_review"} <= it.keys():
            problems.append(it.get("file", "?"))
    if problems:
        print("ERROR: bad or missing manifest entries:", problems)
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest, DST / "samples.json")
    for it in items:
        pdf_path = SRC / it["file"]
        shutil.copyfile(pdf_path, DST / it["file"])
        with pdfplumber.open(pdf_path) as pdf:
            img = pdf.pages[0].to_image(resolution=DPI).original.convert("RGB")
        png_path = DST / f"{Path(it['file']).stem}.png"
        for max_w, colors in STEPS:
            small = img if img.width <= max_w else img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
            small.quantize(colors=colors).save(png_path, optimize=True)
            if png_path.stat().st_size <= MAX_PNG_KB * 1024:
                break
        print(f"{it['file']:<24} pdf {pdf_path.stat().st_size / 1024:7.1f} KB   png {png_path.stat().st_size / 1024:6.1f} KB   {small.width}x{small.height}")
    print(f"done: {len(items)} samples in {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
