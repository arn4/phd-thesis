"""Extract one arXiv paper into papers/YYMM/ in a clean thesis-ready layout.

Run with:
    uv run scripts/paper_extraction.py 2302
    uv run scripts/paper_extraction.py arXiv-2302.05882v1 --force
    uv run scripts/paper_extraction.py 2305 --dry-run
    uv run scripts/paper_extraction.py 2402 --no-compile

Outputs (per paper, at papers/YYMM/):
    abstract.tex            - abstract body only, no \\begin{abstract} wrapper
    main.tex                - \\input{sections/...} ... \\appendix \\input{appendices/...}
    sections/<slug>.tex     - one file per (real) section, with its \\section header
    appendices/<slug>.tex   - one file per appendix
    figs/                   - figures, with subdir structure preserved

Also writes papers/stand-alone-paper.tex (idempotent), a single \\jobname-
dispatched driver that uses the global papers-{dependencies,macros,bibliography}
files to compile any paper individually for review.

Citation keys are rewritten via arxiv-papers/arXiv-YYMM-citation-map.json.
Renamed macros are rewritten via arxiv-papers/arXiv-YYMM-macro-map.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from paper_parsing import (
    REPO_ROOT,
    ARXIV_DIR,
    Source,
    consume_pattern,
    die,
    find_main_tex,
    make_source,
    read_control_seq,
    resolve_paper_folder,
    scan_sidecar_full,
    skip_balanced_braces,
    skip_balanced_brackets,
    skip_ws_and_comments,
    write_atomic,
    yymm_of,
)

PAPERS_DIR = REPO_ROOT / "papers"
BUILD_DIR = REPO_ROOT / "build"

# Globals produced by the merger pipeline. All required to exist.
GLOBAL_DEPS_TEX = REPO_ROOT / "papers-dependencies.tex"
GLOBAL_MACROS_TEX = REPO_ROOT / "papers-macros.tex"
GLOBAL_BIB = REPO_ROOT / "papers-bibliography.bib"

STANDALONE_DRIVER = PAPERS_DIR / "stand-alone-paper.tex"


# ---------------------------------------------------------------------------
# Command tables for the body walker
# ---------------------------------------------------------------------------

# Top-level heading commands that start a new section unit. Subsection and
# below stay inside their parent's body — they don't split units.
HEADING_CMDS = {"section", "chapter"}

# Commands that bring in another file as content.
INPUT_CMDS = {"input", "include"}

# Commands whose entire call (name + args) we strip from the body of the
# extracted paper. The standalone driver owns title/bib/page setup, and
# class-specific metadata commands (ceurart's \copyrightyear etc.) make no
# sense outside their original class.
DROP_PATTERNS: dict[str, str] = {
    "maketitle": "",
    "tableofcontents": "",
    "thispagestyle": "r",
    "pagestyle": "r",
    "setcounter": "r r",
    "addtocounter": "r r",
    "newpage": "",
    "clearpage": "",
    "pagebreak": "o",
    "linebreak": "o",
    "vspace": "s r",
    "hspace": "s r",
    "bibliography": "r",
    "bibliographystyle": "r",
    "addbibresource": "o r",
    "printbibliography": "o",
    "nobibliography": "r",
    # ceurart / ACM / Springer LNCS class metadata (post-\begin{document}).
    "copyrightyear": "r",
    "copyrightclause": "r",
    "setcopyright": "r",
    "acmConference": "o r r r",
    "acmYear": "r",
    "acmISBN": "r",
    "acmDOI": "r",
    "acmJournal": "r",
    "acmVolume": "r",
    "acmNumber": "r",
    "ccsdesc": "o r",
    "keywords": "r",
    "authornote": "r",
    "authornotemark": "o",
    "titlenote": "r",
    "subtitle": "r",
    "institution": "r",
    "streetaddress": "r",
    "city": "r",
    "country": "r",
    "postcode": "r",
    "state": "r",
    "orcid": "r",
    "email": "r",
}

# Cite-command names recognised by the citation rewriter. Greedy regex handles
# the natbib \cite[a-zA-Z]* family in one shot; this set documents intent.
CITE_CMDS = {
    "cite", "citep", "citet", "citeauthor", "citeyear", "citeyearpar",
    "citealt", "citealp", "citenum", "citetext", "Citep", "Citet", "Citeauthor",
    "autocite", "Autocite", "textcite", "Textcite", "parencite", "Parencite",
    "footcite", "footcitetext", "smartcite", "Smartcite",
    "fullcite", "nocite",
}

CITE_RE = re.compile(
    r"\\(nocite|cite[a-zA-Z]*|[Aa]utocite|[Tt]extcite|[Pp]arencite|"
    r"footcite[a-zA-Z]*|smartcite|Smartcite|fullcite)"
    r"(\*?)"
    r"(\[[^\]]*\])?(\[[^\]]*\])?"
    r"\{([^{}]*)\}"
)

# Names that always count as "Acknowledgements" regardless of source spelling.
ACK_RE = re.compile(
    r"^\s*\\section\*?\s*\{\s*Acknowledg(?:e)?ments?\s*\}",
    re.IGNORECASE,
)

# Definition-of-a-macro markers used to detect a "pure macro file" (no body).
PURE_MACRO_MARKERS = re.compile(
    r"\\(newcommand|renewcommand|providecommand|DeclareMathOperator|"
    r"newtheorem|newenvironment|renewenvironment|newcounter|newlength|"
    r"newdimen|let|def|edef|gdef|xdef|RequirePackage|usepackage|"
    r"PassOptionsToPackage|input|include)\b"
)

# Markers that indicate a file is a TikZ/diagram asset (treated as a figure).
TIKZ_MARKERS = re.compile(
    r"\\(begin\{tikzpicture\}|tikz\b|begin\{forest\}|begin\{circuitikz\})"
)

# Standard LaTeX command names that any document uses regardless of which
# .sty redefines them. Skip when reporting vendored-package usage.
_STANDARD_LATEX_NAMES: set[str] = {
    "section", "subsection", "subsubsection", "paragraph", "subparagraph",
    "chapter", "part", "title", "author", "date", "maketitle", "thanks",
    "begin", "end", "item", "label", "ref", "cite", "footnote",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SectionUnit:
    """One section/appendix file to write under papers/YYMM/."""
    bucket: str          # "sections" | "appendices"
    slug: str            # filename stem
    text: str            # full file content including its \section header
    is_acknowledgements: bool = False


@dataclass
class FigureRef:
    """A figure file to copy and rewrite references for."""
    raw_path: str        # path as it appeared inside \includegraphics{...}
    src: Path            # resolved absolute source path
    dst_rel: str         # path under papers/YYMM/ (e.g. "figs/classic/foo.jpg")


@dataclass
class AssetRef:
    """A non-section \\input asset (TikZ diagram, table, snippet)."""
    raw_path: str
    src: Path
    dst_rel: str         # path under papers/YYMM/ (e.g. "figs/training_phases.tex")
    is_tikz: bool


@dataclass
class ExtractionPlan:
    layout: str                       # "modular" | "monolithic" | "mixed"
    abstract_prose: str = ""
    units: list[SectionUnit] = field(default_factory=list)
    figures: list[FigureRef] = field(default_factory=list)
    assets: list[AssetRef] = field(default_factory=list)
    graphicspath_dirs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dropped_inputs: list[str] = field(default_factory=list)
    # Populated after rewrite passes:
    cite_rewrites: int = 0
    cite_unchanged: int = 0
    cite_unknown: list[str] = field(default_factory=list)
    macro_rewrites: dict[str, int] = field(default_factory=dict)
    macro_unknown: list[str] = field(default_factory=list)
    vendored_warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Argument-consumer helpers (sit on top of paper_parsing's primitives)
# ---------------------------------------------------------------------------

def _consume_brace_arg(text: str, pos: int) -> tuple[int, str | None]:
    """Skip whitespace/comments, then if next char is '{', read its inner text.
    Returns (position-after-closing-brace, inner-text-stripped) or (pos, None)."""
    k = skip_ws_and_comments(text, pos)
    if k >= len(text) or text[k] != "{":
        return pos, None
    end = skip_balanced_braces(text, k)
    return end, text[k + 1 : end - 1].strip()


def _consume_optional_star(text: str, pos: int) -> tuple[int, bool]:
    k = skip_ws_and_comments(text, pos)
    if k < len(text) and text[k] == "*":
        return k + 1, True
    return pos, False


def _parse_graphicspath(text: str, pos: int) -> tuple[int, list[str]]:
    """\\graphicspath{ {dir1/} {dir2/} ... } — returns (end, [dir1, dir2, ...])."""
    end, inner = _consume_brace_arg(text, pos)
    if inner is None:
        return pos, []
    dirs: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c.isspace():
            i += 1
            continue
        if c == "{":
            j = skip_balanced_braces(inner, i)
            dirs.append(inner[i + 1 : j - 1].strip())
            i = j
        else:
            # Tolerant: bare path without inner braces — treat to end of token.
            j = i
            while j < n and not inner[j].isspace() and inner[j] != "{":
                j += 1
            dirs.append(inner[i:j].strip())
            i = j
    return end, dirs


# ---------------------------------------------------------------------------
# Body walker — emit events for the planner
# ---------------------------------------------------------------------------

def _split_at_begin_document(text: str) -> tuple[str, str]:
    """Return (preamble, body) splitting at first un-commented \\begin{document}.
    The body excludes \\begin{document} itself; also strips trailing
    \\end{document} and anything after."""
    n = len(text)
    i = 0
    body_start = -1
    while i < n:
        c = text[i]
        if c == "%":
            nl = text.find("\n", i)
            i = nl + 1 if nl >= 0 else n
            continue
        if text.startswith(r"\begin{document}", i):
            body_start = i + len(r"\begin{document}")
            break
        i += 1
    if body_start < 0:
        die("no \\begin{document} found")
    preamble = text[: i]
    # Find matching \end{document}
    j = body_start
    while j < n:
        c = text[j]
        if c == "%":
            nl = text.find("\n", j)
            j = nl + 1 if nl >= 0 else n
            continue
        if text.startswith(r"\end{document}", j):
            return preamble, text[body_start:j]
        j += 1
    return preamble, text[body_start:]


def _extract_abstract(body: str) -> tuple[str, str]:
    """Find the first \\begin{abstract}...\\end{abstract} in the body.
    Return (abstract_prose, body_with_abstract_removed). If none, returns ('', body).
    Also handles \\abstract{...} form."""
    # Form 1: \begin{abstract}...\end{abstract}
    m_begin = re.search(r"\\begin\s*\{\s*abstract\s*\}", body)
    if m_begin:
        m_end = re.search(r"\\end\s*\{\s*abstract\s*\}", body[m_begin.end():])
        if m_end:
            end_pos = m_begin.end() + m_end.end()
            inner = body[m_begin.end() : m_begin.end() + m_end.start()]
            new_body = body[: m_begin.start()] + body[end_pos:]
            return inner.strip(), new_body
    # Form 2: \abstract{...} (some classes)
    m = re.search(r"\\abstract\s*\{", body)
    if m:
        end = skip_balanced_braces(body, m.end() - 1)
        inner = body[m.end() : end - 1]
        new_body = body[: m.start()] + body[end:]
        return inner.strip(), new_body
    return "", body


def _scan_graphicspath(text: str) -> tuple[str, list[str]]:
    """Find every un-commented \\graphicspath{{...}{...}}; strip from text,
    return collected dir prefixes."""
    out_dirs: list[str] = []
    out_parts: list[str] = []
    n = len(text)
    i = 0
    last = 0
    while i < n:
        c = text[i]
        if c == "%":
            nl = text.find("\n", i)
            i = nl + 1 if nl >= 0 else n
            continue
        if c != "\\":
            i += 1
            continue
        cmd_start = i
        j, cmd_name = read_control_seq(text, i)
        if cmd_name == "graphicspath":
            end, dirs = _parse_graphicspath(text, j)
            out_parts.append(text[last:cmd_start])
            out_dirs.extend(dirs)
            last = end
            i = end
            continue
        i = j
    out_parts.append(text[last:])
    return "".join(out_parts), out_dirs


# ---------------------------------------------------------------------------
# Walking events
# ---------------------------------------------------------------------------

@dataclass
class _Event:
    kind: str          # "TEXT" | "SECTION" | "APPENDIX" | "INPUT" | "DROP"
    raw: str           # the literal source span (for replay/debug)
    # Per-kind payload:
    level: str | None = None       # for SECTION: "section", "subsection", ...
    star: bool = False             # for SECTION
    title: str = ""                # for SECTION (verbatim, includes math)
    path: str | None = None        # for INPUT
    cmd: str | None = None         # for DROP/INPUT


def _walk_body(text: str) -> list[_Event]:
    events: list[_Event] = []
    n = len(text)
    i = 0
    last_text = 0

    def flush_text(upto: int) -> None:
        nonlocal last_text
        if upto > last_text:
            chunk = text[last_text:upto]
            if chunk:
                events.append(_Event(kind="TEXT", raw=chunk))
        last_text = upto

    while i < n:
        c = text[i]
        if c == "%":
            nl = text.find("\n", i)
            i = nl + 1 if nl >= 0 else n
            continue
        if c != "\\":
            i += 1
            continue
        cmd_start = i
        j, cmd_name = read_control_seq(text, i)
        if not cmd_name:
            i = j
            continue

        if cmd_name in HEADING_CMDS:
            end_after_star, star = _consume_optional_star(text, j)
            end, title = _consume_brace_arg(text, end_after_star)
            if title is None:
                # malformed section — leave as text
                i = j
                continue
            flush_text(cmd_start)
            events.append(_Event(
                kind="SECTION", raw=text[cmd_start:end],
                level=cmd_name, star=star, title=title,
            ))
            last_text = end
            i = end
            continue

        if cmd_name == "appendix":
            flush_text(cmd_start)
            events.append(_Event(kind="APPENDIX", raw=text[cmd_start:j]))
            last_text = j
            i = j
            continue

        if cmd_name in INPUT_CMDS:
            end, path = _consume_brace_arg(text, j)
            if path is None:
                i = j
                continue
            flush_text(cmd_start)
            events.append(_Event(
                kind="INPUT", raw=text[cmd_start:end],
                cmd=cmd_name, path=path,
            ))
            last_text = end
            i = end
            continue

        if cmd_name in DROP_PATTERNS:
            end, _ = consume_pattern(text, j, DROP_PATTERNS[cmd_name])
            flush_text(cmd_start)
            events.append(_Event(
                kind="DROP", raw=text[cmd_start:end], cmd=cmd_name,
            ))
            last_text = end
            i = end
            continue

        # Unknown command: keep in TEXT, advance past name only.
        i = j

    flush_text(n)
    return events


# ---------------------------------------------------------------------------
# Input-file resolution and classification
# ---------------------------------------------------------------------------

def _resolve_input_path(paper_dir: Path, path: str, extra_dirs: list[str]) -> Path | None:
    """Resolve an \\input{path} (extension-optional) against paper_dir + extra_dirs."""
    candidates = [paper_dir / path]
    if not path.lower().endswith(".tex"):
        candidates.append(paper_dir / (path + ".tex"))
    for d in extra_dirs:
        candidates.append(paper_dir / d / path)
        if not path.lower().endswith(".tex"):
            candidates.append(paper_dir / d / (path + ".tex"))
    for c in candidates:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def _file_is_pure_macros(content: str) -> bool:
    """A file is 'pure macros' if every non-blank, non-comment line is a macro
    or package definition. Used to drop \\input{macros}, \\input{math_commands},
    \\input{custom_latex} from the body — they're already in papers-macros.tex."""
    has_content = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%"):
            continue
        has_content = True
        # Allow brace-only lines (continuations) and \makeatletter / \makeatother.
        if line in ("{", "}", "\\makeatletter", "\\makeatother"):
            continue
        if line.startswith("\\"):
            head = re.match(r"\\([A-Za-z@]+)", line)
            if head and PURE_MACRO_MARKERS.match("\\" + head.group(1)):
                continue
            # Tolerate continuation lines that start with backslash but are
            # arguments of a definition (e.g. a multi-line \newcommand body).
            return False
        return False
    return has_content


