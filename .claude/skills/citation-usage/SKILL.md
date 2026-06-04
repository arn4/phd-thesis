---
name: citation-usage
description: Generate an HTML report counting how often each papers-bibliography.bib entry is cited across the papers under papers/, with a per-paper main·appendix breakdown, Total/Refined counts, and an importance Score (the default ordering) that rewards repeated and widely-spread citations, down-weights appendix and excluded-paper citations, and time-discounts recent references. Rows that are the thesis's own papers are highlighted. Use when the user asks to audit/visualize citation usage, count references, rank reference importance, find never-cited or thesis-carrying references, or runs /citation-usage.
---

# citation-usage

Wrap `scripts/citation_usage.py`, which counts how many times every entry in the
thesis-wide `papers-bibliography.bib` is cited across the extracted paper bodies under
`papers/<YYMM>/`, and writes a single self-contained HTML report. Read-only over the repo;
the only thing it writes is the one HTML file.

## Procedure

Run the script (from the repo root):

```
uv run scripts/citation_usage.py
```

Add `--open` to open the report in the browser afterwards (macOS `open`), or `--output PATH`
to write somewhere other than the default. The score knobs are tunable:
`--main-cap` (depth saturation, default 5), `--main-decay` (0.5), `--app-weight` (0.3),
`--breadth-floor` (0.4), `--external-weight` (0.5; set 1.0 to stop down-weighting
excluded-paper citations). Then relay the stdout summary — it leads with the numbers:

- `bib entries: N (X cited, Y never cited)`
- `total citations: N occurrences across M papers on disk (main, appendix)`
- `papers on disk:` / `in final thesis:` — the two paper sets
- `own-paper rows:` — bib entries that ARE the thesis's own papers (`YYMM→key`), plus a note
  on which thesis papers have no bib entry (not cited by other chapters)
- `top by score:` — the three highest-scoring references
- `orphan keys:` — only printed if some cited key is missing from the bib (a stale/renamed
  key worth flagging to Luca)
- `wrote:` — the artifact path

Keep the report under ~7 lines; point the user at the HTML for the full table.

## What the report contains

A self-contained HTML page (inline CSS + vanilla JS, no external assets):

- **One row per bib entry** — *every* entry, cited or not — sorted by the importance **Score**
  descending (click any header to re-sort).
- **One column per paper folder on disk** (`papers/<YYMM>/`), each cell showing
  **main·appendix** counts. Columns for papers in the final thesis are highlighted with a `✓`;
  the others are greyed.
- **Main vs appendix:** a citation is *appendix* when it sits in a file under the paper's
  `appendices/` directory; everything else (`sections/`, `main.tex`, `abstract.tex`) is *main*.
- **Total** = raw citation occurrences summed over *all* papers on disk, shown as the combined
  count with its `main / app` split.
- **Refined** = the same but summed over *only* the papers actually compiled into the thesis
  (the `✓` columns), again with its `main / app` split. The Total↔Refined gap shows what each
  excluded paper contributes.
- Counts are raw occurrences: a key cited 3× in one paper counts 3.
- **Score / Refined score** — the importance model (full formula in the report's collapsible
  "How the importance Score works" panel):
  `Score = ( Σ_papers w·[ S(m) + 0.3·a ] ) × ( 0.4 + 0.6·coverage )`, where
  - `S(m) = 1 + 4·(1 − 0.5^(m−1))` — main citations have diminishing returns with an early
    boost: 1→1, 2→3 (more than double), saturating at 5 (10 vs 15 barely differ);
  - appendix citations are flat at `0.3` each (all equal, less than a full main cite);
  - `w` = 1 for in-thesis source papers, **0.5** for on-disk-but-excluded ones (so a citation
    from a paper not in the thesis counts less);
  - `coverage = citing / eligible`, eligible = papers dated no earlier than the reference, so
    a recent work isn't penalised for older papers that couldn't have cited it;
  - **Refined score** applies the same formula over only the in-thesis papers.
  Hover any Score cell for its `depth × breadth (citing/eligible)` breakdown.
- **Own-paper highlighting** — rows that are the thesis's own papers (detected by title match
  against the `arXiv-<YYMM>-metadata.json` sidecars, plus a small override table in the script)
  are tinted and carry a `★ YYMM` badge, so cross-citations between chapters stand out.
- Interactive: click any header to re-sort, filter by key/title, toggle "hide never-cited".
- A diagnostics section lists any keys cited in the bodies but absent from the bib.

## Inputs (all read-only)

- `papers-bibliography.bib` — the authoritative entry list (keys via the same regex as
  `scripts/check_bib_coverage.py`; titles/authors/years via `bibtexparser` for display).
- `papers/<YYMM>/**.tex` — scanned for `\cite`/`\citep` (and other cite commands, defensively),
  comments stripped, multi-key `{a,b,c}` split.
- `thesis.tex` — the **in-thesis set** is derived from its uncommented
  `\include{chapters/paper-<YYMM>}` lines, so the Refined column tracks the lineup
  automatically. The `\includeonly{...}` build selector is ignored on purpose.
- `arxiv-papers/arXiv-<YYMM>-metadata.json` — read (not modified) to detect which bib entries
  are the thesis's own papers. If a paper's title drifts from its bib title, add the bib key to
  `THESIS_OWN_OVERRIDES` at the top of the script.

## Output

- `build/citation-usage.html` by default. `build/` is **gitignored**, so the artifact is
  **never committed** — that is intentional; do not move it under version control or add it to
  a commit.

## Constraints

- The report is a point-in-time snapshot. Re-run it after editing the bib or any paper body to
  refresh; nothing regenerates it automatically (it is not wired into CI or `latexmkrc`).
- Read-only: the script never touches the bib, `papers/`, `thesis.tex`, or any
  `arxiv-papers/` sidecar — it only writes the one HTML file.
- Needs no new dependency (`bibtexparser` is already declared in `pyproject.toml`).
