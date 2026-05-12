#!/usr/bin/env python3
"""
Collect metrics from HTML diff reports in out/ and write a TSV summary.

Run as (in the library root, for paths to work correctly):
python -m helpers.collect_reports
"""

import re
import sys
from urllib.parse import unquote

from pytest_config import OUT_PATH

OUT_DIR = OUT_PATH
OUTPUT_TSV = OUT_DIR / "report_summary.tsv"


def grab(label: str, html: str) -> str:
    """Extract the stat-value that follows a given stat-label text."""
    m = re.search(
        re.escape(label) + r'</div>\s*<div class="stat-value">([^<]+)</div>',
        html,
    )
    return m.group(1).strip() if m else ""


def collect() -> None:
    files = sorted(OUT_DIR.glob("*.diff.html"))
    if not files:
        print("No .diff.html files found in out/", file=sys.stderr)
        sys.exit(1)

    rows = []
    has_ser = False

    for f in files:
        html = f.read_text(encoding="utf-8")

        hint_m = re.search(r"<div class='hint'>(.*?)</div>", html, re.DOTALL)
        hint = hint_m.group(1) if hint_m else ""
        provider = re.search(r"^Provider:\s*(.+)", hint, re.MULTILINE)
        sound = re.search(r"^Sound:\s*(.+)", hint, re.MULTILINE)

        row = {
            "provider": provider.group(1).strip() if provider else "",
            "sound": unquote(sound.group(1).strip()) if sound else "",
            "WER": grab("Word Error Rate", html),
            "CER": grab("Character Error Rate", html),
            "SER": grab("Semantic Error Rate", html),
        }
        if row["SER"]:
            has_ser = True
        rows.append(row)

    cols = ["provider", "sound", "WER", "CER"] + (["SER"] if has_ser else [])
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(row.get(c, "") for c in cols))

    tsv = "\n".join(lines) + "\n"
    OUTPUT_TSV.write_text(tsv, encoding="utf-8")
    print(tsv, end="")
    print(f"[{len(rows)} rows -> {OUTPUT_TSV}]", file=sys.stderr)


if __name__ == "__main__":
    collect()