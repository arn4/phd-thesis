"""Query whether a single BibTeX entry is already covered by the thesis bibliography.

Reuses the exact three-tier disambiguation pipeline from merge_bibs.py
(definitive shared-identifier match -> canonical field equality -> title-BoW
Jaccard candidate) so an entry that already exists under a different cite key
is never silently re-added.

Run:
    uv run scripts/add_bib.py <entry.bib>

<entry.bib> must contain exactly one BibTeX entry. Emits one JSON object to
stdout and always exits 0 (operational errors go to stderr + an error JSON).
This script is QUERY-ONLY: it never writes any file. The add-bib skill performs
the edits to extra-bibliography.bib and thesis.tex.

Output JSON shapes:
    {"status": "found",     "source": "<file>", "key": "<tag>"}
    {"status": "candidate", "source": "<file>", "key": "<tag>", "jaccard": 0.92,
     "existing_preview": "...", "new_preview": "..."}
    {"status": "not_found"}
    {"status": "error", "message": "..."}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser

# Reuse the merge-bibs matching pipeline verbatim so add-bib and merge-bibs
# agree on what counts as a duplicate.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_bibs import Entry, Group, find_match  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPERS_BIB = REPO_ROOT / "papers-bibliography.bib"
EXTRA_BIB = REPO_ROOT / "extra-bibliography.bib"


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _make_parser() -> BibTexParser:
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    parser.homogenize_fields = False
    parser.interpolate_strings = True
    return parser


def load_entries(path: Path) -> list[Entry]:
    """Parse a .bib file into Entry objects (one per record; canonical-only)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        db = bibtexparser.load(f, parser=_make_parser())
    entries: list[Entry] = []
    for raw in db.entries:
        tag = raw["ID"]
        fields = {k.lower(): v for k, v in raw.items() if k not in ("ID", "ENTRYTYPE")}
        entries.append(Entry(
            sidecar=path.name,
            local_tag=tag,
            base_local_tag=tag,
            entry_type=raw["ENTRYTYPE"],
            fields=fields,
        ))
    return entries


def render_preview(entry: Entry) -> str:
    """A compact human-readable rendering of the salient identifying fields."""
    lines = [f"@{entry.entry_type}{{{entry.local_tag},"]
    for fname in ("title", "author", "year", "journal", "booktitle",
                  "publisher", "doi", "eprint"):
        val = entry.fields.get(fname)
        if val:
            lines.append(f"  {fname:9s}: {val}")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        _emit({"status": "error", "message": "usage: add_bib.py <entry.bib>"})
        return 0

    input_path = Path(sys.argv[1])
    if not input_path.is_file():
        _emit({"status": "error", "message": f"input file not found: {input_path}"})
        return 0
    if not PAPERS_BIB.is_file():
        _emit({"status": "error", "message": f"missing {PAPERS_BIB.name}"})
        return 0

    # Parse the candidate entry.
    try:
        new_entries = load_entries(input_path)
    except Exception as exc:  # noqa: BLE001 - report any parse failure to the skill
        _emit({"status": "error", "message": f"could not parse input: {exc}"})
        return 0
    if len(new_entries) != 1:
        _emit({
            "status": "error",
            "message": f"expected exactly 1 entry, parsed {len(new_entries)}",
        })
        return 0
    new_entry = new_entries[0]

    # Build the corpus of existing groups (one group per existing entry).
    # papers-bibliography.bib first so it wins source attribution on ties.
    groups: list[Group] = [Group(canonical=e) for e in load_entries(PAPERS_BIB)]
    if EXTRA_BIB.is_file():
        groups += [Group(canonical=e) for e in load_entries(EXTRA_BIB)]

    # Cite-key collision: same tag already in extra-bibliography.bib counts as
    # found regardless of field differences (can't reuse the key for a new work).
    for g in groups:
        if g.canonical.sidecar == EXTRA_BIB.name and g.canonical.local_tag == new_entry.local_tag:
            _emit({"status": "found", "source": EXTRA_BIB.name, "key": g.canonical.local_tag})
            return 0

    result = find_match(new_entry, groups)

    if result.kind in ("definitive", "canonical_equal"):
        g = result.group
        _emit({
            "status": "found",
            "source": g.canonical.sidecar,
            "key": g.canonical.local_tag,
        })
    elif result.kind == "candidate":
        g = result.group
        _emit({
            "status": "candidate",
            "source": g.canonical.sidecar,
            "key": g.canonical.local_tag,
            "jaccard": round(result.jaccard, 3),
            "existing_preview": render_preview(g.canonical),
            "new_preview": render_preview(new_entry),
        })
    else:
        _emit({"status": "not_found"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
