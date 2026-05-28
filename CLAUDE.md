# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project goal

A LaTeX PhD thesis (Luca Arnaboldi) that weaves a subset of his published arXiv papers into a single organic manuscript. The selection of papers, the chapter ordering, and the high-level narrative are editorial calls — defer to Luca on those.

## Two layers

The repo has two distinct layers, with mostly different files:

- **Editorial pipeline.** Turns each arXiv submission into thesis-ready content. Lives under `arxiv-papers/` (per-paper sources + sidecars), `scripts/` (Python helpers), and the three merged outputs at root (`papers-bibliography.bib`, `papers-dependencies.tex`, `papers-macros.tex`). **All of it is driven by the project skills** — `parse-paper`, `merge-bibs`, `merge-preambles`, `extract-paper`, `check-paper`. Don't invoke the scripts directly.
- **Thesis assembly.** Combines the extracted papers into one book. Lives in `thesis.tex`, `thesis-style.sty`, `chapters/`, `front-matter/`, and `latexmkrc`. Hand-edited LaTeX; no skill owns this layer.

## Top-level layout

| Path | What it is |
| --- | --- |
| `arxiv-papers/` | Per-paper arXiv sources + editorial sidecars (see next section). |
| `scripts/` | Python helpers — **don't invoke directly**, use the skills. |
| `papers-bibliography.bib` | Thesis-wide merged bib (regenerable via `merge-bibs`). |
| `papers-dependencies.tex`, `papers-macros.tex` | Thesis-wide merged preamble (regenerable via `merge-preambles`). |
| `papers/YYMM/` | Per-paper thesis-ready content (`abstract.tex`, `main.tex`, `sections/`, `appendices/`, `figs/`), produced by `extract-paper`. Tracked; hand-editable after extraction. |
| `papers/stand-alone-paper.tex` | Shared `\jobname`-dispatched driver to compile any single paper. Auto-generated; do not edit by hand. |
| `thesis.tex` | Root of the full thesis build (`\documentclass{book}`). |
| `thesis-style.sty` | Thesis-wide style package; defines `\paperchapter`. |
| `chapters/` | `introduction.tex`, `background.tex`, plus per-paper `paper-YYMM.tex` and `appendix-YYMM.tex` wrappers. |
| `front-matter/` | Three abstracts (en/it/fr), acknowledgements, foreword, cover/cv placeholders. |
| `latexmkrc` | Build config: `lualatex` + `biber`, `out_dir=build/`. |
| `.github/workflows/build.yml` | CI — incremental per-target build on main + tag-triggered all-targets Pages deploy. |
| `build/` | latexmk aux/PDF output. Gitignored. |
| `pyproject.toml`, `uv.lock` | uv-managed environment (`uv.lock` is currently gitignored). |

## `arxiv-papers/` sidecar conventions

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

Two behavioral rules apply to everything under `arxiv-papers/`:

- **Tarballs and unpacked dirs are strictly read-only.** Edit copies under `papers/YYMM/`, never the originals. After running `latexmk` inside any unpacked paper directory, clean up with `latexmk -C` so no aux files are left behind.
- **Don't assume internal structure of an unpacked paper.** Layouts differ across submissions (single-file vs `sections/` subdir, `.bbl` filename varies, macros may be inlined vs in `macros.tex`, vendored `.sty`/`.cls` may or may not be present). Always `ls` the specific paper's directory before working with it.

## Skills (editorial pipeline — now in maintenance mode)

All eight papers under `arxiv-papers/` have been extracted into `papers/YYMM/` and Luca is now hand-editing both that tree and the three merged outputs at root. **Do not re-run any of the editorial skills proactively:**

