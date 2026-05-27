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

Each included paper has up to nine artefacts, all keyed by **YYMM prefix** (so paper `arXiv-2302.05882v1` shares `arXiv-2302.bib`, `arXiv-2302-macros.tex`, etc.):

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
| `arXiv-YYMM-patches.json` | tracked, optional | hand-authored `find_replace` substitutions applied as the last step of `extract-paper`; for upstream-source defects that can't be repaired in any other sidecar |

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

In addition to the bib/macro/figure-path rewrites driven by the sidecar maps, `extract-paper` applies a body-cleanup pass to every unit/asset/abstract before writing:

- Every `\label{X}` and `\ref|cref|Cref|eqref|autoref|pageref|nameref|vref|Vref|cpageref|Cpageref|hyperref` key is prefixed with `YYMM:`, so the same label name across papers is unambiguous when the thesis is later assembled (`\label{eq:foo}` in paper 2302 becomes `\label{2302:eq:foo}`, and every reference to it is rewritten in lock-step).
- Spacing and page-layout commands are stripped: `\vspace`/`\hspace`/`\addvspace{...}`, `\bigskip`/`\medskip`/`\smallskip`, `\hfill`/`\vfill`/`\hfil`/`\vfil`, `\noindent`/`\indent`, `\newpage`/`\clearpage`/`\cleardoublepage`, `\linebreak`/`\pagebreak`/`\nolinebreak`/`\nopagebreak`, `\enlargethispage{...}`, `\samepage`/`\sloppy`/`\fussy`, and the optional `[X]` of `\\[X]`. Math-layout commands (`\phantom`/`\smash`/`\strut`/`\centering`) are kept.
- List-layout config is stripped: the optional enumitem arg of `\begin{itemize|enumerate|description}[...]` (e.g. `[noitemsep,leftmargin=1em,wide=0pt]`), bare `\setlist[*]?{...}` calls, and `\setlength{\itemsep|\parsep|\topsep|\partopsep|\listparindent|\labelwidth|\labelsep|\leftmargin|\rightmargin}{...}` calls.
- Math-mode kerning macros are stripped: `\,`, `\;`, `\:`, `\!`, `\>`, plus the named variants `\thinspace`/`\medspace`/`\thickspace`/`\enspace`/`\hairspace` and `\negthinspace`/`\negmedspace`/`\negthickspace`. Semantic math spacing `\quad`/`\qquad`, the line-break `\\`, the non-breaking space `~`, and the escaped space `\ ` are kept.
- Float placement specifiers are stripped: `\begin{figure}[ht!]` → `\begin{figure}` (same for `figure*`/`table`/`table*`/`longtable`/`sidewaystable`/`sidewaysfigure`/`algorithm`/`algorithm2e`).
- `wrapfigure` is converted to `figure`: `\begin{wrapfigure}[N]{r}{w}` → `\begin{figure}`, `\end{wrapfigure}` → `\end{figure}`.
- In-body `\newcommand`/`\renewcommand`/`\providecommand`/`\DeclareMathOperator` whose target name is already defined in `papers-macros.tex` are dropped (auto-fixes paper-body redefs that collide with the merged global macros).
- Finally, the optional `arxiv-papers/arXiv-YYMM-patches.json` sidecar (`{"find_replace": [{"find": "...", "replace": "..."}, ...]}`) is applied as a last-step literal substitution, for upstream defects that can't be expressed in any other sidecar.

## Working with the user

- Luca is the author. When in doubt about scope, structure, or which papers to include, ask — these are editorial decisions, not implementation details.
- Prefer small, reversible LaTeX changes (one section at a time) over large restructurings; thesis prose is hand-tuned and easy to clobber.
