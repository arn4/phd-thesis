"""Count how often each papers-bibliography.bib entry is cited across papers/.

For every entry in the thesis-wide ``papers-bibliography.bib`` this scans the
extracted paper bodies under ``papers/<YYMM>/`` and counts how many times the
entry is cited in each paper, then emits a single self-contained HTML report:

- one row per bib entry (every entry, cited or not), sorted by Total desc,
- one column per paper folder on disk,
- a *Total* column (sum over all on-disk papers), and
- a *Refined total* column (sum over only the papers actually \\include'd in
  ``thesis.tex`` -- i.e. present in the final compiled thesis).

The in-thesis set is derived from the uncommented
``\\include{chapters/paper-<YYMM>}`` lines in ``thesis.tex`` (the
``\\includeonly`` build selector is ignored), so the report tracks the lineup
automatically.

Run with:
    uv run scripts/citation_usage.py
    uv run scripts/citation_usage.py --open
    uv run scripts/citation_usage.py --output /tmp/cites.html

The artifact lands under the gitignored ``build/`` directory by default, so it
is never committed. The script only reads; it writes the one HTML file.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = REPO_ROOT / "papers"
ARXIV_DIR = REPO_ROOT / "arxiv-papers"
BIB_PATH = REPO_ROOT / "papers-bibliography.bib"
THESIS_PATH = REPO_ROOT / "thesis.tex"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "citation-usage.html"

# A papers/<YYMM>/ folder (excludes the stand-alone-paper.tex driver at the root).
YYMM_RE = re.compile(r"^\d{4}$")

# Authoritative bib-key regex (reused from scripts/check_bib_coverage.py): the @-line
# of every real entry, skipping @string/@preamble/@comment.
BIB_ENTRY_RE = re.compile(
    r"^[ \t]*@(?!string\b|preamble\b|comment\b)[A-Za-z]+[ \t]*\{\s*([^\s,]+)\s*,",
    re.IGNORECASE | re.MULTILINE,
)

# Uncommented \include{chapters/paper-YYMM} -> in-thesis. A leading % disqualifies
# the line (the [^%\n]* prefix cannot cross a comment char).
THESIS_INCLUDE_RE = re.compile(
    r"^[^%\n]*\\include\{chapters/paper-(\d{4})\}", re.MULTILINE
)

# Any citation command: optional starred form, up to two optional [..] arguments,
# then the mandatory {key,key,...}. Only \cite/\citep exist today, but the wider
# set is free insurance against future biblatex-style commands.
CITE_RE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|citeyearpar|"
    r"autocite|textcite|parencite|smartcite|footcite|footcitetext|supercite|"
    r"fullcite|citenum|Cite|Citep|Citet|Autocite|Textcite|Parencite)"
    r"\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^{}]*)\}",
    re.DOTALL,
)

# Strip from an unescaped % to end of line (LaTeX comment).
COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")

# --- importance-score defaults (all overridable on the CLI) ---
SCORE_MAIN_BASE = 1.0      # value of the 1st main-text citation in a paper ("a full one")
SCORE_MAIN_CAP = 5.0       # main depth saturates here (10 vs 15 citations barely differ)
SCORE_MAIN_DECAY = 0.5     # geometric decay of each successive main citation's marginal value
SCORE_APP_WEIGHT = 0.3     # value of each appendix citation (all equal, < a full main cite)
SCORE_BREADTH_FLOOR = 0.4  # breadth multiplier at zero coverage; rises to 1.0 at full coverage
SCORE_EXTERNAL_WEIGHT = 0.5  # weight of a citation coming from a paper not in the final thesis

# Bib keys that ARE one of the thesis's own papers, keyed by YYMM. detect_own_keys() finds
# most by exact title match against the metadata.json sidecars; this table supplements the
# cases title-normalisation misses (e.g. punctuation drift) or that should be forced.
THESIS_OWN_OVERRIDES: dict[str, list[str]] = {
    "2406": ["arnaboldi2024online"],  # title differs only by "On The"/"&"/punctuation
}

YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def parse_year(text: str) -> int | None:
    """First 4-digit 19xx/20xx year found in a bib year field, else None."""
    m = YEAR_RE.search(text or "")
    return int(m.group(0)) if m else None


def paper_year(yymm: str) -> int:
    """Calendar year of a thesis paper from its YYMM id (2302 -> 2023)."""
    return 2000 + int(yymm[:2])


def _norm_title(title: str) -> str:
    """Lowercase, accent-stripped, alnum-only key for matching titles across sources."""
    title = unicodedata.normalize("NFKD", title)
    title = "".join(c for c in title if not unicodedata.combining(c))
    title = title.lower()
    title = re.sub(r"[^a-z0-9 ]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def main_citation_value(m: int, base: float, cap: float, decay: float) -> float:
    """Depth value of `m` main-text citations in one paper.

    base + (cap-base)*(1 - decay^(m-1)): the 1st citation is worth `base`, the 2nd adds
    more than the 1st (so 2 > 2x1), and the total saturates at `cap`.
    """
    if m <= 0:
        return 0.0
    return base + (cap - base) * (1.0 - decay ** (m - 1))


def importance_score(
    pc: dict[str, list[int]],
    scope: set[str],
    thesis_set: set[str],
    ref_year: int | None,
    params: dict,
) -> dict:
    """Importance of a reference over the papers in `scope`.

    `pc` maps yymm -> [main, appendix] counts. Citations from papers outside `thesis_set`
    are down-weighted by params['w_excl']. Returns the score plus its breakdown.
    """
    depth = 0.0
    citers: list[str] = []
    for yymm, (m, a) in pc.items():
        if yymm not in scope:
            continue
        if m + a > 0:
            citers.append(yymm)
        w = 1.0 if yymm in thesis_set else params["w_excl"]
        depth += w * (
            main_citation_value(m, params["base"], params["cap"], params["decay"])
            + params["w_app"] * a
        )

    # Papers that *could* have cited a work from ref_year, restricted to the scope, plus any
    # that actually did (guards against bib year > preprint year, and keeps coverage <= 1).
    if ref_year is None:
        eligible = set(scope)
    else:
        eligible = {p for p in scope if paper_year(p) >= ref_year}
    eligible |= set(citers)

    coverage = (len(citers) / len(eligible)) if eligible else 0.0
    mult = params["floor"] + (1.0 - params["floor"]) * coverage
    return {
        "score": depth * mult,
        "depth": depth,
        "citing": len(citers),
        "eligible": len(eligible),
        "coverage": coverage,
        "mult": mult,
    }


def detect_own_keys(
    papers: list[str], meta: dict[str, dict[str, str]], valid_keys: set[str]
) -> dict[str, str]:
    """Map bib keys that ARE one of the thesis's own papers -> their YYMM.

    Matches the hand-curated title in each ``arxiv-papers/arXiv-<YYMM>-metadata.json``
    against bib titles (exact, normalised), then layers in THESIS_OWN_OVERRIDES. Tolerant
    of missing/unparseable sidecars.
    """
    by_title: dict[str, list[str]] = defaultdict(list)
    for key, m in meta.items():
        nt = _norm_title(m.get("title", ""))
        if nt:
            by_title[nt].append(key)

    own: dict[str, str] = {}
    for yymm in papers:
        candidates: list[str] = []
        mj = ARXIV_DIR / f"arXiv-{yymm}-metadata.json"
        if mj.exists():
            try:
                title = json.loads(mj.read_text(encoding="utf-8")).get("title", "")
            except (OSError, ValueError):
                title = ""
            nt = _norm_title(title)
            if nt:
                candidates.extend(by_title.get(nt, []))
        candidates.extend(THESIS_OWN_OVERRIDES.get(yymm, []))
        for key in candidates:
            if key in valid_keys:
                own[key] = yymm
    return own


def discover_papers() -> list[str]:
    """YYMM folders present under papers/."""
    if not PAPERS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in PAPERS_DIR.iterdir() if p.is_dir() and YYMM_RE.match(p.name)
    )


def in_thesis_papers() -> list[str]:
    """YYMM ids actually \\include'd into the final thesis."""
    if not THESIS_PATH.exists():
        return []
    text = THESIS_PATH.read_text(encoding="utf-8", errors="replace")
    return sorted(set(THESIS_INCLUDE_RE.findall(text)))


