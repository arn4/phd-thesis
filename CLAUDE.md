# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

This repository will become Luca Arnaboldi's PhD thesis: a single organic LaTeX document that integrates (a subset of) his published arXiv papers into a coherent manuscript. The selection of papers and the chapter structure are not yet finalised.

## Current state

At the repo root:

- `arxiv-papers/` — per-paper sources + editorial sidecars (see below).
- `scripts/` — Python helpers. **Don't invoke them directly** — use the project skills (`merge-bibs`, `parse-paper`, `merge-preambles`, `check-paper`, `extract-paper`); they run the script with the right flags and produce concise reports.
- `papers-bibliography.bib`, `papers-dependencies.tex`, `papers-macros.tex` — thesis-wide merged outputs, regenerable via the merger skills from the per-paper sidecars + the editorial state recorded inline (provenance + `% [skip:...]` markers) and in the `arxiv-papers/arXiv-YYMM-*-map.json` files.
- `papers/YYMM/` — per-paper thesis-ready content (abstract.tex, main.tex, sections/, appendices/, figs/), produced by `extract-paper`. Tracked in git; hand-editable after extraction.
- `papers/stand-alone-paper.tex` — shared `\jobname`-dispatched driver that compiles any extracted paper individually for review (auto-generated; do not edit by hand).
- `build/` — latexmk aux/PDF output from standalone compiles. Gitignored.
- `pyproject.toml` / `uv.lock` — uv-managed environment.

No thesis LaTeX source or build config (`latexmkrc`, document class) exists yet. Do not assume conventional directories (`chapters/`, `src/`, `bib/`) exist until checked.

## `arxiv-papers/` layout

Each included paper has up to eight artefacts, all keyed by **YYMM prefix** (so paper `arXiv-2302.05882v1` shares `arXiv-2302.bib`, `arXiv-2302-macros.tex`, etc.):

| Artefact | Tracked? | Owner / source |
| --- | --- | --- |
| `arXiv-<id>.tar.gz` | gitignored | pristine arXiv tarball; re-fetch via `scripts/fetch_arxiv_sources.py` |
| `arXiv-<id>/` | gitignored | unpacked submission (read-only; must stay byte-identical to the tarball) |
| `arXiv-YYMM.bib` | tracked | hand-managed by Luca; **do not edit** |
| `arXiv-YYMM-citation-map.json` | tracked | bib merge state, managed by `merge-bibs` |
| `arXiv-YYMM-packages.tex` | tracked | extracted package loads, managed by `parse-paper` |
| `arXiv-YYMM-macros.tex` | tracked | extracted macro / environment / theorem defs, managed by `parse-paper` |
| `arXiv-YYMM-metadata.json` | tracked | plain-text title + `Surname Name` author list — **hand-curated** via `parse-paper`, not script-written |
| `arXiv-YYMM-macro-map.json` | tracked | local-macro → global-name mapping (for prose-folding later), managed by `merge-preambles` |

A sidecar `.bib` is typically a superset of what its paper actually cites — extra entries are fine.

**Do not assume internal structure of an unpacked paper.** Layouts differ: single-file vs `sections/` subdir, `.bbl` filename varies (`arxiv.bbl`, `main.bbl`, `assembler.bbl`, …), macros may live in `macros.tex` or be inlined, vendored `.sty` / `.cls` files may or may not be present. Always `ls` the specific paper's directory before working with it.

**Tarballs and unpacked dirs are read-only.** After running `latexmk` inside any unpacked paper directory, clean up with `latexmk -C` so no aux files are left behind. Edit copies in the thesis tree, never the originals.

## Build toolchain (planned)

- **LaTeX:** `latexmk` driving `pdflatex`.
- **Bibliography:** `biblatex` with the `biber` backend. `natbib` is intentionally skipped in `papers-dependencies.tex` (`% [skip:package] natbib` / `% [skip:passopts] natbib`).
- **Document class:** not yet decided; keep the class choice swappable.
- **Python:** managed with `uv`. Invoke helpers via the skills.

Once a `latexmkrc` exists, prefer `latexmk` over invoking `pdflatex` / `biber` by hand — it handles the multi-pass dance.

## Per-paper standalone builds

`extract-paper` converts each unpacked paper into `papers/YYMM/`, then `papers/stand-alone-paper.tex` lets you compile any one of them from the repo root:

```
latexmk -pdf -jobname=2302 -outdir=build/2302 papers/stand-alone-paper.tex
```

The driver loads the thesis-wide preamble (`papers-dependencies.tex` + `papers-macros.tex`) and bib (`papers-bibliography.bib`), picks the paper via `\jobname`, and uses biblatex (`authoryear-comp` style) with biber. `cleveref` is loaded by the driver itself rather than by `papers-dependencies.tex`, because it must load after `hyperref`.

## Working with the user

- Luca is the author. When in doubt about scope, structure, or which papers to include, ask — these are editorial decisions, not implementation details.
- Prefer small, reversible LaTeX changes (one section at a time) over large restructurings; thesis prose is hand-tuned and easy to clobber.
