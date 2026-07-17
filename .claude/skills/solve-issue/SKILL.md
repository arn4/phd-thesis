---
name: solve-issue
description: Resolve one finding from the current Claude review (the git-ignored claude-review/ folder) by its CODE, using the finding's recorded info plus the user's free-text COMMENT. A dismissive comment ("it is not an issue", "false positive", "by design") marks the finding solved with NO thesis change; any other comment is guidance for the actual fix, which is made in an isolated detached worktree, committed, and cherry-picked onto main — so several findings can be solved in parallel without sharing a working tree. Only once the commit has landed does it flip `solved` in claude-review/findings/merged.json, record a resolution note, propagate to other unsolved findings the same change resolves, and regenerate the local report.html/report.md. Use when the user runs /solve-issue [CODE] [COMMENT], or asks to resolve, fix, or dismiss a specific review finding by its Fxxx id. Works for whatever review version is currently in claude-review/ (see meta.json).

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
either *directs* the fix or *dismisses* the finding. The mechanical parts
(worktree setup, landing the commit, JSON edits, related-finding lookup, report
regeneration) are delegated to `.claude/skills/solve-issue/solve_issue.py`; the
judgment (interpret the comment, make the real `.tex` edit, decide what "related"
means) stays here.

## How the parallelism works

Each invocation fixes its finding in **its own detached worktree** under
`.claude/worktrees/solve-fNNN/`, commits there, and then cherry-picks that commit
onto `main`. No branch is ever created and history stays linear. Several
instances can therefore run at once — Luca fires off `/solve-issue` in several
sessions — without sharing a working tree.

`integrate` is the only critical section. It holds an exclusive lock while it
cherry-picks, updates `merged.json`, and rebuilds the report, in that order, so
the review folder is only ever touched *after* the fix has really landed, and two
instances can never interleave. **Never run `git cherry-pick`/`merge`/`stash` by
hand to land a fix** — that is exactly the race `integrate` exists to prevent.

The worktree exists for exactly one thing: the `.tex` edit and its commit.
`claude-review/` is the main checkout's business from start to finish — you read
the finding there before the worktree exists, and `integrate` writes it back
there only once the commit has landed on `main`. Nothing in the worktree ever
touches it, which is why the folder being gitignored (and so absent from the
worktree) costs nothing. Two rules follow:

- **Run every `solve_issue.py` command from the repo root.** The helper resolves
  the main checkout itself, so the review folder is always found regardless of
  where you invoke it from.
- **Edit the file under the worktree path, not the main checkout.** Editing
  `chapters/foo.tex` instead of `.claude/worktrees/solve-fNNN/chapters/foo.tex`
  silently defeats the whole design: the commit comes out empty and the edit
  lands in Luca's uncommitted working state.

## Inputs

- `CODE` — the finding id, e.g. `F042` (case-insensitive; `f42` also works).
- `COMMENT` — free text. Two modes:
  - **Dismissal** — "it is not an issue", "not a problem", "false positive",
    "intended / by design", "wontfix", "leave as is", "correct as is", "ignore".
    → mark solved, **no worktree, no thesis edit**.
  - **Fix guidance** — anything else (may be empty). Combine it with the
    finding's `suggested_fix` to make the concrete edit. Empty COMMENT ⇒ apply
    `suggested_fix` as recorded, using your judgment.

## Procedure

All commands are run **from the repo root**.

1. **Guard.** If `claude-review/findings/merged.json` does not exist, tell the
   user there's no current Claude review (run `deep-review` first) and stop.

2. **Read the finding:**

   ```
   python3 .claude/skills/solve-issue/solve_issue.py show CODE
   ```

   If it errors "not found", report that and stop. If the JSON shows
   `"solved": true`, tell the user it's already resolved and ask (AskUserQuestion)
   whether to redo it before proceeding.

3. **Classify the COMMENT** as *dismissal* or *fix guidance* (see Inputs).

4. **If dismissal:** no worktree, no thesis edit. Skip to step 8 with `--dismiss`
   and a note capturing the user's reason.

5. **If fix guidance — open a worktree:**

   ```
   python3 .claude/skills/solve-issue/solve_issue.py start CODE
   ```

   This prints the worktree path (`.claude/worktrees/solve-fnnn`), branched
   detached off `main`. If it says the worktree already exists, that finding is
   already in flight in another session — check with the user before continuing.

