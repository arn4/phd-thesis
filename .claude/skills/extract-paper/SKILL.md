---
name: extract-paper
description: Rewrite one paper under arxiv-papers/ into a clean thesis-ready layout at papers/YYMM/ (via scripts/paper_extraction.py). Produces abstract.tex, main.tex, sections/<slug>.tex, appendices/<slug>.tex, figs/. Rewrites \cite keys via arXiv-YYMM-citation-map.json, renames macros via arXiv-YYMM-macro-map.json, rewrites figure paths, splits monolithic papers at \section boundaries, ensures the shared papers/stand-alone-paper.tex driver exists, then runs a verification latexmk compile and reports. Use when the user asks to "extract paper", "convert paper YYMM", or "/extract-paper 2302".
---

# extract-paper

Run `scripts/paper_extraction.py` against one paper under `arxiv-papers/`, producing a uniform thesis-ready layout at `papers/YYMM/`, then build it once via the shared standalone driver and report.

## Inputs

A single paper identifier. Accept any of:

- Full folder name: `arXiv-2302.05882v1`
- YYMM prefix with or without `arXiv-`: `arXiv-2302`, `2302`
- arXiv id with or without version: `2302.05882`, `2302.05882v1`

The script resolves to the unique folder under `arxiv-papers/`. If zero or multiple folders match, it exits non-zero — surface that error and stop.

## Prerequisites

Both renaming maps must exist for the paper (they are produced by the upstream `merge-bibs` and `merge-preambles` flows). The script fails fast if either is missing:

- `arxiv-papers/arXiv-YYMM-citation-map.json` — cite-key rewrites (`merge-bibs`).
- `arxiv-papers/arXiv-YYMM-macro-map.json` — macro renames (`merge-preambles`).
- `papers-bibliography.bib`, `papers-dependencies.tex`, `papers-macros.tex` — the merged thesis-wide preamble and bib.

If a prerequisite is missing, surface the error and tell the user to run the upstream skill (`merge-bibs` / `merge-preambles`) first.

## Procedure

Treat `arxiv-papers/arXiv-<id>/` as **strictly read-only**. The script writes only under `papers/` and `build/` (plus a single-line append to `.gitignore` if `build/` isn't ignored yet).

1. **Run the script** from the repo root:

   ```
   uv run scripts/paper_extraction.py <id>                  # extract + verify
   uv run scripts/paper_extraction.py <id> --force          # overwrite existing papers/YYMM/
   uv run scripts/paper_extraction.py <id> --dry-run        # preview the plan, no writes
   uv run scripts/paper_extraction.py <id> --no-compile     # write files but skip latexmk
   ```

   By default, if `papers/YYMM/` already exists the script refuses; pass `--force` to overwrite. The shared `papers/stand-alone-paper.tex` driver is written once and is idempotent.

2. **Output layout** (uniform across all papers, including monolithic ones):

   ```
   papers/YYMM/
     abstract.tex                 # abstract body only, no \begin{abstract} wrapper
     main.tex                     # \input{sections/...} ... \appendix \input{appendices/...}
     sections/<slug>.tex          # one per real section, each with its own \section header
     appendices/<slug>.tex
     figs/                        # figures, subdir structure preserved
   ```

   For modular papers (e.g. 2302/2305/2402/2405/2406/2506), section files mirror the source's `sections/` / `section/` layout. For monolithic papers (2602/2605), the script splits at `\section` boundaries and slugs file names from titles.

3. **Verification compile** (unless `--no-compile`):

   ```
   latexmk -pdf -interaction=nonstopmode -jobname=YYMM -outdir=build/YYMM papers/stand-alone-paper.tex
   ```

   The driver loads the thesis-wide preamble (`papers-dependencies.tex`, `papers-macros.tex`) and bib, picks the paper via `\jobname`, and uses biblatex + biber with the `authoryear-comp` style. The script parses `build/YYMM/YYMM.log` and reports counts by category (hard errors, undefined refs/cites, missing figures, package clashes, over/underfull boxes). Aux artifacts stay under `build/YYMM/`.

   If the compile failed, surface the first hard error verbatim plus the per-category counts. Do **not** retry with guesses — most failures originate in `papers-dependencies.tex` or `papers-macros.tex` (merge-preambles conflicts) and need to be resolved upstream.

4. **Print a concise report** (≤ 12 lines). Example shape:

   ```
   ## extract-paper: arXiv-2302.05882v1 → papers/2302/
   - Main tex: arxiv.tex  (layout: modular)
   - Files: abstract.tex, main.tex, 5 sections, 6 appendices, 10 figures, 0 assets
   - Citations: 108 rewritten, 0 already-global, 0 unknown
   - Macros rewritten: \diag (1)
   - Vendored: (none)
   - Compile: ok (build/2302/2302.pdf)
   ```

   On failure, replace the last line with `- Compile: FAILED (returncode=...)` and the first hard error from the log.

## Constraints

- **Never modify anything under `arxiv-papers/`** — neither paper sources nor sidecar bibs/maps.
- **Never edit `papers-bibliography.bib`, `papers-dependencies.tex`, or `papers-macros.tex`** — those are owned by `merge-bibs` / `merge-preambles`. If the verification compile surfaces conflicts (e.g. clashing packages or doubly-defined macros), report them and ask the user to re-run the appropriate merge skill.
- If `papers/YYMM/` already exists and `--force` was not passed, surface the error and ask.
- If `uv` or `latexmk` is unavailable, report and stop.
- `papers/YYMM/` is tracked in git; `build/` is gitignored (the script appends it on first run if missing).
- The standalone driver loads `cleveref` itself (after `\input{papers-dependencies.tex}`) because `cleveref` must load after `hyperref`; `papers-dependencies.tex` is set up with `% [skip:package] cleveref` so it doesn't auto-load.