- `merge-bibs` and `merge-preambles` regenerate the three merged outputs from per-paper sidecars. Hand-edits to those merged files get wiped (e.g. the 2506 bilingual macros currently in `papers-macros.tex`, which aren't derivable from any sidecar). Their output is also expected to be stable now — a non-empty diff on a re-run means a sidecar shifted that needs investigation, not silent acceptance.
- `extract-paper` overwrites `papers/YYMM/` with `--force` and would clobber every hand-tuning of the extracted bodies (and break the slug lists hard-coded in `chapters/appendix-YYMM.tex`). Treat it as destructive.

If you have a real reason to re-run one — sidecar deliberately updated, new paper added, a definite bug requires the regen — surface the proposal to Luca first (what would change, what's at risk) and wait for explicit confirmation. `parse-paper` and `check-paper` are safe without this caveat: `parse-paper` only writes per-paper sidecars, and `check-paper` operates on `arxiv-papers/` and restores its working copy.

For flags, inputs, outputs, and exact behavior of each skill, **read the skill** — don't restate it here.

- `parse-paper` — extract per-paper packages + macros sidecars from an unpacked arXiv submission; prompts you to hand-curate `metadata.json`.
- `merge-bibs` — assemble `papers-bibliography.bib` from the per-paper `arXiv-YYMM.bib` files, resolving duplicates via the citation-map sidecars.
- `merge-preambles` — assemble `papers-dependencies.tex` + `papers-macros.tex` from the per-paper packages/macros sidecars, resolving conflicts via the macro-map sidecars.
- `extract-paper` — convert one paper into `papers/YYMM/`. Rewrites cite keys, macro names, label/ref keys (`\label{eq:foo}` → `\label{YYMM:eq:foo}` etc.), and figure paths; runs a body-cleanup pass (spacing/list-layout/math-kerning/float-placement strips, `wrapfigure` → `figure`, etc.); applies the optional `patches.json`; then runs a verification compile via the standalone driver.
- `check-paper` — compile a paper from `arxiv-papers/` as-is and report LaTeX errors/warnings plus bibliography coverage; restores the folder afterwards.

## Thesis assembly

- `thesis.tex` is `\documentclass[11pt,a4paper,openright]{book}` with multi-language babel (`main=english,italian,french`). `\usepackage{thesis-style}` **must precede** `\input{papers-dependencies.tex}` so its `\PassOptionsToPackage` calls take effect before the merged preamble loads `hyperref` / `placeins`.
- `cleveref` is loaded directly from `papers-dependencies.tex`, positioned immediately after `hyperref` (LaTeX requires the order). The line sits out-of-alphabetical-order on purpose; `merge_preambles.py` honours that placement via its `PACKAGE_ORDER_OVERRIDES` table, so a future re-run keeps it next to `hyperref` rather than alphabetising it back ahead.
- `\paperchapter{title}{authors}{abstract-path}` (defined in `thesis-style.sty`) is the entry point for each paper chapter. Each `chapters/paper-YYMM.tex` calls it and then `\subimport`s the paper's `main.tex` wrapped in `{\let\appendix\endinput ...}`, so the paper-internal `\appendix` doesn't switch the whole book into appendix mode mid-chapter.
- Per-paper appendices are deferred to `chapters/appendix-YYMM.tex`, which `\subimport`s each appendix slug explicitly. **If `extract-paper` is re-run and the slug set in `papers/YYMM/main.tex` changes, update the corresponding `chapters/appendix-YYMM.tex` list to match.**
- Babel reserves `\og` and `\no` for French; both are `\let ... \relax`'d at the top of `thesis.tex` so `papers-macros.tex` can rebind them.

## Builds

- Per-paper standalone: `latexmk -jobname=YYMM -outdir=build/YYMM papers/stand-alone-paper.tex`. (Also invoked by `extract-paper`'s verification step.)
- Full thesis: `latexmk thesis.tex`.
- Do **not** pass `-pdf`. It forces pdflatex, which overrides the `latexmkrc` and fails immediately because `thesis-style.sty` requires `fontspec` (lualatex-only). The `latexmkrc` already selects `lualatex`, `biber`, and `out_dir=build/`; let it. Use `-pdflua` if you want to be explicit.
- Prefer `latexmk` over invoking `lualatex` / `biber` by hand — it handles the multi-pass dance.

## Working with the user

- Luca is the author. When in doubt about scope, structure, or which papers to include, ask — these are editorial decisions, not implementation details.
- Prefer small, reversible LaTeX changes (one section at a time) over large restructurings; thesis prose is hand-tuned and easy to clobber.