def _file_is_section_like(content: str) -> bool:
    """A file is a 'section as a file' if its first non-whitespace, non-comment
    line starts with \\section or \\chapter. We deliberately exclude
    \\subsection — a paper's section file may start with subsections that
    live INSIDE the parent's \\section, not as a top-level header."""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%"):
            continue
        return bool(re.match(r"\\(section|chapter)\*?\s*\{", line))
    return False


def _file_is_tikz_like(content: str) -> bool:
    """A file is a 'tikz/diagram asset' if it contains a tikzpicture/forest
    environment and no \\section command."""
    if re.search(r"\\section\*?\s*\{", content):
        return False
    return bool(TIKZ_MARKERS.search(content))


def _slugify(text: str) -> str:
    """Slug a section title or filename into [a-z0-9-]+ form."""
    s = text
    # Strip math
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"\\\([^)]*\\\)", " ", s)
    # Expand simple \textit{x} / \textbf{x} / \mathrm{x} / \emph{x}: keep inner
    s = re.sub(
        r"\\(text[a-z]+|emph|mathrm|mathbf|mathit|mathsf|operatorname)\s*\{([^{}]*)\}",
        r"\2", s,
    )
    # Drop other LaTeX commands and braces
    s = re.sub(r"\\[A-Za-z@]+\*?\s*(\[[^\]]*\])?", " ", s)
    s = s.replace("{", " ").replace("}", " ")
    s = s.lower()
    # Non-alnum → '-'
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "section"


