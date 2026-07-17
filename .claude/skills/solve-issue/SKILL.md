---
name: solve-issue
description: Resolve one finding from the current Claude review (the git-ignored claude-review/ folder) by its CODE, using the finding's recorded info plus the user's free-text COMMENT. A dismissive comment ("it is not an issue", "false positive", "by design") marks the finding solved with NO thesis change; any other comment is guidance for the actual fix, applied to the located .tex file. The skill then flips `solved` in claude-review/findings/merged.json, records a resolution note, propagates to other unsolved findings the same change resolves, and regenerates the local report.html/report.md. Use when the user runs /solve-issue [CODE] [COMMENT], or asks to resolve, fix, or dismiss a specific review finding by its Fxxx id. Works for whatever review version is currently in claude-review/ (see meta.json).

---

# solve-issue

Close out a single review finding end to end. The findings live in the
git-ignored `claude-review/` working set produced by the `deep-review` skill
(`findings/merged.json` is the source of truth and drives the report;
`meta.json` holds the review's identity — name/version). This skill is
version-agnostic: it operates on whatever review is currently in
`claude-review/`. The report is **local only** — `claude-review/report.html` — and
is never published anywhere.

It combines two things: the **finding's own record** (its `location`, `quote`,
`suggested_fix`, `severity`, `confidence`) and the **user's COMMENT**, which
either *directs* the fix or *dismisses* the finding. The mechanical parts (JSON
edits, related-finding lookup, report regeneration) are delegated to
`.claude/skills/solve-issue/solve_issue.py`; the judgment (interpret the comment,
make the real `.tex` edit, decide what "related" means) stays here.

## Inputs

- `CODE` — the finding id, e.g. `F042` (case-insensitive; `f42` also works).
- `COMMENT` — free text. Two modes:
  - **Dismissal** — "it is not an issue", "not a problem", "false positive",
    "intended / by design", "wontfix", "leave as is", "correct as is", "ignore".
    → mark solved, **no thesis edit**.
  - **Fix guidance** — anything else (may be empty). Combine it with the
    finding's `suggested_fix` to make the concrete edit. Empty COMMENT ⇒ apply
    `suggested_fix` as recorded, using your judgment.

## Procedure

1. **Guard.** If `claude-review/findings/merged.json` does not exist, tell the
   user there's no current Claude review (run `deep-review` first) and stop.

2. **Read the finding** (from the repo root):

   ```
   python3 .claude/skills/solve-issue/solve_issue.py show CODE
   ```

   If it errors "not found", report that and stop. If the JSON shows
   `"solved": true`, tell the user it's already resolved and ask (AskUserQuestion)
   whether to redo it before proceeding.

3. **Classify the COMMENT** as *dismissal* or *fix guidance* (see Inputs).

4. **If dismissal:** make **no** change to any thesis file. Skip to step 7 with
   `--dismiss` and a note capturing the user's reason (e.g.
   `--dismiss --note "Not an issue: intended per author"`).

5. **If fix guidance — apply the edit to the thesis:**
   - Open the file named in `location` (the part before `:`). **Locate the text
     by the finding's `quote`, not the line number** — earlier fixes may have
     shifted lines. Read around the area and confirm the exact spot.
   - Make the **minimal** edit that satisfies `suggested_fix` as steered by the
     COMMENT. Honor `CLAUDE.md`: American English; cross-refs spelled out
     ("Equation~\eqref{}", never "eq."); never edit anything under
     `arxiv-papers/` (edit the `papers/YYMM/`, `chapters/`, or `front-matter/`
     copy the finding points at). See [[feedback-arxiv-papers-readonly]] and
     [[feedback-thesis-writing-style]].
   - **Caution on math:** for `Correctness`/`Soundness` findings with
     `confidence: Low` (and `Medium` when the COMMENT doesn't state the intended
     direction), confirm the exact change with the user (AskUserQuestion) before
     editing — a wrong "fix" to a displayed equation is worse than the finding.
   - Do not mark solved if you could not actually apply the edit; report the
     blocker instead.

6. **Find related unsolved findings:**

   ```
   python3 .claude/skills/solve-issue/solve_issue.py related CODE
   ```

   This lists unsolved findings sharing the location or overlapping the quote
   (e.g. the same defect flagged by two agents, or corroborating duplicates).
   **Judge** which are genuinely resolved by the *same* edit you just made — only
   those become `--also`. Mention any related-but-not-covered findings to the
   user so they aren't silently lost.

7. **Mark solved + regenerate the report:**

   ```
   python3 .claude/skills/solve-issue/solve_issue.py solve CODE \
     --note "<what you changed, or why dismissed>" \
     [--dismiss] [--also F0xx F0yy]
   ```

   This flips `solved`/records `resolution`+`solved_at` in
   `claude-review/findings/merged.json` for `CODE` and each `--also` id, then runs
   `claude-review/build_report.py` to rebuild the local `report.html` +
   `report.md`. Relay the printed `progress: N/total`. **Do not publish the report
   anywhere** — it is local only.

8. **Report** concisely: the thesis edit made (file + what changed) or that it
   was dismissed; the finding(s) now solved (`CODE` + any `--also`); any related
   findings left for separate handling; and the new `N/total` progress. Mention
   the user can reopen `claude-review/report.html` to see it refreshed.

## Constraints

- **One finding per invocation.** For a batch, run the skill once per CODE.
- **`merged.json` is authoritative.** The raw `findings/agents/*.json` snapshots
  are a frozen record and are not modified by this skill.
- **Never mark solved without doing the work:** either a real thesis edit landed,
  or the COMMENT was a genuine dismissal. No silent solves.
- **Never edit `arxiv-papers/`**; only the extracted/hand-editable copies. Do not
  re-run any editorial skill (`extract-paper`, `merge-*`) — see
  [[feedback-dont-rerun-editorial-scripts]].
- **Don't re-run the review agents.** This skill resolves one finding; use
  `deep-review` to (re)generate the whole review.
- Trust the helper's `related` list as *candidates*, not orders — you decide what
  `--also` actually covers.
- **The report is local only.** Never publish it as an Artifact or upload it
  anywhere — `claude-review/report.html` is the single copy, by design (one
  reference is easier to keep in sync than two).
- The whole `claude-review/` folder is git-ignored; edits there are local working
  state, not manuscript changes.
