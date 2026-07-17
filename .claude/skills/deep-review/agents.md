# deep-review — agent roster & prompt templates

Read this before launching (step 4 of `SKILL.md`). Spawn each agent as a
**parallel background `Agent` call**. Every agent is **read-only** and writes a
single findings JSON to `claude-review/findings/agents/<agent-id>.json`.

## Shared finding schema (every agent emits a JSON array of these)

```jsonc
{
  "category": "Correctness|Soundness|Grammar/Language|Typography/LaTeX|Notation/Consistency|Visual/Figures|Citations/Bib|Structure/Narrative|Overclaim/Defence",
  "severity": "Critical|Major|Minor|Nitpick",
  "location": "relative/file.tex:LINE",     // or "PDF p.N" for visual/box findings
  "unit": "Introduction|Paper-YYMM|Front-matter|Cross-cutting",
  "quote": "<=~15 words of the offending source (or a description)",
  "description": "what's wrong and why it matters (+ how to verify, for math)",
  "suggested_fix": "concrete fix — NEVER applied by the agent",
  "confidence": "High|Medium|Low"            // REQUIRED for Correctness/Soundness
}
```

Write a **valid JSON array only** (no markdown fences) with the Write tool. `id`,
`source`, and `solved` are added later by `merge_findings.py` — agents omit them.

## Priority-aware severity rubric (put in every prompt)

- **Introduction** — held to a *perfect* standard: any math slip is `Critical`;
  even wording/typography nits may reach `Major`.
- **Paper main sections** — high standard: math errors `Critical`, prose nits `Minor`.
- **Appendices** — lenient: cap prose/typography at `Minor`; reserve `Major`/`Critical`
  for genuine math/logic errors.
- For every `Correctness`/`Soundness` finding, set `confidence` honestly and say in
  `description` *how to verify*, so false positives are easy to triage.

## Project style facts (put in every prompt)

- American English throughout ("normalize", "behavior").
- Cross-refs spelled out: "Equation~\eqref{}", "Section~\ref{}" — never "eq."/"Eq.".
- The Introduction uses a per-component O(1) normalization (x,w,w*~N(0,I); λ=Wx/√d;
  Q=WWᵀ/d) that intentionally differs from the papers' scalings — flag genuine
  inconsistencies, but know the bridge is deliberate.
- Read-only: never edit any thesis file; never edit `arxiv-papers/`; write only the
  one findings JSON. (See `CLAUDE.md`.)

## Roster (v1 used 13 agents — adjust the PAPER-* set to the active papers in `thesis.tex`)

| Agent id | Model | Scope | Dimension |
| --- | --- | --- | --- |
| `intro-lang` | opus | `chapters/{motivation,setting,saad_and_solla,exponents,appendix-introduction}.tex` | prose, grammar, American English, cross-ref style, clarity, typography |
| `intro-math` | opus | same intro files | math correctness/soundness; re-derive; check the O(1) normalization bridges to the papers |
| `paper-YYMM` (one each) | opus | `papers/YYMM/{abstract,sections/*,appendices/*}.tex` + `chapters/{paper,appendix}-YYMM.tex` | combined: math (deep on sections, lenient on appendices), notation, prose, typography |
| `examiner` | opus | intro + each paper's `abstract`+`sections/*` (skip proofs) + `front-matter/foreword.tex` | hostile committee: overclaims, unstated assumptions, novelty/limitation gaps; each fix appends "Q: <the question>"; category `Overclaim/Defence` |
| `xcut-notation` | sonnet | intro notation files + each paper's setting/abstract | cross-chapter symbol/terminology clashes (same symbol≠meaning; silent convention switches) |
| `xcut-bib` | sonnet | `build/thesis.log`, `build/thesis.blg`, `papers-bibliography.bib`, `extra-bibliography.bib`, all `\cite/\ref/\cref` | undefined/duplicate refs, malformed/missing bib fields, foreword↔chapters consistency |
| `xcut-typo` | sonnet | all active `.tex` + `build/thesis.log` | `chktex`+`lacheck` + log-mining (Overfull/Underfull/Warning), triaged high-signal only |
| `xcut-visual` | sonnet | `build/thesis.pdf` (read pages as images) | figure resolution/legibility, tables, floats, orphans/widows, title/abstract/ToC/CV pages |
| `front-matter` | sonnet | `front-matter/*` (abstracts en/it/fr, foreword, cv, acknowledgements, epigraph, cover) | native-quality it/fr checks, cross-abstract consistency, CV chronology/dates |

## Prompt scaffold (prepend to every agent prompt)

> You are the **`<AGENT-ID>`** reviewer in a squad auditing Luca Arnaboldi's PhD
> thesis (LaTeX, book class), repo root `/Users/.../phd-thesis`. STRICT READ-ONLY:
> do not edit/create/modify any thesis file; do not run latexmk/any build (unless
> your role explicitly uses existing build artifacts). Only READ files and WRITE
> your single findings JSON to EXACTLY
> `claude-review/findings/agents/<AGENT-ID>.json`. Touch nothing else.
> [Then paste: the finding schema, the severity rubric, the project style facts,
> your SCOPE (files), and your DIMENSION from the roster above. End with: "Report
> genuine, defence-relevant problems only — don't pad; be thorough. After writing
> the file, reply with a one-line summary: counts by severity + the file path."]

Role-specific notes:
- `intro-math` / `paper-*`: re-derive key steps; challenge every "clearly/it follows";
  check scaling factors (√d, d, n), signs, indices, limits, Itô vs Stratonovich.
- `xcut-visual`: work in ≤20-page batches; prioritise every figure/table page; verify
  figure formats with `pdfimages -list` before claiming a resolution problem.
- `xcut-typo`: `chktex` is noisy on math — triage to high-signal classes (missing `~`
  before `\cite/\ref`, doubled words, quote marks, bad spacing); report systemic
  patterns (e.g. "~N missing ties") as single findings, not hundreds of line items.
- `examiner`: be specific and adversarial; pair each finding's `suggested_fix` with the
  exact "Q: …" the committee would ask.

After all agents finish, return to `SKILL.md` step 5 (merge).