def _dedupe_slug(slug: str, used: set[str]) -> str:
    if slug not in used:
        used.add(slug)
        return slug
    i = 2
    while f"{slug}-{i}" in used:
        i += 1
    out = f"{slug}-{i}"
    used.add(out)
    return out


# ---------------------------------------------------------------------------
# Planner — consume events, produce SectionUnits + asset registrations
# ---------------------------------------------------------------------------

@dataclass
class _Buffer:
    """A pending section unit being accumulated."""
    header: str | None = None        # the \section{...} verbatim (or None)
    starred: bool = False
    slug_hint: str | None = None     # if no inline header, slug derived from filename
    parts: list[str] = field(default_factory=list)
    from_file: bool = False          # True if an \input file supplied the body

    def append_text(self, chunk: str) -> None:
        self.parts.append(chunk)

    def is_empty(self) -> bool:
        if self.header is not None:
            return False
        return not _has_real_body(self.parts)


_LABEL_RE = re.compile(r"\\(label|index)\s*\{[^}]*\}")
_COMMENT_RE = re.compile(r"%[^\n]*")


def _has_real_body(parts: list[str]) -> bool:
    """Treat \\label{...}, \\index{...}, comments, and whitespace as
    non-body. Used to decide whether an inline \\section header is still
    'unfilled' when an \\input arrives."""
    s = "".join(parts)
    s = _LABEL_RE.sub("", s)
    s = _COMMENT_RE.sub("", s)
    return bool(s.strip())


