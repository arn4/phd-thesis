---
name: deep-review
description: Run a multi-agent, read-only "deep review" of the whole thesis for defence-readiness — typography, grammar, correctness, soundness, notation, bibliography, visual/figures, front-matter, and a hostile-examiner pass — and assemble the results into the git-ignored claude-review/ folder (findings + an interactive local report.html). Produces a fresh, versioned review (v1, v2, …) whose identity lives in claude-review/meta.json; resolve individual findings afterward with /solve-issue. Use when the user asks to deeply review, audit, or defence-check the thesis, or runs /deep-review. This is expensive (many parallel agents) and long-running.
---

# deep-review

Audit the whole thesis with a squad of parallel **read-only** review agents and
land the results in the git-ignored `claude-review/` folder: raw per-agent
outputs, a merged findings set, `meta.json` (the review's identity), and an
interactive `report.html`. Each run produces a **new numbered version**; the
version/name live *inside* `meta.json`, so the folder name stays generic and
`/solve-issue` keeps working across versions.

Nothing in the thesis is edited — agents only read and write their own findings
JSON. The only writes are under `claude-review/` (plus `build/` from the compile,
which is git-ignored).

## What it produces

```
claude-review/
  meta.json              review identity: name, version, thesis title, counts,
                         curated "start here" corroborations
  findings/
    agents/<agent>.json  raw per-agent outputs (frozen record)
    merged.json          the working set — ids, source, `solved` (source of truth)
  report.html            interactive report — LOCAL ONLY, open it from disk
  report.md              markdown backup with [ ]/[x] checkboxes
  build_report.py        report generator (copied from this skill; also used by /solve-issue)
  README.md              how to use the folder
```

The report is **never published** — no hosted copy, no Artifact. One local file is
the single source of truth for viewing.

## Procedure

1. **Version & archive.** If `claude-review/meta.json` exists, the new version is
   its `version + 1`; move the existing `meta.json`, `findings/`, `report.*` into
   `claude-review/archive/v<old>/` first so the new review replaces it cleanly
   without losing history. Otherwise this is `v1`. Ensure `claude-review/` is in
   `.gitignore` (add `claude-review/` if missing).

2. **Refresh the build.** Compile so the typography/visual agents read a current
   PDF + log: `latexmk thesis.tex` (per `CLAUDE.md`: no `-pdf`; the `latexmkrc`
   selects lualatex+biber). Note the page count (`pdfinfo build/thesis.pdf`) and
   the log's Overfull/Underfull/Warning counts.

3. **Scope the review from the source — don't hardcode.** Read `thesis.tex` for
   the active `\include{chapters/paper-YYMM}` lines (the paper set can change
   between versions), the introduction's `\input` section files, and the
   front-matter includes. Read `\title` for the thesis title.

4. **Launch the agent squad.** Follow `agents.md` (in this skill dir): the shared
   finding schema, the priority-aware severity rubric, and one prompt per agent.
   Spawn them as **parallel background `Agent` calls**, each instructed to write
   its findings JSON to `claude-review/findings/agents/<agent-id>.json` and to
   touch nothing else. Weight depth to priorities: **Introduction perfect, paper
   main sections high, appendices lenient.** Wait for all to finish.

5. **Merge.** Run the assembler (from repo root):

   ```
   python3 .claude/skills/deep-review/merge_findings.py
   ```

   It writes `claude-review/findings/merged.json` (assigns `Fxxx` ids, tags
   `source`, adds `solved: false`) and prints per-severity counts plus
   **corroboration candidates** (locations flagged by >1 agent).

6. **Write `claude-review/meta.json`:** `name` = `"Deep Review of v<N>"`,
   `version` = N, `generated` = today, `thesis_title`, `pages`, `words` (approx),
   `agents` (count launched), and `corroborations` = a curated 3–5 "start here"
   list (each `{sev, t, who, q}`, where `q` is a search string) drawn from the
   merge's corroboration candidates and the sharpest cross-agent findings.

7. **Generate the report.** Copy this skill's generator in and run it:

   ```
   cp .claude/skills/deep-review/build_report.py claude-review/build_report.py
   python3 claude-review/build_report.py
   ```

   It writes `claude-review/report.html` (a full standalone document that opens
   straight from disk) and `report.md`. Drop a `README.md` into `claude-review/`
   (see the v1 copy for the template). **Do not publish the report** — it is local
   only.

8. **Verify it renders before claiming it works.** The report's findings list is
   built by JavaScript, so a valid-looking file can still render empty. Check that
   no element id is duplicated (the JSON blob ids `findings-data`/`corro-data` must
   not collide with any markup id — that exact bug once silently emptied the whole
   report), and ideally execute the page's script against a stubbed DOM with
   `/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc`
   to confirm the expected number of cards render. Parsing the JSON in Python is
   **not** sufficient — it does not exercise the page.

9. **Report** to the user: totals by severity, the Critical findings, and the
   curated corroborations. Tell them to open `claude-review/report.html`
   (`open claude-review/report.html`) and point them at `/solve-issue [CODE]
   [COMMENT]` to work through the findings.

## Constraints

- **Read-only on the thesis.** Agents must not edit any thesis file; the only
  writes are under `claude-review/` and the git-ignored `build/`. State this in
  every agent prompt.
- **Priority-aware severity** (see `agents.md`): the Introduction is held to a
  perfect standard, paper main sections to a high one, appendices leniently.
- **Don't re-run the editorial pipeline** (`extract-paper`, `merge-bibs`,
  `merge-preambles`) — see [[feedback-dont-rerun-editorial-scripts]]. Never edit
  `arxiv-papers/` — see [[feedback-arxiv-papers-readonly]].
- **Expensive & long-running** (a dozen-plus parallel agents, several on the
  strongest model). Confirm scope with the user if it's ambiguous which papers
  are in scope.
- **The report is local only.** Never publish it as an Artifact or upload it
  anywhere — `claude-review/report.html` is the single copy, by design (one
  reference is easier to keep in sync than two).
- The whole `claude-review/` tree is git-ignored working state, not part of the
  manuscript. Resolve findings with `/solve-issue`, not by hand-editing
  `merged.json` unless you want to.

## Files in this skill

- `agents.md` — the agent roster, shared finding schema, severity rubric, and the
  per-agent prompt templates. **Read it before launching.**
- `merge_findings.py` — assembles `merged.json` from the per-agent outputs.
- `build_report.py` — generates `report.html` + `report.md` from `meta.json` +
  `merged.json`; version-agnostic (also invoked by `/solve-issue`).
