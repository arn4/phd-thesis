---
name: check-paper
description: Compile a paper from arxiv-papers/, report LaTeX errors/warnings, run the bibliography-coverage check, restore the paper folder to its original contents, and print a concise report. Use when the user asks to "check paper", validate a paper builds, or audit a paper by its arXiv ID (e.g. "check arXiv-2302", "/check-paper 2305").
---

# check-paper

Compile one paper under `arxiv-papers/` and produce a single short report covering build health and bibliography coverage.

## Inputs

A single paper identifier. Accept any of:

- Full folder name: `arXiv-2302.05882v1`
- YYMM prefix with or without the `arXiv-` prefix: `arXiv-2302`, `2302`
- An arXiv id: `2302.05882`, `2302.05882v1`

Resolve to the unique folder under `arxiv-papers/` whose name starts with `arXiv-` and matches the user's input. If no folder matches, stop and report. If more than one matches (multiple versions of the same paper), ask which one.

## Procedure

Treat `arxiv-papers/` as **strictly read-only**. The folder must end byte-identical to how it started.

1. **Resolve the paper folder.** `ls arxiv-papers/` and pick the matching folder.

2. **Snapshot the folder.** Capture `ls -1 <folder>` (and any nested aux files of interest) so step 7 can restore it.

3. **Find the main `.tex`.** It varies per paper. Heuristic: look at the folder's top-level `.tex` files. Prefer the one whose stem matches an existing `.bbl` (e.g. `arxiv.bbl` ⇒ `arxiv.tex`). If only one top-level `.tex` exists, use it. Otherwise grep for `\documentclass` to find the root. If still ambiguous, ask.

4. **Compile.** From inside the paper folder:

   ```
   latexmk -pdf -interaction=nonstopmode <main.tex>
   ```

   Capture the exit code and tail of stdout. Note the path/size/page-count of the produced `.pdf` if any.

5. **Inspect `<stem>.log`.** Search for:
   - Hard errors: lines starting with `! `, `LaTeX Error`, `Emergency stop`, `Fatal error`, `Undefined control sequence`, `Runaway argument`.
   - Notable warnings: `LaTeX Warning: Reference ... undefined`, `Citation ... undefined`, `Package hyperref Warning`, `pdfTeX warning ... destination with the same identifier ... duplicate ignored`, `Overfull`/`Underfull` (count only).
   - Bibliography issues: missing `.bib` warnings from latexmk, biber/bibtex error blocks.

   Quote a couple of representative warning lines if there are interesting ones; otherwise just give counts by category.

6. **Run the bibliography-coverage check.** From the repo root:

   ```
   uv run scripts/check_bib_coverage.py
   ```

   Extract the section delimited by `=== <folder-name> ===` for the targeted paper and use that for the report.

7. **Restore the folder.** From inside the paper folder run `latexmk -C <main.tex>`, then `ls` the folder. If anything that wasn't in the step-2 snapshot still remains (e.g. a freshly-generated `.bbl` for papers that didn't ship one), delete it explicitly. Do **not** leave any generated file behind. See feedback memory [[feedback-arxiv-papers-readonly]].

8. **Report.** Output a concise summary, e.g.:

   ```
   ## check-paper: arXiv-2302.05882v1

   - Main tex: arxiv.tex
   - Build: ✅ success (23 pages, 1.1 MB) / ❌ failed (see errors)
   - Errors: 0
   - Warnings: 16 (15 hyperref PDF-string, 1 font-size substitution); 3 duplicate hyperref destinations in appendix C
   - Bibliography: 39 cited / 89 in arXiv-2302.bib — MISSING: `veiga2022phase`, `benarous2022`
   - Folder restored: ✅

   Overall: builds cleanly, two cite keys need to be added to the sidecar bib.
   ```

   Keep it under ~12 lines unless errors require quoting specific log excerpts.

## Constraints

- Never edit anything under `arxiv-papers/` — neither paper sources nor sidecar `.bib` files. The skill is read-only with respect to that tree.
- If `latexmk` or `uv` is unavailable, report and stop.
- If the paper id resolves to a folder with no `.tex` at top level, do not recurse into subdirectories blindly — report and ask.