def _build_plan(paper_dir: Path, body_text: str) -> ExtractionPlan:
    """Walk events and accumulate section units, figure refs, and assets."""
    plan = ExtractionPlan(layout="modular")
    events = _walk_body(body_text)

    in_appendix = False
    # used_slugs keyed per-bucket so sections/proofs and appendices/proofs can coexist.
    used_slugs: dict[str, set[str]] = {"sections": set(), "appendices": set()}
    cur = _Buffer()

    # Drop everything before the first SECTION/APPENDIX/INPUT event. This is
    # class-specific front matter (\title/\author/\address/\cormark/\sep/etc.)
    # that the article-class standalone driver doesn't understand. Abstract
    # was already extracted from body_text before we got here.
    first_content = next(
        (i for i, ev in enumerate(events)
         if ev.kind in ("SECTION", "APPENDIX", "INPUT")),
        len(events),
    )
    dropped_front = "".join(ev.raw for ev in events[:first_content] if ev.kind == "TEXT")
    if dropped_front.strip():
        commands_seen = sorted({
            m.group(0)
            for m in re.finditer(r"\\[A-Za-z@]+", dropped_front)
        })
        if commands_seen:
            plan.warnings.append(
                f"dropped {len(dropped_front.splitlines())} lines of front matter "
                f"before first section (commands: {', '.join(commands_seen[:8])}"
                f"{'...' if len(commands_seen) > 8 else ''})"
            )
    events = events[first_content:]

    def finalise() -> None:
        nonlocal cur
        if cur.is_empty():
            cur = _Buffer()
            return
        bucket = "appendices" if in_appendix else "sections"
        body = "".join(cur.parts).strip()
        # Determine header text. If the body already starts with a \section,
        # don't double up.
        body_starts_with_section = bool(
            re.match(r"\s*\\(section|chapter|subsection)\*?\s*\{", body)
        )
        if cur.header is not None and not body_starts_with_section:
            header = cur.header.rstrip()
        else:
            header = ""
        # Slug
        slug_base = cur.slug_hint or "section"
        if not slug_base:
            slug_base = "section"
        # Acknowledgements canonicalisation
        ack = False
        probe = (header + " " + body).lstrip()
        if re.search(
            r"\\section\*?\s*\{\s*Acknowledg(?:e)?ments?\s*\}",
            probe, re.IGNORECASE,
        ) or "acknowledg" in slug_base:
            ack = True
            slug_base = "acknowledgements"
            body = re.sub(
                r"\\section\*?\s*\{\s*Acknowledg(?:e)?ments?\s*\}",
                "", body, count=1, flags=re.IGNORECASE,
            ).lstrip()
            header = r"\section*{Acknowledgements}"
        slug = _dedupe_slug(slug_base, used_slugs[bucket])
        text_out = (header + "\n\n" + body).strip() + "\n"
        plan.units.append(SectionUnit(
            bucket=bucket, slug=slug, text=text_out, is_acknowledgements=ack,
        ))
        cur = _Buffer()

    saw_file_unit = False
    saw_inline_unit = False

    for ev in events:
        if ev.kind == "TEXT":
            cur.append_text(ev.raw)
            continue
        if ev.kind == "DROP":
            plan.dropped_inputs.append(ev.cmd or "")
            continue
        if ev.kind == "APPENDIX":
            if cur.header is not None or cur.parts:
                if not cur.from_file and _has_real_body(cur.parts):
                    saw_inline_unit = True
                elif cur.from_file:
                    saw_file_unit = True
            finalise()
            in_appendix = True
            continue
        if ev.kind == "SECTION":
            if cur.header is not None or cur.parts:
                if not cur.from_file and _has_real_body(cur.parts):
                    saw_inline_unit = True
                elif cur.from_file:
                    saw_file_unit = True
            finalise()
            cur = _Buffer(
                header=ev.raw, starred=ev.star,
                slug_hint=_slugify(ev.title),
            )
            continue
        if ev.kind == "INPUT":
            path = ev.path or ""
            resolved = _resolve_input_path(paper_dir, path, plan.graphicspath_dirs)
            if resolved is None:
                plan.warnings.append(f"unresolved \\{ev.cmd}{{{path}}}")
                continue
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                plan.warnings.append(f"could not read {resolved}: {e}")
                continue

            if _file_is_pure_macros(content):
                plan.dropped_inputs.append(f"\\{ev.cmd}{{{path}}} (pure-macros)")
                continue

            if _file_is_tikz_like(content):
                asset_stem = Path(path).stem
                plan.assets.append(AssetRef(
                    raw_path=path, src=resolved,
                    dst_rel=f"figs/{asset_stem}.tex", is_tikz=True,
                ))
                cur.append_text(f"\\input{{figs/{asset_stem}}}")
                continue

            # General prose / section content.
            file_starts_with_section = _file_is_section_like(content)
            if (
                cur.header is not None
                and not _has_real_body(cur.parts)
                and not file_starts_with_section
            ):
                # Modular pattern: inline \section{X} \label{} \input{x} →
                # the file IS the body.
                cur.append_text(content)
                cur.from_file = True
            else:
                if cur.header is not None or cur.parts:
                    if not cur.from_file and _has_real_body(cur.parts):
                        saw_inline_unit = True
                    elif cur.from_file:
                        saw_file_unit = True
                finalise()
                cur = _Buffer(slug_hint=_slugify(Path(path).stem))
                cur.append_text(content)
                cur.from_file = True
            continue

    if cur.header is not None or cur.parts:
        if not cur.from_file and _has_real_body(cur.parts):
            saw_inline_unit = True
        elif cur.from_file:
            saw_file_unit = True
    finalise()

    if saw_file_unit and not saw_inline_unit:
        plan.layout = "modular"
    elif saw_inline_unit and not saw_file_unit:
        plan.layout = "monolithic"
    elif saw_file_unit and saw_inline_unit:
        plan.layout = "mixed"
    else:
        plan.layout = "modular"

    return plan


# ---------------------------------------------------------------------------
# Figure discovery and path rewriting
# ---------------------------------------------------------------------------

FIG_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".tikz")

INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"
)