def bib_keys() -> list[str]:
    """Authoritative ordered list of every cite key in the bib."""
    text = BIB_PATH.read_text(encoding="utf-8", errors="replace")
    seen: set[str] = set()
    ordered: list[str] = []
    for key in BIB_ENTRY_RE.findall(text):
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def bib_metadata() -> dict[str, dict[str, str]]:
    """Best-effort display metadata (title/author/year/type) keyed by cite key.

    Uses bibtexparser the same way scripts/merge_bibs.py does. Any entry it drops
    simply gets no metadata -- the authoritative key list from bib_keys() still
    guarantees a row, so this never affects which entries appear.
    """
    try:
        import bibtexparser
        from bibtexparser.bparser import BibTexParser
    except Exception:  # pragma: no cover - bibtexparser is a declared dependency
        return {}

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    try:
        with BIB_PATH.open(encoding="utf-8") as fh:
            db = bibtexparser.load(fh, parser=parser)
    except Exception as exc:
        print(f"warning: bibtexparser could not parse the bib ({exc}); "
              f"rendering keys without metadata", file=sys.stderr)
        return {}

    meta: dict[str, dict[str, str]] = {}
    for entry in db.entries:
        key = entry.get("ID")
        if not key:
            continue
        meta[key] = {
            "title": _clean(entry.get("title", "")),
            "author": _short_author(entry.get("author", "")),
            "year": _clean(entry.get("year", "")),
            "type": entry.get("ENTRYTYPE", ""),
        }
    return meta


