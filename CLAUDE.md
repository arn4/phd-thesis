# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

This repository will become Luca Arnaboldi's PhD thesis: a single organic LaTeX document that integrates (a subset of) his published arXiv papers into a coherent manuscript to be presented to a thesis commission. The selection of papers to include and the chapter structure are not yet finalised.

## Current state

Present at the repo root:

- `arxiv-papers/` — source material (see below).
- `scripts/` — Python helper scripts (run with `uv run scripts/<name>.py`).
- `pyproject.toml` / `uv.lock` — uv-managed environment for those scripts.
- `CLAUDE.md` — this file.

No thesis LaTeX source or build config (`latexmkrc`, document class, merged thesis-wide `.bib`) exists yet. Do not assume conventional directories (`chapters/`, `src/`, `bib/`, etc.) exist until you've checked.

## Source material: `arxiv-papers/`

For each included arXiv paper there are three artifacts at the top of `arxiv-papers/`:

1. `arXiv-<id>.tar.gz` — the pristine arXiv tarball (e.g. `arXiv-2302.05882v1.tar.gz`).
2. `arXiv-<id>/` — the unpacked submission directory.
3. `arXiv-<YYMM>.bib` — a sidecar BibTeX file with the references cited by that paper, supplied by Luca (the originals were not bundled in the arXiv source). **The mapping is by YYMM prefix**, not the full ID: e.g. paper `arXiv-2302.05882v1/` ↔ bib `arXiv-2302.bib`. A sidecar `.bib` is typically a superset of what its paper actually cites (extra entries are fine).

**Do not assume internal structure of an unpacked paper.** The layouts differ — some are single-file, some use `sections/` / `figures/` subdirs, the `.bbl` filename varies (`arxiv.bbl`, `main.bbl`, `assembler.bbl`, …), macros may live in `macros.tex` or be inlined, vendored `.sty` files may or may not be present. Always `ls` the specific paper's directory before working with it; never generalise from one paper to the rest.

General expectations when assembling the thesis:

- **Macro / command collisions are likely** across papers. Plan to consolidate into a single thesis-wide macros file rather than `\input`-ing per-paper macros blindly.
- **Bibliographies must be merged and deduplicated** from the per-paper sidecars into a thesis-wide `.bib`. Use `scripts/check_bib_coverage.py` to verify that every cite key in each paper's `.bbl` is present in its sidecar `.bib` before merging.
- **Treat `arxiv-papers/` as read-only source.** Tarballs and unpacked directories must remain byte-identical to what arXiv shipped; the sidecar `.bib` files are managed by Luca, not Claude. Edit copies in the thesis tree, never the originals — re-deriving the unpacked source from the tarballs should always be possible. In particular, after running `latexmk` inside any unpacked paper directory, clean up with `latexmk -C` so no aux files are left behind.
- **Tarballs and unpacked papers are git-ignored** (only the sidecar `.bib` files are tracked). To re-create them on a fresh checkout, run `uv run scripts/fetch_arxiv_sources.py` — it pulls each version-pinned tarball from `https://arxiv.org/src/<id>v<n>` and extracts it.

## Build toolchain (planned)

- **LaTeX:** `latexmk` driving `pdflatex`.
- **Bibliography:** `biblatex` with the `biber` backend. Merged `.bib` lives in the thesis tree, not under `arxiv-papers/`.
- **Document class:** not yet decided (no confirmed university template). Keep the class choice swappable.
- **Python (helper scripts):** managed with `uv`. Run with `uv run scripts/<name>.py`; add deps with `uv add`. `pyproject.toml` and `uv.lock` live at the repo root.

Once a `latexmkrc` exists, prefer `latexmk` over invoking `pdflatex`/`biber` by hand — it handles the multi-pass dance.

## Working with the user

- Luca is the author; when in doubt about scope, structure, or which papers to include, ask rather than guessing — these are editorial decisions, not implementation details.
- Prefer small, reversible LaTeX changes (one section at a time) over large restructurings; thesis prose is hand-tuned and easy to clobber.