def _resolve_figure_path(
    paper_dir: Path,
    raw_path: str,
    graphicspath_dirs: list[str],
) -> Path | None:
    """Resolve an \\includegraphics{raw_path} against paper_dir + graphicspath.

    pdflatex resolves images without insisting on an extension, so the raw
    path may be 'figs/foo' OR 'figs/foo.pdf' OR even 'figs/foo_noise0.001'
    where the literal '.001' is part of the stem, not an extension. We
    try the path verbatim first, then for each candidate base, try appending
    each known extension to the FULL raw path."""
    bases = [paper_dir] + [paper_dir / d for d in graphicspath_dirs]
    for base in bases:
        verbatim = base / raw_path
        try:
            if verbatim.is_file():
                return verbatim.resolve()
        except OSError:
            pass
        for ext in FIG_EXTS:
            candidate = base / (raw_path + ext)
            try:
                if candidate.is_file():
                    return candidate.resolve()
            except OSError:
                continue
    return None


def _figure_dst_rel(raw_path: str, src: Path, paper_dir: Path) -> str:
    """Compute destination relpath under papers/YYMM/figs/ given the raw path
    and the resolved source file. Strip a leading 'figures/' or 'figs/' from
    the raw_path; the rest mirrors under figs/. Extension comes from the
    resolved source file (raw_path stems may contain dots that aren't
    extensions — e.g. 'noise0.001')."""
    rp = raw_path
    if rp.startswith("./"):
        rp = rp[2:]
    for prefix in ("figures/", "figs/"):
        if rp.startswith(prefix):
            rp = rp[len(prefix):]
            break
    if not rp.lower().endswith(src.suffix.lower()):
        rp = rp + src.suffix
    return "figs/" + rp


def _discover_figures(paper_dir: Path, plan: ExtractionPlan) -> dict[str, str]:
    """Scan every unit and asset for \\includegraphics; build raw_path → dst_rel
    map. Populates plan.figures with copy plans."""
    mapping: dict[str, str] = {}
    seen_src: set[Path] = set()

    def visit(text: str) -> None:
        for m in INCLUDEGRAPHICS_RE.finditer(text):
            raw = m.group(1).strip()
            if raw in mapping:
                continue
            src = _resolve_figure_path(paper_dir, raw, plan.graphicspath_dirs)
            if src is None:
                plan.warnings.append(f"unresolved figure: {raw}")
                continue
            dst_rel = _figure_dst_rel(raw, src, paper_dir)
            mapping[raw] = dst_rel
            if src not in seen_src:
                plan.figures.append(FigureRef(
                    raw_path=raw, src=src, dst_rel=dst_rel,
                ))
                seen_src.add(src)

    for u in plan.units:
        visit(u.text)
    visit(plan.abstract_prose)
    # Also visit asset files (their content will get rewritten too).
    for a in plan.assets:
        try:
            visit(a.src.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return mapping


def _rewrite_figure_paths(text: str, mapping: dict[str, str]) -> str:
    """Replace every \\includegraphics{raw} with \\includegraphics{dst-no-ext}.

    The required path argument is captured as group 1; replace exactly that
    span using its absolute positions, so any inner braces inside the
    optional [...] (e.g. trim={0 8 0 0}) are preserved verbatim."""
    def repl(m: re.Match) -> str:
        raw = m.group(1).strip()
        if raw not in mapping:
            return m.group(0)
        dst = mapping[raw]
        if Path(dst).suffix in FIG_EXTS:
            dst = str(Path(dst).with_suffix(""))
        s = m.group(0)
        match_start = m.start()
        before_path = s[: m.start(1) - match_start]
        after_path = s[m.end(1) - match_start :]
        return before_path + dst + after_path
    return INCLUDEGRAPHICS_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Bibliography-in-body stripper
# ---------------------------------------------------------------------------

BIB_BODY_RE = re.compile(
    r"\\(bibliography|bibliographystyle|addbibresource|printbibliography|nobibliography)\b"
    r"(\s*\[[^\]]*\])?(\s*\{[^}]*\})?"
)


def _strip_bib_commands(text: str) -> str:
    return BIB_BODY_RE.sub("", text)


# ---------------------------------------------------------------------------
# Citation rewriter
# ---------------------------------------------------------------------------

def _load_citation_map(yymm: str) -> dict[str, str]:
    """Return local_key → global_tag mapping for the paper's citation-map."""
    path = ARXIV_DIR / f"arXiv-{yymm}-citation-map.json"
    if not path.is_file():
        die(f"citation map not found: {path.relative_to(REPO_ROOT)} "
            f"(run merge-bibs first)")
    data = json.loads(path.read_text(encoding="utf-8"))
    mappings = data.get("mappings", {})
    out: dict[str, str] = {}
    for local, entry in mappings.items():
        global_tag = entry.get("global_tag") if isinstance(entry, dict) else None
        if global_tag:
            out[local] = global_tag
    return out


def _load_global_bib_keys() -> set[str]:
    if not GLOBAL_BIB.is_file():
        die(f"global bibliography not found: {GLOBAL_BIB.relative_to(REPO_ROOT)} "
            f"(run merge-bibs first)")
    text = GLOBAL_BIB.read_text(encoding="utf-8", errors="replace")
    keys: set[str] = set()
    for m in re.finditer(r"^@\w+\s*\{\s*([^,\s]+)\s*,", text, re.MULTILINE):
        keys.add(m.group(1))
    return keys


def _rewrite_citations(
    text: str,
    cmap: dict[str, str],
    global_keys: set[str],
    counters: dict[str, int],
    unknown_keys: set[str],
) -> str:
    """Rewrite every \\cite*{a,b,c} key per the citation map; warn on unknown."""
    def repl(m: re.Match) -> str:
        cmd = m.group(1)
        star = m.group(2) or ""
        opt1 = m.group(3) or ""
        opt2 = m.group(4) or ""
        keys_str = m.group(5)
        new_keys = []
        for k in keys_str.split(","):
            k = k.strip()
            if not k:
                continue
            if k in cmap:
                new_keys.append(cmap[k])
                counters["rewritten"] = counters.get("rewritten", 0) + 1
            else:
                new_keys.append(k)
                if k in global_keys:
                    counters["unchanged"] = counters.get("unchanged", 0) + 1
                else:
                    unknown_keys.add(k)
        return f"\\{cmd}{star}{opt1}{opt2}{{{','.join(new_keys)}}}"
    return CITE_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Macro rewriter
# ---------------------------------------------------------------------------

def _load_macro_map(yymm: str) -> dict[str, str]:
    path = ARXIV_DIR / f"arXiv-{yymm}-macro-map.json"
    if not path.is_file():
        die(f"macro map not found: {path.relative_to(REPO_ROOT)} "
            f"(run merge-preambles first)")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.get("mappings", {}).items() if k != v}


