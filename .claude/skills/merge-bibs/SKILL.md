---
name: merge-bibs
description: Run scripts/merge_bibs.py (the per-paper sidecar bibliography merger) in the right mode and report concisely. Use when the user asks to merge bibs, refresh papers-bibliography.bib, audit pending duplicate prompts, or auto-resolve outstanding matches.
---

# merge-bibs

Wrap `scripts/merge_bibs.py`, which consolidates `arxiv-papers/arXiv-*.bib` into the thesis-wide `papers-bibliography.bib` with sticky editorial citation maps (`arxiv-papers/arXiv-YYMM-citation-map.json`). See the *Bibliography* section in `CLAUDE.md` for the design.

## Modes

Pick the mode that matches the user's intent. If ambiguous, default to `dry-run` and ask.

### `dry-run` — read-only report (safe default)

```
uv run scripts/merge_bibs.py --dry-run
```

Parses every sidecar, simulates the matching pipeline, writes nothing. Report from the script's tail:

- `Parsed N entries`
- `Groups: N`
- The stats block: `auto_merge_identifier`, `auto_merge_canonical`, `auto_merge_in_sidecar_canonical`, `would_prompt`, `in_sidecar_would_prompt`, `new`, `tag_collisions_extended`.

Keep the report under ~8 lines; lead with numbers, not prose. Surface any `[dup-local-tag]` warnings if there are new ones since the last run (they're sidecar-quality bugs Luca might want to fix).

### `interactive` — editorial run, driven by the user

**The user must run this themselves.** Tell them to type, at the chat prompt:

```
! uv run scripts/merge_bibs.py
```

The `!` prefix runs the command in the terminal session so its output shows up in the conversation. Each prompt requires their editorial judgment — never drive the prompts yourself. After the script exits cleanly, report the final stats block. If the user aborted (`q`) mid-run, point out that progress is saved and they can resume with the same command.

### `no-stop` — batch auto-resolve

```
uv run scripts/merge_bibs.py --no-stop
```

Resolves remaining candidate matches by heuristic (DOI > eprint > published > longest-excluding-abstract). Writes the bib + maps. Appropriate for:

- Regenerating the bib on a fresh clone after maps already encode the editorial decisions (idempotent, no new prompts will fire).
- Spot-checking what the heuristic would pick.

**Not** appropriate as a substitute for a real editorial pass on new duplicates. Before invoking, confirm with the user — `--no-stop` overrides their editorial control for any candidates not yet in the maps.

## What the script writes

- `papers-bibliography.bib` at the repo root — tracked.
- `arxiv-papers/arXiv-YYMM-citation-map.json` per sidecar — tracked. The persistent record of editorial decisions (winners, tag-collision extensions, merged-away losers). Sticky across re-runs: once a global tag is assigned it never changes, so `\cite{}` calls in thesis prose stay stable.

In interactive mode the script also saves maps after every decision; aborting with `q` is safe.

## Constraints

- Never invoke `interactive` yourself — the user makes the editorial calls.
- Never edit the sidecar `.bib` files (`arxiv-papers/arXiv-*.bib`); they're managed by Luca. See [[feedback-arxiv-papers-readonly]].
- The script handles dup detection, tag generation, and idempotency itself — trust it; don't pre-process the inputs.
- If the user asks "why does this entry have such a long global tag?", look at its map record's `tag_baseline` + `collided_with` fields — they record exactly which other tags forced the extension.