def _clean(value: str) -> str:
    """Flatten braces/whitespace from a bib field for plain-text display."""
    value = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", value).strip()


def _short_author(author: str) -> str:
    """First author's surname, with 'et al.' when there are co-authors."""
    author = author.strip()
    if not author:
        return ""
    first, *rest = re.split(r"\s+and\s+", author)
    first = _clean(first)
    surname = first.split(",")[0].strip() if "," in first else first.split()[-1]
    return f"{surname} et al." if rest else surname


def count_citations(papers: list[str]) -> dict[str, dict[str, list[int]]]:
    """Return counts[key][yymm] = [main, appendix] raw citation occurrences.

    A citation is classified as *appendix* when its file lives under the paper's
    ``appendices/`` directory; everything else (``sections/``, ``main.tex``,
    ``abstract.tex``) counts as *main*. Holds every key seen in the bodies,
    including keys not in the bib (orphans); the caller separates those out.
    """
    counts: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    for yymm in papers:
        paper_dir = PAPERS_DIR / yymm
        for tex in sorted(paper_dir.rglob("*.tex")):
            slot = 1 if "appendices" in tex.relative_to(paper_dir).parts else 0
            text = tex.read_text(encoding="utf-8", errors="replace")
            text = COMMENT_RE.sub("", text)
            for match in CITE_RE.finditer(text):
                for raw in match.group(1).split(","):
                    key = raw.strip()
                    if key:
                        counts[key][yymm][slot] += 1
    return counts