def _rewrite_macros(
    text: str,
    mmap: dict[str, str],
    counts: dict[str, int],
) -> str:
    """Rename macros per mmap with LaTeX-letter word boundaries."""
    for local, global_ in mmap.items():
        pattern = re.compile(rf"\\{re.escape(local)}(?![A-Za-z@])")
        new_text, n = pattern.subn(rf"\\{global_}", text)
        if n:
            counts[local] = counts.get(local, 0) + n
        text = new_text
    return text


# ---------------------------------------------------------------------------
# Vendored .sty / .cls usage detector
# ---------------------------------------------------------------------------

def _detect_vendored(paper_dir: Path) -> list[Path]:
    return sorted(
        p for p in paper_dir.iterdir()
        if p.is_file() and p.suffix in (".sty", ".cls")
    )


def _vendored_warnings(
    paper_dir: Path,
    vendored: list[Path],
    plan: ExtractionPlan,
) -> list[str]:
    warnings: list[str] = []
    if not vendored:
        return warnings
    # Concatenate all body content we'll write (rewritten units + abstract +
    # assets) and grep for command usage.
    body_corpus_parts = [plan.abstract_prose]
    for u in plan.units:
        body_corpus_parts.append(u.text)
    for a in plan.assets:
        try:
            body_corpus_parts.append(a.src.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    body_corpus = "\n".join(body_corpus_parts)

    for v in vendored:
        if v.suffix == ".cls":
            warnings.append(
                f"vendored class {v.name}: standalone driver loads "
                f"\\documentclass{{article}}; layout/formatting from the original "
                f"class will not be reproduced. Verify the PDF carefully."
            )
            continue
        # .sty — parse its definitions, grep for usage.
        try:
            result = scan_sidecar_full(paper_dir, v)
        except Exception as e:
            warnings.append(f"could not parse vendored {v.name}: {e}")
            continue
        names = sorted({d.name for d in result.macros if d.name})
        # Filter out standard LaTeX names every body uses regardless of which
        # .sty is in scope (the .sty redefines them for formatting, but their
        # presence in the body isn't evidence of substantive use).
        names = [n for n in names if n not in _STANDARD_LATEX_NAMES]
        if not names:
            continue
        used: list[str] = []
        for name in names:
            pat = re.compile(rf"\\{re.escape(name)}(?![A-Za-z@])")
            if pat.search(body_corpus):
                used.append(name)
        if used:
            preview = ", ".join(f"\\{n}" for n in used[:8])
            more = "" if len(used) <= 8 else f" (and {len(used) - 8} more)"
            warnings.append(
                f"vendored {v.name} defines commands used in the body: "
                f"{preview}{more}. Standalone build may need these in "
                f"papers-dependencies.tex/papers-macros.tex."
            )
    return warnings


# ---------------------------------------------------------------------------
# Standalone driver content
# ---------------------------------------------------------------------------

STANDALONE_DRIVER_TEXT = r"""% Auto-generated by scripts/paper_extraction.py — do not hand-edit.
% Build one paper from the repo root with:
%   latexmk -pdf -jobname=YYMM -outdir=build/YYMM papers/stand-alone-paper.tex
%
% Loads the thesis-wide preamble + bibliography, picks the paper via
% \jobname, and uses biblatex (authoryear-comp) with the biber backend.
\documentclass[11pt]{article}

\input{papers-dependencies.tex}
\input{papers-macros.tex}

% cleveref must load after hyperref (which is in papers-dependencies.tex,
% but skipped from auto-loading because of the ordering constraint).
\usepackage[capitalize,noabbrev]{cleveref}

% TikZ library loads — 2602's pipelines_figure asset depends on these
% (the libraries were in 2602's main.tex preamble, which the extractor
% strips). Idempotent for papers that don't use TikZ. Long-term, these
% should be extracted into papers-dependencies.tex by paper_parsing.
\usetikzlibrary{arrows.meta,positioning,calc}

\usepackage{import}
\usepackage[backend=biber,style=authoryear-comp,natbib=true]{biblatex}
\addbibresource{papers-bibliography.bib}

\graphicspath{{papers/\jobname/}}

\title{Paper \jobname}
\author{Luca Arnaboldi \& collaborators}

\begin{document}
\maketitle
\begin{abstract}
\subimport{papers/\jobname/}{abstract}
\end{abstract}
\subimport{papers/\jobname/}{main}
\printbibliography
\end{document}
"""


def _ensure_standalone_driver() -> bool:
    """Write papers/stand-alone-paper.tex if missing or stale. Returns True
    if the file was (re)written."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    if STANDALONE_DRIVER.is_file():
        current = STANDALONE_DRIVER.read_text(encoding="utf-8")
        if current == STANDALONE_DRIVER_TEXT:
            return False
    write_atomic(STANDALONE_DRIVER, STANDALONE_DRIVER_TEXT)
    return True


# ---------------------------------------------------------------------------
# Verification compile
# ---------------------------------------------------------------------------

LOG_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("hard_errors",     re.compile(r"^! |^LaTeX Error:|^Emergency stop|^Fatal error|^Runaway argument", re.MULTILINE)),
    ("undefined_cs",    re.compile(r"Undefined control sequence")),
    ("undefined_refs",  re.compile(r"LaTeX Warning: Reference .* undefined")),
    ("undefined_cites", re.compile(r"(Citation .* undefined|Empty bibliography)")),
    ("missing_files",   re.compile(r"File `[^']+' not found|Package pdftex\.def Error")),
    ("package_clashes", re.compile(r"Option clash for package|biblatex.*natbib|natbib.*biblatex")),
    ("overfull",        re.compile(r"^Overfull \\[hv]box", re.MULTILINE)),
    ("underfull",       re.compile(r"^Underfull \\[hv]box", re.MULTILINE)),
]


def _run_verification_compile(yymm: str) -> dict[str, object]:
    """Run latexmk on the standalone driver for this paper. Returns a dict
    with: returncode, log_path, pdf_path, categories (name → count or list)."""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    outdir = BUILD_DIR / yymm
    outdir.mkdir(parents=True, exist_ok=True)
    # LuaLaTeX handles Unicode natively (notably combining diacritics like
    # NFD ï = ı + U+0308 that biber emits into the .bbl), which pdflatex's
    # utf8 inputenc cannot handle without per-character \DeclareUnicodeCharacter
    # mappings. The standalone driver works with either engine.
    cmd = [
        "latexmk", "-lualatex", "-interaction=nonstopmode",
        f"-jobname={yymm}", f"-outdir={outdir}",
        str(STANDALONE_DRIVER.relative_to(REPO_ROOT)),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True,
            errors="replace", timeout=300,
        )
    except FileNotFoundError:
        return {"returncode": -1, "error": "latexmk not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"returncode": -2, "error": "latexmk timed out after 300s"}

    log_path = outdir / f"{yymm}.log"
    pdf_path = outdir / f"{yymm}.pdf"
    result: dict[str, object] = {
        "returncode": proc.returncode,
        "log_path": str(log_path.relative_to(REPO_ROOT)) if log_path.is_file() else None,
        "pdf_path": str(pdf_path.relative_to(REPO_ROOT)) if pdf_path.is_file() else None,
        "categories": {},
        "first_hard_error": None,
    }
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        cats: dict[str, int] = {}
        for name, pat in LOG_CATEGORIES:
            cats[name] = len(pat.findall(log_text))
        result["categories"] = cats
        # Snippet of first hard error.
        m = LOG_CATEGORIES[0][1].search(log_text)
        if m:
            start = m.start()
            end = log_text.find("\n", start)
            for _ in range(3):
                if end < 0:
                    break
                end = log_text.find("\n", end + 1)
            if end < 0:
                end = len(log_text)
            result["first_hard_error"] = log_text[start:end].strip()
    return result


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

def _ensure_build_gitignored() -> bool:
    """Warn (but do not error) if build/ is not in .gitignore. Append it if
    .gitignore exists and doesn't already mention it."""
    gi = REPO_ROOT / ".gitignore"
    if not gi.is_file():
        return False
    text = gi.read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if s in ("build", "build/", "/build", "/build/"):
            return False
    write_atomic(gi, text.rstrip() + "\nbuild/\n")
    return True


def _write_plan_to_disk(
    plan: ExtractionPlan,
    out_dir: Path,
    fig_map: dict[str, str],
    cmap: dict[str, str],
    mmap: dict[str, str],
    global_bib_keys: set[str],
) -> None:
    """Apply final rewrites and write all files under out_dir."""
    cite_counters: dict[str, int] = {}
    unknown_cites: set[str] = set()
    macro_counts: dict[str, int] = {}

    def finalize_text(text: str) -> str:
        text = _strip_bib_commands(text)
        text = _rewrite_figure_paths(text, fig_map)
        text = _rewrite_citations(text, cmap, global_bib_keys, cite_counters, unknown_cites)
        text = _rewrite_macros(text, mmap, macro_counts)
        return text

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sections").mkdir(exist_ok=True)
    (out_dir / "appendices").mkdir(exist_ok=True)
    (out_dir / "figs").mkdir(exist_ok=True)

    # abstract.tex
    abstract_final = finalize_text(plan.abstract_prose).strip() + "\n"
    write_atomic(out_dir / "abstract.tex", abstract_final)

    # section/appendix files
    main_lines = ["% Auto-generated by scripts/paper_extraction.py.\n"]
    section_units = [u for u in plan.units if u.bucket == "sections"]
    appendix_units = [u for u in plan.units if u.bucket == "appendices"]
    for u in section_units:
        path = out_dir / "sections" / f"{u.slug}.tex"
        write_atomic(path, finalize_text(u.text))
        main_lines.append(f"\\input{{sections/{u.slug}}}\n")
    if appendix_units:
        main_lines.append("\n\\appendix\n")
        for u in appendix_units:
            path = out_dir / "appendices" / f"{u.slug}.tex"
            write_atomic(path, finalize_text(u.text))
            main_lines.append(f"\\input{{appendices/{u.slug}}}\n")
    write_atomic(out_dir / "main.tex", "".join(main_lines))

    # Figures
    for f in plan.figures:
        dst = out_dir / f.dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f.src, dst)

    # Assets (recursively rewrite their content too)
    for a in plan.assets:
        try:
            content = a.src.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            plan.warnings.append(f"could not read asset {a.src}: {e}")
            continue
        content = finalize_text(content)
        dst = out_dir / a.dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(dst, content)

    # Stash counts into the plan for the final report.
    plan.cite_rewrites = cite_counters.get("rewritten", 0)
    plan.cite_unchanged = cite_counters.get("unchanged", 0)
    plan.cite_unknown = sorted(unknown_cites)
    plan.macro_rewrites = macro_counts


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_report(
    paper_dir: Path,
    main_tex: Path,
    yymm: str,
    out_dir: Path,
    plan: ExtractionPlan,
    compile_result: dict[str, object] | None,
    dry_run: bool,
) -> None:
    prefix = "[dry-run] " if dry_run else ""
    print(f"\n## extract-paper: {paper_dir.name} → "
          f"{out_dir.relative_to(REPO_ROOT)}/")
    print(f"- Main tex: {main_tex.name}  (layout: {plan.layout})")
    sec = [u for u in plan.units if u.bucket == "sections"]
    app = [u for u in plan.units if u.bucket == "appendices"]
    print(
        f"- Files: abstract.tex, main.tex, "
        f"{len(sec)} section{'s' if len(sec) != 1 else ''}, "
        f"{len(app)} appendi{'ces' if len(app) != 1 else 'x'}, "
        f"{len(plan.figures)} figure{'s' if len(plan.figures) != 1 else ''}, "
        f"{len(plan.assets)} asset{'s' if len(plan.assets) != 1 else ''}"
    )
    print(
        f"- Citations: {plan.cite_rewrites} rewritten, "
        f"{plan.cite_unchanged} already-global, "
        f"{len(plan.cite_unknown)} unknown"
    )
    if plan.cite_unknown:
        preview = ", ".join(plan.cite_unknown[:5])
        more = "" if len(plan.cite_unknown) <= 5 else f" (+{len(plan.cite_unknown) - 5} more)"
        print(f"  unknown: {preview}{more}")
    if plan.macro_rewrites:
        items = ", ".join(
            f"\\{local} ({n})" for local, n in plan.macro_rewrites.items()
        )
        print(f"- Macros rewritten: {items}")
    else:
        print("- Macros rewritten: (none)")
    if plan.vendored_warnings:
        print(f"- Vendored ({len(plan.vendored_warnings)} warning(s)):")
        for w in plan.vendored_warnings:
            print(f"    {w}")
    else:
        print("- Vendored: (none)")
    if plan.warnings:
        print(f"- Warnings ({len(plan.warnings)}):")
        for w in plan.warnings[:10]:
            print(f"    {w}")
        if len(plan.warnings) > 10:
            print(f"    ... (+{len(plan.warnings) - 10} more)")
    if dry_run:
        print("- (dry-run: no files written)")
        return
    if compile_result is None:
        print("- Compile: skipped (--no-compile)")
        return
    rc = compile_result.get("returncode", -1)
    cats = compile_result.get("categories", {}) or {}
    pdf_path = compile_result.get("pdf_path")
    hard_errors = cats.get("hard_errors", 0)
    # latexmk returns non-zero from a subprocess pipe even when a clean PDF
    # got written (e.g. early-pass warnings about undefined refs that get
    # resolved on the next pass). Trust the artefacts + categorised log:
    # a PDF exists and the log has zero hard errors → the build is clean.
    err = compile_result.get("error")
    success = err is None and pdf_path is not None and hard_errors == 0
    if success:
        overfull = cats.get("overfull", 0) + cats.get("underfull", 0)
        print(f"- Compile: ok ({pdf_path})")
        if overfull:
            print(f"  warnings: {cats.get('overfull', 0)} overfull, {cats.get('underfull', 0)} underfull boxes")
        return
    if err:
        print(f"- Compile: FAILED — {err}")
    elif pdf_path is None:
        print(f"- Compile: FAILED (no PDF; returncode={rc}; see {compile_result.get('log_path')})")
    else:
        # PDF exists but the log has hard errors — partial render.
        print(
            f"- Compile: PARTIAL ({pdf_path}; {hard_errors} hard error(s) in log, "
            f"see {compile_result.get('log_path')})"
        )
    hard = compile_result.get("first_hard_error")
    if hard:
        print("  first hard error:")
        for line in str(hard).splitlines():
            print(f"    {line}")
    if cats:
        counts = ", ".join(f"{k}={v}" for k, v in cats.items() if v)
        if counts:
            print(f"  log: {counts}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paper",
                   help="Paper id: folder name (arXiv-2302.05882v1), YYMM (2302 / arXiv-2302), "
                        "or arXiv id (2302.05882[v1]).")
    p.add_argument("--force", action="store_true",
                   help="Overwrite an existing papers/YYMM/ directory.")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan and report without writing any output files.")
    p.add_argument("--no-compile", action="store_true",
                   help="Skip the verification latexmk compile after writing.")
    p.add_argument("--arxiv-dir", type=Path, default=ARXIV_DIR,
                   help=f"Directory containing the unpacked papers (default: {ARXIV_DIR}).")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    paper_dir = resolve_paper_folder(args.arxiv_dir, args.paper)
    yymm = yymm_of(paper_dir)
    out_dir = PAPERS_DIR / yymm

    if out_dir.exists() and not args.force and not args.dry_run:
        die(
            f"{out_dir.relative_to(REPO_ROOT)} already exists; "
            f"pass --force to overwrite or --dry-run to preview."
        )

    main_tex = find_main_tex(paper_dir)
    text = main_tex.read_text(encoding="utf-8", errors="replace")
    _, body = _split_at_begin_document(text)

    # Strip \graphicspath from preamble AND body, collect dirs.
    preamble = text[: text.find(r"\begin{document}")]
    _, preamble_dirs = _scan_graphicspath(preamble)
    body, body_dirs = _scan_graphicspath(body)

    abstract_prose, body_no_abs = _extract_abstract(body)

    plan = _build_plan(paper_dir, body_no_abs)
    plan.abstract_prose = abstract_prose
    plan.graphicspath_dirs = preamble_dirs + body_dirs

    # Maps (require merge-bibs / merge-preambles to have been run).
    cmap = _load_citation_map(yymm)
    mmap = _load_macro_map(yymm)
    global_bib_keys = _load_global_bib_keys()

    # Discover figures (after units exist; needs graphicspath).
    fig_map = _discover_figures(paper_dir, plan)

    # Vendored detection
    vendored = _detect_vendored(paper_dir)
    plan.vendored_warnings = _vendored_warnings(paper_dir, vendored, plan)

    # Ensure standalone driver exists (idempotent).
    if not args.dry_run:
        rewrote = _ensure_standalone_driver()
        if rewrote:
            print(f"[write]   {STANDALONE_DRIVER.relative_to(REPO_ROOT)}", file=sys.stderr)
        if _ensure_build_gitignored():
            print(f"[update]  .gitignore (added 'build/')", file=sys.stderr)

    # Write outputs (or skip if dry-run).
    if not args.dry_run:
        if out_dir.exists() and args.force:
            shutil.rmtree(out_dir)
        _write_plan_to_disk(plan, out_dir, fig_map, cmap, mmap, global_bib_keys)
        print(f"[write]   {out_dir.relative_to(REPO_ROOT)}/", file=sys.stderr)
    else:
        # Still apply rewrites to populate counters for the report.
        cite_counters: dict[str, int] = {}
        unknown_cites: set[str] = set()
        macro_counts: dict[str, int] = {}
        for u in plan.units:
            t = _strip_bib_commands(u.text)
            t = _rewrite_figure_paths(t, fig_map)
            t = _rewrite_citations(t, cmap, global_bib_keys, cite_counters, unknown_cites)
            _rewrite_macros(t, mmap, macro_counts)
        plan.cite_rewrites = cite_counters.get("rewritten", 0)
        plan.cite_unchanged = cite_counters.get("unchanged", 0)
        plan.cite_unknown = sorted(unknown_cites)
        plan.macro_rewrites = macro_counts

    # Verify compile (unless skipped).
    compile_result: dict[str, object] | None = None
    if not args.dry_run and not args.no_compile:
        compile_result = _run_verification_compile(yymm)

    _print_report(paper_dir, main_tex, yymm, out_dir, plan, compile_result, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
