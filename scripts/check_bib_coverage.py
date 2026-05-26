"""Verify per-paper .bbl cite keys are present in the sidecar .bib files.

For each ``arXiv-<YYMM>.<id>v<n>/`` folder under ``arxiv-papers/``:
- find a ``*.bbl`` at the folder root (paper layouts vary),
- read cite keys from ``\\bibitem{...}`` entries,
- look up the sidecar ``arxiv-papers/arXiv-<YYMM>.bib`` and read its entry keys,
- print any cite keys cited in the paper but missing from the sidecar bib.

Exit code is non-zero if any paper has missing keys (or a missing sidecar bib).

Run with:  uv run scripts/check_bib_coverage.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARXIV_DIR = REPO_ROOT / "arxiv-papers"

PAPER_FOLDER_RE = re.compile(r"^arXiv-(\d{4})\.\d+v\d+$")
BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
BIB_ENTRY_RE = re.compile(
    r"^[ \t]*@(?!string\b|preamble\b|comment\b)[A-Za-z]+[ \t]*\{\s*([^\s,]+)\s*,",
    re.IGNORECASE | re.MULTILINE,
)


def cite_keys_from_bbl(bbl_path: Path) -> list[str]:
    text = bbl_path.read_text(encoding="utf-8", errors="replace")
    seen: set[str] = set()
    ordered: list[str] = []
    for key in BIBITEM_RE.findall(text):
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def entry_keys_from_bib(bib_path: Path) -> set[str]:
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    return set(BIB_ENTRY_RE.findall(text))


def find_bbl(folder: Path) -> Path | None:
    candidates = sorted(folder.glob("*.bbl"))
    return candidates[0] if candidates else None


def check_paper(folder: Path) -> tuple[bool, bool]:
    """Return (had_problem, was_checkable)."""
    m = PAPER_FOLDER_RE.match(folder.name)
    if not m:
        return (False, False)
    yymm = m.group(1)
    bib_path = ARXIV_DIR / f"arXiv-{yymm}.bib"
    bbl_path = find_bbl(folder)

    print(f"\n=== {folder.name} ===")

    if bbl_path is None:
        print("  no .bbl in folder — skipping (paper has not been compiled)")
        return (False, False)
    if not bib_path.exists():
        print(f"  ERROR: sidecar bib {bib_path.name} not found")
        return (True, True)

    cite_keys = cite_keys_from_bbl(bbl_path)
    bib_keys = entry_keys_from_bib(bib_path)

    missing = [k for k in cite_keys if k not in bib_keys]
    extra = sorted(bib_keys - set(cite_keys))

    print(f"  bbl: {bbl_path.name}    bib: {bib_path.name}")
    print(
        f"  cited: {len(cite_keys)}    in bib: {len(bib_keys)}    "
        f"missing: {len(missing)}    extra-in-bib: {len(extra)}"
    )

    if missing:
        print("  MISSING from .bib:")
        for k in missing:
            print(f"    - {k}")

    return (bool(missing), True)


def main() -> int:
    if not ARXIV_DIR.is_dir():
        print(f"error: {ARXIV_DIR} not found", file=sys.stderr)
        return 2

    folders = sorted(p for p in ARXIV_DIR.iterdir() if p.is_dir())
    any_problems = False
    checked = 0

    for folder in folders:
        had_problem, was_checkable = check_paper(folder)
        if was_checkable:
            checked += 1
        any_problems = any_problems or had_problem

    print(f"\nChecked {checked} paper(s).", end=" ")
    print("Problems found." if any_problems else "All cite keys present.")
    return 1 if any_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
