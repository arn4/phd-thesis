---
name: parse-paper
description: Extract package loads and macro definitions from one paper under arxiv-papers/ into arXiv-YYMM-packages.tex and arXiv-YYMM-macros.tex (via scripts/paper_parsing.py), hand-curate arXiv-YYMM-metadata.json with the plain-text title and Surname-Name authors, then print a short summary of what was extracted plus likely cross-paper collision points. Use when the user asks to "parse paper", "extract macros from", or "/parse-paper 2302".
---

# parse-paper

Run `scripts/paper_parsing.py` against one paper under `arxiv-papers/`, hand-curate a plain-text metadata JSON for it, and produce a concise report that flags likely collisions against papers already parsed.

## Inputs

A single paper identifier. Accept any of:

- Full folder name: `arXiv-2302.05882v1`
- YYMM prefix with or without `arXiv-`: `arXiv-2302`, `2302`
- arXiv id with or without version: `2302.05882`, `2302.05882v1`

The script resolves the identifier to the unique folder under `arxiv-papers/`. If zero or multiple folders match, it exits non-zero — surface that error and stop.

## Procedure

Treat `arxiv-papers/arXiv-<id>/` as **strictly read-only**. The script writes only at the top of `arxiv-papers/`; never modify anything inside the paper folder.

1. **Run the script** from the repo root:

   ```
   uv run scripts/paper_parsing.py <id>
   ```

   It writes (or overwrites) `arxiv-papers/arXiv-YYMM-packages.tex` and `arxiv-papers/arXiv-YYMM-macros.tex`, and prints a stderr summary that includes counts, the sources it parsed, vendored `.sty`/`.cls` files, intra-paper duplicate macro names, and a **cross-paper collision sweep** against every other already-written `arXiv-*-macros.tex` sidecar. Capture this output for the final report.

   The cross-paper sweep is done in Python inside the script (not in the shell) so backslash-laden names like `\Cov` and `\DeclareMathOperator` round-trip reliably. Boilerplate `\newtheorem` env names (theorem, lemma, …) get collapsed into one line.

   If the script exits non-zero, surface its stderr verbatim and stop. Do not retry with guesses.

2. **Hand-curate `arxiv-papers/arXiv-YYMM-metadata.json`** if it doesn't already exist (or if the user asks to refresh it). This step is intentionally **not** automated — LaTeX `\title{...}` and `\author{...}` are too irregular across papers (math, `\and`, `\affiliation`, footnotes, accents) for a regex to handle cleanly.

   Open the main `.tex` (the script's stderr lists it as `main: <name>.tex`). Find `\title{...}` and `\author{...}` (some classes use per-author `\author{...}` blocks). Write a JSON file with exactly this shape:

   ```json
   {
     "title": "A unifying approach to SGD",
     "authors": ["Arnaboldi Luca", "Stephan Ludovic", "Krzakala Florent", "Loureiro Bruno"]
   }
   ```

   Rules:
   - `title`: plain text. Strip surrounding braces and any trailing period. Expand `\textsc{...}` etc. to the inner text. Math should be rendered as the user prefers — if the title contains math or macros that don't have an obvious plain-text rendering, **ask the user** rather than guessing.
   - `authors`: list of strings in `"Surname Name"` order (surname first, single space, then given name(s)/initials). Strip `\thanks{...}`, `\affiliation{...}`, footnote markers (`\textsuperscript{...}`, `\affmark[...]`, etc.), ORCID, email addresses, and `\and` separators. Convert LaTeX accents (e.g. `\'e` → `é`, `\"u` → `ü`) to their Unicode forms.
   - If the source author order is ambiguous (e.g. only initials, multiple affiliations interleaved), ask before guessing.

3. **Optional package-option sweep.** The script's collision report covers macros only. If `natbib`/`hyperref`/`geometry` look suspect, eyeball the relevant `arxiv-papers/arXiv-*-packages.tex` files by hand — they're a handful of lines each and cross-paper option mismatches are usually obvious. Also mention any vendored `.sty`/`.cls` listed in the new packages.tex header that may shadow CTAN at thesis-build time.

4. **Print a concise report** (≤ 12 lines). Example shape:

   ```
   ## parse-paper: arXiv-2302.05882v1

   - Main tex: arxiv.tex (+ macros.tex)
   - Packages: 22 (1 \PassOptionsToPackage, 21 \usepackage)
   - Macros:   46 (5 operators, 31 commands, 10 theorems)
   - Vendored .sty/.cls: (none)
   - Intra-paper duplicates: (none)
   - Metadata: "A unifying approach to SGD" — Arnaboldi Luca + 3 others
   - Cross-paper collisions vs. already-parsed:
     - \Cov also in arXiv-2402 (DeclareMathOperator)
     - \vec also in arXiv-2305, arXiv-2402, arXiv-2405 (all different bodies)
     - natbib loaded with [numbers,sort&compress] here but no options in arXiv-2402
   ```

   If no other papers have been parsed yet, the cross-paper section says `(no prior outputs to compare)`.

## Constraints

- **Never modify anything under `arxiv-papers/arXiv-<id>/`.** The script enforces this for itself; the metadata curation step also only writes at the top of `arxiv-papers/`.
- If `uv` is unavailable, report and stop.
- The output `.tex` files are tracked in git — re-running the script may produce a diff if the source preamble changed; that's expected, leave the diff for the user to review.
- If the script reports a missing `\input{...}` file (`[warn]` line) for a paper, mention it in the report — it usually means that file's preamble contribution is unaccounted for.