6. **Make the edit inside the worktree:**
   - Open `<worktree>/<file>`, where the file is the part of `location` before
     `:`. **Locate the text by the finding's `quote`, not the line number** — the
     recorded line may be stale. Read around the area and confirm the exact spot.
   - The worktree holds **committed** `main`, so it won't show Luca's
     uncommitted hand-edits. If the `quote` isn't there, he may have already
     fixed it by hand — check the main checkout before concluding anything, and
     say so rather than inventing a fix.
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
   - **No verification build by default.** These edits are small and Luca
     compiles the thesis himself. If you are genuinely unsure the edit still
     compiles (a macro, environment, or math-mode change you can't fully reason
     through), **ask the user** whether to build before landing rather than
     building on your own initiative — a cold `latexmk` in a fresh worktree has
     no `build/` cache and takes minutes.

7. **Commit in the worktree** (this is the only git command you run by hand):

   ```
   git -C <worktree> commit -am "fix CODE: <one-line summary>"
   ```

   If nothing was committed, do not proceed — report the blocker instead.

8. **Find related unsolved findings:**

   ```
   python3 .claude/skills/solve-issue/solve_issue.py related CODE
   ```

   This lists unsolved findings sharing the location or overlapping the quote
   (e.g. the same defect flagged by two agents, or corroborating duplicates).
   **Judge** which are genuinely resolved by the *same* edit you just made — only
   those become `--also`. Mention any related-but-not-covered findings to the
   user so they aren't silently lost.

9. **Land it + mark solved + regenerate the report** (one locked step):

   ```
   python3 .claude/skills/solve-issue/solve_issue.py integrate CODE \
     --note "<what you changed, or why dismissed>" \
     [--dismiss] [--also F0xx F0yy]
   ```

   It cherry-picks the worktree's commit onto `main`, flips
   `solved`/`resolution`/`solved_at`/`commit` for `CODE` and each `--also` id,
   rebuilds the local `report.html` + `report.md`, and removes the worktree.
   Relay the printed `progress: N/total`. **Do not publish the report anywhere** —
   it is local only.

   `integrate` can refuse, and every refusal leaves main untouched, the finding
   unsolved, and the fix safe in its worktree. Relay the message as-is and stop —
   do not work around it:
   - **Uncommitted edits genuinely conflict** — Luca is hand-editing the same
     lines. He commits or stashes them, then you retry `integrate`.
     (Uncommitted edits *elsewhere*, even in the same file, are merged around
     automatically — that is expected and not a problem.)
   - **Cherry-pick conflicts with a committed change** — another finding landed
     in the same lines while you worked. Redo the edit against current `main`
     (abandon and restart), or hand it to Luca.
   - **Already solved** — another instance covered it via `--also`. Confirm with
     the user before `--redo`.

10. **Report** concisely: the thesis edit made (file + what changed) or that it
    was dismissed; the finding(s) now solved (`CODE` + any `--also`); any related
    findings left for separate handling; and the new `N/total` progress. Mention
    the user can reopen `claude-review/report.html` to see it refreshed.

To give up on a finding mid-flight, drop its worktree with
`python3 .claude/skills/solve-issue/solve_issue.py abandon CODE` (add `--force` if
it has uncommitted junk). The finding stays unsolved.

## Constraints

- **One finding per invocation.** For a batch, run the skill once per CODE — or
  in several parallel sessions, which is what the worktree design is for.
- **The edit goes in the worktree.** Never edit the main checkout to fix a
  finding; that is Luca's hand-editing space, and `integrate` deliberately merges
  *around* whatever he has uncommitted there.
- **Never land a fix by hand.** `integrate` owns the cherry-pick, the lock, and
  the ordering. Bare `git cherry-pick`/`git merge` will also refuse whenever a
  touched file is dirty at all, which is most of the time.
- **`merged.json` is authoritative.** The raw `findings/agents/*.json` snapshots
  are a frozen record and are not modified by this skill.
- **Never mark solved without doing the work:** either a real commit landed on
  main, or the COMMENT was a genuine dismissal. No silent solves.
- **Never edit `arxiv-papers/`**; only the extracted/hand-editable copies. Do not
  re-run any editorial skill (`extract-paper`, `merge-*`) — see
  [[feedback-dont-rerun-editorial-scripts]].
- Trust the helper's `related` list as *candidates*, not orders — you decide what
  `--also` actually covers.
- **The report is local only.** Never publish it as an Artifact or upload it
  anywhere — `claude-review/report.html` is the single copy, by design (one
  reference is easier to keep in sync than two).
- The whole `claude-review/` folder is git-ignored; edits there are local working
  state, not manuscript changes.
