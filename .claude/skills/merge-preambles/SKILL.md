---
name: merge-preambles
description: Run scripts/merge_preambles.py (the per-paper preamble merger) to refresh papers-dependencies.tex and papers-macros.tex from the per-paper sidecars, then inspect the merged files for residual problems — macros with different names but the same body (alias candidates), suspect \usepackage ordering, vendored .sty/.cls shadowing risks, and macros that were skipped from the preamble but are still referenced in paper bodies. Default mode just reports what's pending; --no-stop auto-resolves conflicts; full interactive runs require a real terminal because the script reads stdin. Use when the user asks to "merge preambles", refresh the thesis-wide packages/macros, audit pending preamble conflicts, or auto-resolve outstanding macro/package collisions.
---

# merge-preambles

Wrap `scripts/merge_preambles.py`, which folds the eight per-paper `arxiv-papers/arXiv-YYMM-{packages,macros}.tex` sidecars (produced by `parse-paper`) into thesis-wide `papers-dependencies.tex` and `papers-macros.tex`. **The merged files ARE the editorial record** — there is no separate state JSON. On re-run the script reads the existing output files: every name with an entry there is treated as already-decided and never re-prompted, even if a new variant later appears in the per-paper sidecars. To exclude a name from the merged output, the prompt's `[s]` choice writes a `% [skip:<category>] <name>` comment line in the appropriate output file; that line is honoured on every subsequent run. To re-prompt for a name, delete its entry (or skip marker) from the merged file before re-running.

The skill also performs a **post-merge inspection** of the generated files — this is the part the script can't do, and it's the most useful output for thesis-merging work.

## Modes

Pick the mode that matches the user's intent. If ambiguous, default to `dry-run` and ask.

### `dry-run` — read-only conflict preview (safe default)

```
uv run scripts/merge_preambles.py --dry-run
```

Reports the count of **still-undecided** conflicts (any name already present in `papers-dependencies.tex` / `papers-macros.tex` is treated as decided) and lists each pending one with its distinct variants. Keep the relayed summary tight: lead with the totals (`N pending; K kept from existing files; M auto-identical`), then surface the 3-5 most contentious names (highest variant count). Don't dump all conflict blocks back to the user.

If the script reports `0 pending`, say so and continue straight to the post-merge inspection — it's still worth doing because the inspection covers cross-name problems the script doesn't.

### `interactive` — editorial run, driven by the user

**The user must run this themselves** — the script reads from stdin and Claude can't drive interactive prompts. Tell them to type, at the chat prompt:

```
! uv run scripts/merge_preambles.py
```

Each prompt expects one of: a digit (pick variant N), `c` (write a custom replacement, terminated by a line containing only `END`), `s` (skip — don't emit this name; recorded as a `% [skip:<category>] <name>` line in the relevant output file; useful for review-comment macros like `\bl`/`\ls`/`\la`), or `q` (quit). The merged `.tex` files are rewritten after every prompted answer, so `q` mid-run is safe — re-running picks up from the next still-undecided name.

When the script exits cleanly, run the post-merge inspection (next section).

### `no-stop` — batch auto-resolve

```
uv run scripts/merge_preambles.py --no-stop
```

For every **still-undecided** conflict (anything already in the merged files is left alone), picks the variant used by the **most papers** (tiebreaker: longest verbatim). Logs each pick as `[auto:popular]`. Appropriate for:

- Regenerating the merged files on a fresh clone where the existing `.tex` files already encode prior editorial calls (idempotent — no new prompts fire, existing entries stay put).
- A first-pass overview when the user wants to see what the heuristic would produce before doing a real interactive editorial run.

**Not** appropriate as a substitute for editorial judgement on new conflicts — `--no-stop` will silently pick winners for them. Confirm with the user before invoking it when there are pending conflicts the user hasn't reviewed.

After it finishes, always continue to the post-merge inspection.

## Post-merge inspection

Run after every mode (including a clean `dry-run` when nothing is pending). The script picks winners *per name*, but the problems below span names and only a careful read of the merged output catches them. Keep the relayed report to ≤ 15 lines unless the alias list is unusually long.

1. **Same-body alias detection.** Read `papers-macros.tex`, extract each definition's body, normalise whitespace, group by body. Flag any group with **>1 distinct name** as a candidate alias pair. Real examples already known in this repo: `\ReLU` vs `\Relu`, `\Diag` (operator "diag") vs `\diag`. Report them with their winning verbatims so the user can decide whether to unify by editing per-paper sidecars or by adding a custom replacement that aliases one to the other.

2. **Suspect package ordering.** Scan `papers-dependencies.tex` top-to-bottom and apply known LaTeX ordering rules:
   - `inputenc` / `fontenc` should be near the top
   - `hyperref` should be last (or near-last); load order matters for its compatibility patches
   - `xcolor` should appear before any package that depends on colour
   - `amsthm` should precede theorem-styling packages
   Surface mis-orderings as `% suggest moving X after Y`; don't claim a build is broken unless certain.

3. **Vendored `.sty` / `.cls` callouts.** Grep the per-paper sidecars' header comments (`arxiv-papers/arXiv-YYMM-packages.tex` headers list any vendored files in the paper directory). Report each vendored file once, named alongside the paper(s) carrying it, with a warning that it may shadow the CTAN package of the same name at thesis-build time. Known cases: `algorithmic.sty` and `fancyhdr.sty` in arXiv-2402, `neurips_2024.sty` in arXiv-2405, `ceurart.cls` in arXiv-2602.

4. **Skipped-but-still-referenced names.** Read `papers-merge-decisions.json` for entries with `resolution: "skip"`. For each skipped name, grep the unpacked paper bodies under `arxiv-papers/arXiv-*v*/` for `\<name>` usage. Any matches mean the name is dropped from the preamble but still cited in prose — a future compile error. Report the matching files. (Expected case: `\bl{...}`, `\ls{...}`, `\la{...}` review-comment macros — they're routinely skipped from the preamble but heavily used in the paper bodies; the user has to strip those usages from prose too.)

## What the script writes

- `papers-dependencies.tex` at the repo root — **tracked in git**. Holds `\PassOptionsToPackage` and `\usepackage` lines plus their provenance comments and any `% [skip:passopts] …` / `% [skip:package] …` markers.
- `papers-macros.tex` at the repo root — **tracked in git**. Holds macro/environment/theorem definitions plus any `% [skip:macro] …` markers.

Both files are the **single source of truth for editorial state.** They're regenerable from the per-paper sidecars in the sense that re-running `--no-stop` from a freshly-deleted state would produce something coherent — but the user's interactive picks and custom replacements live here too, so deleting them loses those decisions. Once decided, a name is decided forever; the script never re-prompts for it on a re-run, even if a new variant later appears in a per-paper sidecar (the script logs `[stale]` to stderr in that case but leaves the existing entry alone).

To change a decision: edit the relevant entry by hand, or delete it from the merged file and re-run the script — deletion brings the name back into the prompt queue.

## Constraints

- Never invoke the interactive mode yourself — the user makes the editorial calls.
- Read-only with respect to `arxiv-papers/` (sidecars and paper folders). See [[feedback-arxiv-papers-readonly]].
- If the user asks why a particular entry has its current form, the provenance comment (`% picked from arXiv-XXXX — conflicts: …` or `% custom replacement; overrides variants from …`) records what the script saw at decision time. For deeper forensics, grep the relevant macro/package name in `arxiv-papers/arXiv-*-{packages,macros}.tex` to see every paper's variant.