def build_html(
    keys: list[str],
    meta: dict[str, dict[str, str]],
    counts: dict[str, dict[str, int]],
    papers: list[str],
    thesis_papers: list[str],
    own_keys: dict[str, str],
    params: dict,
) -> str:
    thesis_set = set(thesis_papers)

    def split_of(pc: dict[str, list[int]], scope: set[str] | None) -> tuple[int, int]:
        """Sum (main, appendix) over papers in `scope` (None = all papers)."""
        m = a = 0
        for yymm, (pm, pa) in pc.items():
            if scope is None or yymm in scope:
                m += pm
                a += pa
        return m, a

    all_set = set(papers)

    # Build rows: (key, per-paper [main,app], total_main, total_app, total,
    #              refined_main, refined_app, refined, score_full, score_refined).
    rows = []
    for key in keys:
        pc = counts.get(key, {})
        tm, ta = split_of(pc, None)
        rm, ra = split_of(pc, thesis_set)
        ry = parse_year(meta.get(key, {}).get("year", ""))
        sf = importance_score(pc, all_set, thesis_set, ry, params)
        sr = importance_score(pc, thesis_set, thesis_set, ry, params)
        rows.append((key, pc, tm, ta, tm + ta, rm, ra, rm + ra, sf, sr))
    rows.sort(key=lambda r: (-r[8]["score"], -r[4], r[0]))

    keyset = set(keys)
    orphans = sorted(
        (k for k in counts if k not in keyset),
        key=lambda k: (-sum(sum(v) for v in counts[k].values()), k),
    )

    cited = sum(1 for r in rows if r[4] > 0)
    uncited = len(rows) - cited
    grand_main = sum(r[2] for r in rows)
    grand_app = sum(r[3] for r in rows)
    grand_total = grand_main + grand_app
    own_count = len(own_keys)

    esc = html.escape
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    def paper_label(yymm: str) -> str:
        mark = " ✓" if yymm in thesis_set else ""
        return f"{yymm}{mark}"

    def split_cell(m: int, a: int, base: str) -> str:
        """Per-paper cell: 'main·appendix' (appendix dimmed), blank when zero."""
        tot = m + a
        cls = base + (" z" if tot == 0 else "")
        inner = "" if tot == 0 else f'{m}<span class="ap">&middot;{a}</span>'
        return f'<td class="{cls}" data-v="{tot}">{inner}</td>'

    def total_cell(m: int, a: int, base: str) -> str:
        """Total/Refined cell: bold combined sum + small 'main / app' split."""
        tot = m + a
        cls = base + (" z" if tot == 0 else "")
        inner = "" if tot == 0 else (
            f'<span class="bg">{tot}</span> <span class="sp">{m} / {a}</span>'
        )
        return (f'<td class="{cls}" data-v="{tot}" '
                f'title="main {m} / appendix {a}">{inner}</td>')

    def score_cell(s: dict, base: str) -> str:
        """Score/Refined-score cell: value + tooltip with the depth × breadth breakdown."""
        val = s["score"]
        cls = base + (" z" if val == 0 else "")
        disp = f"{val:.1f}" if val else ""
        tip = (f"depth {s['depth']:.1f} × breadth {s['mult']:.2f} "
               f"({s['citing']}/{s['eligible']} eligible papers, "
               f"coverage {s['coverage']:.0%})")
        return f'<td class="{cls}" data-v="{val:.4f}" title="{tip}">{disp}</td>'

    # --- header row ---
    head_cells = [
        '<th class="num" data-sort="num">#</th>',
        '<th data-sort="text">Cite key</th>',
        '<th data-sort="text">Title</th>',
        '<th class="num" data-sort="num">Year</th>',
    ]
    for yymm in papers:
        cls = "num paper" + (" intc" if yymm in thesis_set else " outc")
        where = "in thesis" if yymm in thesis_set else "not in thesis"
        head_cells.append(
            f'<th class="{cls}" data-sort="num" '
            f'title="papers/{yymm}/ ({where}) — main · appendix">'
            f'{paper_label(yymm)}<span class="hsub">main&middot;app</span></th>'
        )
    head_cells.append(
        '<th class="num total" data-sort="num" title="all papers on disk">'
        'Total<span class="hsub">main / app</span></th>'
    )
    head_cells.append(
        '<th class="num refined" data-sort="num" title="only papers in the final thesis">'
        'Refined<span class="hsub">main / app</span></th>'
    )
    head_cells.append(
        '<th class="num score" data-sort="num" '
        'title="importance over all papers on disk (excluded-paper citations down-weighted)">'
        'Score<span class="hsub">importance</span></th>'
    )
    head_cells.append(
        '<th class="num rscore" data-sort="num" title="importance over in-thesis papers only">'
        'Refined<span class="hsub">score</span></th>'
    )
    thead = "<tr>" + "".join(head_cells) + "</tr>"

    # --- body rows ---
    body_rows = []
    for rank, (key, pc, tm, ta, total, rm, ra, refined, sf, sr) in enumerate(rows, start=1):
        m = meta.get(key, {})
        title = m.get("title", "")
        author = m.get("author", "")
        year = m.get("year", "")
        title_disp = title or '<span class="muted">—</span>'
        tip = esc(f"{author} ({year}) {title}".strip()) if (title or author) else ""
        own_yymm = own_keys.get(key)
        if own_yymm:
            badge = (f'<span class="own" title="this reference is thesis paper {own_yymm}'
                     f'{" (in the final thesis)" if own_yymm in thesis_set else ""}">'
                     f'&#9733; {own_yymm}{" &#10003;" if own_yymm in thesis_set else ""}</span>')
        else:
            badge = ""
        cells = [
            f'<td class="num rank">{rank}</td>',
            f'<td class="key"><code>{esc(key)}</code>{badge}</td>',
            f'<td class="title" title="{tip}">{esc(title) if title else title_disp}</td>',
            f'<td class="num">{esc(year)}</td>',
        ]
        for yymm in papers:
            pm, pa = pc.get(yymm, (0, 0))
            base = "num cell" + (" intc" if yymm in thesis_set else " outc")
            cells.append(split_cell(pm, pa, base))
        cells.append(total_cell(tm, ta, "num total"))
        cells.append(total_cell(rm, ra, "num refined"))
        cells.append(score_cell(sf, "num score"))
        cells.append(score_cell(sr, "num rscore"))
        tr_cls = ' class="ownrow"' if own_yymm else ""
        zero_attr = ' data-zero="1"' if total == 0 else ""
        body_rows.append(f'<tr{tr_cls}{zero_attr}>' + "".join(cells) + "</tr>")
    tbody = "\n".join(body_rows)

    # --- orphan diagnostics ---
    if orphans:
        orphan_head = (
            "<tr><th>Cite key</th>"
            + "".join(f'<th class="num">{paper_label(y)}</th>' for y in papers)
            + '<th class="num">Total</th></tr>'
        )
        orphan_body = []
        for key in orphans:
            pc = counts[key]
            cells = [f'<td class="key"><code>{esc(key)}</code></td>']
            for yymm in papers:
                pm, pa = pc.get(yymm, (0, 0))
                cells.append(split_cell(pm, pa, "num"))
            om, oa = split_of(pc, None)
            cells.append(total_cell(om, oa, "num total"))
            orphan_body.append("<tr>" + "".join(cells) + "</tr>")
        orphan_section = f"""
  <h2>Citations not found in the bibliography <span class="muted">({len(orphans)})</span></h2>
  <p class="note">Keys cited in paper bodies but absent from <code>papers-bibliography.bib</code> &mdash; likely stale or renamed keys.</p>
  <table class="report">
    <thead>{orphan_head}</thead>
    <tbody>{''.join(orphan_body)}</tbody>
  </table>"""
    else:
        orphan_section = (
            '\n  <h2>Citations not found in the bibliography</h2>'
            '\n  <p class="note ok">None &mdash; every cited key resolves to a bib entry.</p>'
        )

    disk_list = ", ".join(papers)
    thesis_list = ", ".join(thesis_papers) or "(none)"
    excluded = ", ".join(y for y in papers if y not in thesis_set) or "(none)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Citation usage &mdash; papers-bibliography.bib</title>
<style>
  :root {{
    --ink:#1d2433; --muted:#8a93a6; --line:#e3e7ef; --bg:#f6f7fb;
    --accent:#2f6fed; --accent-bg:#eaf1ff; --warn:#b4690e;
  }}
  * {{ box-sizing:border-box; }}
  body {{ font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); margin:0; background:var(--bg); }}
  .wrap {{ max-width:100%; padding:24px 28px 60px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:16px; margin:34px 0 8px; }}
  .sub {{ color:var(--muted); margin:0 0 18px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin:0 0 18px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px;
           padding:10px 14px; min-width:120px; }}
  .card .n {{ font-size:22px; font-weight:600; }}
  .card .l {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
  .legend {{ background:#fff; border:1px solid var(--line); border-radius:10px;
             padding:10px 14px; margin:0 0 18px; font-size:13px; }}
  .legend b {{ font-weight:600; }}
  .pill {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px;
           border:1px solid var(--line); margin:1px 2px; }}
  .pill.in {{ background:var(--accent-bg); border-color:#bcd3ff; color:var(--accent); }}
  .controls {{ display:flex; gap:14px; align-items:center; margin:0 0 10px; flex-wrap:wrap; }}
  .controls input[type=search] {{ padding:6px 10px; border:1px solid var(--line);
           border-radius:8px; font-size:14px; min-width:240px; }}
  .controls label {{ color:var(--ink); font-size:13px; user-select:none; }}
  .count {{ color:var(--muted); font-size:13px; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:#fff; }}
  table.report {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
  table.report th, table.report td {{ padding:5px 9px; border-bottom:1px solid var(--line);
           text-align:left; white-space:nowrap; }}
  table.report thead th {{ position:sticky; top:0; background:#fff; z-index:2;
           border-bottom:2px solid var(--line); cursor:pointer; user-select:none; }}
  table.report thead th:hover {{ background:var(--accent-bg); }}
  table.report th.num, table.report td.num {{ text-align:right; }}
  table.report td.title {{ max-width:520px; overflow:hidden; text-overflow:ellipsis;
           white-space:nowrap; }}
  td.key code {{ font-size:12.5px; }}
  td.rank, .muted {{ color:var(--muted); }}
  td.z {{ color:#c7cdd8; }}
  .ap {{ color:var(--muted); font-size:12px; }}          /* appendix part of m·a */
  td.z .ap {{ color:#d4d9e2; }}
  td.total .bg, td.refined .bg {{ font-weight:600; }}
  .sp {{ color:var(--muted); font-size:11px; font-weight:400; }}  /* main / app split */
  td.refined .sp {{ color:#7fa3e8; }}
  .hsub {{ display:block; font-weight:400; font-size:10px; color:var(--muted);
           letter-spacing:0; text-transform:none; }}
  th.intc, td.intc {{ background:var(--accent-bg); }}
  th.outc, td.outc {{ background:#fafbfd; color:#9aa3b4; }}
  th.total, td.total {{ font-weight:600; border-left:2px solid var(--line); }}
  th.refined, td.refined {{ font-weight:600; color:var(--accent); }}
  th.score, td.score {{ font-weight:700; border-left:2px solid #cdb86b; background:#fffaf0; }}
  th.score {{ color:#8a6d1a; }}
  th.rscore, td.rscore {{ font-weight:700; color:#8a6d1a; background:#fffaf0; }}
  .own {{ display:inline-block; margin-left:6px; padding:0 6px; border-radius:999px;
          font-size:11px; font-weight:600; background:#fdebc6; color:#8a6d1a;
          border:1px solid #e8cf93; }}
  tr.ownrow td {{ background:#fffaf0; }}
  tr.ownrow td.intc {{ background:#fbf3e0; }}
  tr.ownrow td.score, tr.ownrow td.rscore {{ background:#fdf3dd; }}
  tbody tr:hover td {{ background:#f0f4ff; }}
  tbody tr:hover td.outc {{ background:#e7edf7; }}
  tbody tr.ownrow:hover td {{ background:#fbeecb; }}
  .note {{ color:var(--muted); margin:4px 0 10px; }}
  .note.ok {{ color:#2e7d32; }}
  details.method {{ background:#fff; border:1px solid var(--line); border-radius:10px;
           padding:8px 14px; margin:0 0 16px; font-size:13px; }}
  details.method summary {{ cursor:pointer; font-weight:600; color:var(--ink); }}
  details.method ul {{ margin:8px 0; padding-left:20px; }}
  details.method li {{ margin:3px 0; }}
  details.method .formula {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
           background:var(--bg); padding:1px 5px; border-radius:4px; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:28px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Citation usage across the thesis papers</h1>
  <p class="sub">How often each <code>papers-bibliography.bib</code> entry is cited in each paper under <code>papers/</code>. Generated {esc(ts)}.</p>

  <div class="cards">
    <div class="card"><div class="n">{len(keys)}</div><div class="l">bib entries</div></div>
    <div class="card"><div class="n">{cited}</div><div class="l">cited</div></div>
    <div class="card"><div class="n">{uncited}</div><div class="l">never cited</div></div>
    <div class="card"><div class="n">{grand_total}</div><div class="l">total citations</div></div>
    <div class="card"><div class="n">{grand_main}</div><div class="l">in main text</div></div>
    <div class="card"><div class="n">{grand_app}</div><div class="l">in appendices</div></div>
    <div class="card"><div class="n">{len(papers)}</div><div class="l">papers on disk</div></div>
    <div class="card"><div class="n">{len(thesis_papers)}</div><div class="l">papers in thesis</div></div>
    <div class="card"><div class="n">{own_count}</div><div class="l">own papers (&#9733;)</div></div>
  </div>

  <div class="legend">
    <b>Papers on disk:</b> {esc(disk_list)}<br>
    <b>In the final thesis</b> (counted in <i>Refined</i>): {''.join(f'<span class="pill in">{esc(p)}</span>' for p in thesis_papers)}<br>
    <b>On disk but excluded</b> from the thesis: {esc(excluded)}.<br>
    <span class="muted">Per-paper cells read <b>main&middot;appendix</b> (citations in <code>sections/</code> + <code>main.tex</code> vs. <code>appendices/</code>). <b>Total</b> sums every paper on disk, <b>Refined</b> only the in-thesis ones (&#10003;); each shows the combined count with its <b>main / app</b> split. Counts are raw occurrences (a key cited 3&times; counts 3).</span><br>
    <span class="muted">Rows marked <span class="own">&#9733; YYMM</span> are the thesis's own papers, cited by other chapters.</span>
  </div>

  <details class="method">
    <summary>How the importance <b>Score</b> works</summary>
    <p>Each reference is scored from how the thesis papers cite it:
    <span class="formula">Score = ( &Sigma;<sub>papers</sub> w&middot;[ S(m) + {params['w_app']}&middot;a ] ) &times; ( {params['floor']} + {1 - params['floor']:.1f}&middot;coverage )</span>,
    where <i>m</i>/<i>a</i> are the main/appendix citations in a paper.</p>
    <ul>
      <li><b>Main citations</b> have diminishing returns with an early boost:
        <span class="formula">S(m) = {params['base']:.0f} + {params['cap'] - params['base']:.0f}&middot;(1 &minus; {params['decay']}<sup>m&minus;1</sup>)</span>
        &mdash; 1 cite = {main_citation_value(1, params['base'], params['cap'], params['decay']):.0f},
        2 cites = {main_citation_value(2, params['base'], params['cap'], params['decay']):.0f} (more than double),
        saturating at {params['cap']:.0f} (so 10 vs 15 barely differ).</li>
      <li><b>Appendix citations</b> are all weighted equally at {params['w_app']} each (less than a full main citation) and do not escalate.</li>
      <li><b>Spread across papers is rewarded:</b> per-paper depth is summed and the main term saturates, so the same citations spread over several papers beat them piled into one.</li>
      <li><b>Source weight w:</b> a citation from a paper <i>not</i> in the final thesis counts {params['w_excl']}&times; (in-thesis papers count 1&times;).</li>
      <li><b>Breadth &amp; time discount:</b> coverage = citing &divide; <i>eligible</i> papers, where eligible = papers dated no earlier than the reference &mdash; so a recent work is not penalised for the older papers that could never have cited it.</li>
    </ul>
    <p class="muted"><b>Refined score</b> applies the same formula over only the in-thesis papers. Hover any Score cell for its depth &times; breadth breakdown.</p>
  </details>

  <h2>All references <span class="muted">(sorted by Score, click any header to re-sort)</span></h2>
  <div class="controls">
    <input id="filter" type="search" placeholder="Filter by key or title&hellip;">
    <label><input id="hideZero" type="checkbox"> hide never-cited</label>
    <span class="count" id="count"></span>
  </div>
  <div class="tablewrap">
    <table class="report" id="main">
      <thead>{thead}</thead>
      <tbody>
{tbody}
      </tbody>
    </table>
  </div>
{orphan_section}

  <footer>Generated by <code>scripts/citation_usage.py</code>. This file lives under the gitignored <code>build/</code> directory and is not committed. Re-run after editing the bib or paper bodies to refresh.</footer>
</div>

<script>
(function () {{
  var table = document.getElementById('main');
  var tbody = table.tBodies[0];
  var headers = table.tHead.rows[0].cells;
  var rows = Array.prototype.slice.call(tbody.rows);
  var sortState = {{ col: -1, dir: 1 }};

  function cellVal(row, i, numeric) {{
    var cell = row.cells[i];
    if (numeric) {{
      var dv = cell.getAttribute('data-v');
      var n = parseFloat(dv !== null ? dv : cell.textContent);
      return isNaN(n) ? -1 : n;
    }}
    return (cell.textContent || '').trim().toLowerCase();
  }}

  function sortBy(i) {{
    var numeric = headers[i].getAttribute('data-sort') === 'num';
    var dir = (sortState.col === i) ? -sortState.dir : (numeric ? -1 : 1);
    sortState = {{ col: i, dir: dir }};
    rows.sort(function (a, b) {{
      var va = cellVal(a, i, numeric), vb = cellVal(b, i, numeric);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    }});
    rows.forEach(function (r) {{ tbody.appendChild(r); }});
  }}

  Array.prototype.forEach.call(headers, function (h, i) {{
    h.addEventListener('click', function () {{ sortBy(i); }});
  }});

  var filter = document.getElementById('filter');
  var hideZero = document.getElementById('hideZero');
  var count = document.getElementById('count');

  function apply() {{
    var q = filter.value.trim().toLowerCase();
    var hz = hideZero.checked;
    var shown = 0;
    rows.forEach(function (r) {{
      var isZero = r.getAttribute('data-zero') === '1';
      var key = r.cells[1].textContent.toLowerCase();
      var title = r.cells[2].textContent.toLowerCase();
      var matchQ = !q || key.indexOf(q) !== -1 || title.indexOf(q) !== -1;
      var visible = matchQ && !(hz && isZero);
      r.style.display = visible ? '' : 'none';
      if (visible) shown++;
    }});
    count.textContent = shown + ' / ' + rows.length + ' shown';
  }}

  filter.addEventListener('input', apply);
  hideZero.addEventListener('change', apply);
  apply();
}})();
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"HTML output path (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)}).",
    )
    p.add_argument(
        "--open", action="store_true",
        help="Open the report in the default browser after writing (macOS `open`).",
    )
    g = p.add_argument_group("importance-score tuning")
    g.add_argument(
        "--main-cap", type=float, default=SCORE_MAIN_CAP,
        help=f"Saturation cap for main-citation depth in one paper (default {SCORE_MAIN_CAP}).",
    )
    g.add_argument(
        "--main-decay", type=float, default=SCORE_MAIN_DECAY,
        help=f"Geometric decay of each extra main citation's value (default {SCORE_MAIN_DECAY}).",
    )
    g.add_argument(
        "--app-weight", type=float, default=SCORE_APP_WEIGHT,
        help=f"Value of each appendix citation, all equal (default {SCORE_APP_WEIGHT}).",
    )
    g.add_argument(
        "--breadth-floor", type=float, default=SCORE_BREADTH_FLOOR,
        help=f"Breadth multiplier at zero coverage; 1.0 at full (default {SCORE_BREADTH_FLOOR}).",
    )
    g.add_argument(
        "--external-weight", type=float, default=SCORE_EXTERNAL_WEIGHT,
        help="Weight of a citation from a paper not in the final thesis "
             f"(default {SCORE_EXTERNAL_WEIGHT}; set 1.0 to disable).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not BIB_PATH.exists():
        print(f"error: {BIB_PATH} not found", file=sys.stderr)
        return 2
    if not PAPERS_DIR.is_dir():
        print(f"error: {PAPERS_DIR} not found", file=sys.stderr)
        return 2

    papers = discover_papers()
    thesis_papers = in_thesis_papers()
    keys = bib_keys()
    meta = bib_metadata()
    counts = count_citations(papers)
    own_keys = detect_own_keys(papers, meta, set(keys))

    params = {
        "base": SCORE_MAIN_BASE,
        "cap": args.main_cap,
        "decay": args.main_decay,
        "w_app": args.app_weight,
        "floor": args.breadth_floor,
        "w_excl": args.external_weight,
    }

    # Informational consistency check between disk and thesis.tex.
    missing_on_disk = [y for y in thesis_papers if y not in papers]
    if missing_on_disk:
        print(f"warning: thesis includes {missing_on_disk} but no papers/<YYMM>/ "
              f"folder exists for them", file=sys.stderr)

    html_text = build_html(keys, meta, counts, papers, thesis_papers, own_keys, params)

    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(html_text, encoding="utf-8")
    tmp.replace(out)

    bib_key_set = set(keys)
    orphans = {k for k in counts if k not in bib_key_set}
    cited = sum(1 for k in keys if counts.get(k))
    grand_main = sum(pm for k in keys for pm, _ in counts.get(k, {}).values())
    grand_app = sum(pa for k in keys for _, pa in counts.get(k, {}).values())

    # Top references by importance score (for the stdout summary).
    all_set, thesis_set = set(papers), set(thesis_papers)
    scored = sorted(
        (
            (importance_score(
                counts.get(k, {}), all_set, thesis_set,
                parse_year(meta.get(k, {}).get("year", "")), params)["score"],
                k,
            )
            for k in keys
        ),
        reverse=True,
    )

    print(f"bib entries:      {len(keys)}  ({cited} cited, {len(keys) - cited} never cited)")
    print(f"total citations:  {grand_main + grand_app} occurrences across {len(papers)} "
          f"papers on disk  ({grand_main} main, {grand_app} appendix)")
    print(f"papers on disk:   {', '.join(papers)}")
    print(f"in final thesis:  {', '.join(thesis_papers)}  (counted in 'Refined')")
    if orphans:
        print(f"orphan keys:      {len(orphans)} cited but not in the bib")

    own_str = ", ".join(f"{y}→{k}" for k, y in sorted(own_keys.items(),
                                                       key=lambda kv: kv[1]))
    print(f"own-paper rows:   {len(own_keys)}  ({own_str or 'none detected'})")
    no_entry = [y for y in papers if y not in set(own_keys.values())]
    if no_entry:
        print(f"  (no bib entry for: {', '.join(no_entry)} — not cited by other chapters)")
    print("top by score:     " + "; ".join(f"{k} {s:.1f}" for s, k in scored[:3]))
    print(f"wrote:            {out}")

    if args.open:
        try:
            subprocess.run(["open", str(out)], check=False)
        except FileNotFoundError:
            print("note: `open` not available; open the file manually", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
