"""Extract preamble packages and macros from one arXiv paper into sidecar .tex files.

Run with:
    uv run scripts/paper_parsing.py 2302
    uv run scripts/paper_parsing.py arXiv-2302.05882v1
    uv run scripts/paper_parsing.py 2302.05882 --dry-run

Outputs (per paper, at the top of arxiv-papers/):
    arXiv-YYMM-packages.tex   - \\usepackage, \\RequirePackage, \\PassOptionsToPackage
    arXiv-YYMM-macros.tex     - \\new(re)command, \\providecommand, \\def, \\let,
                                 \\DeclareMathOperator, \\new(re)environment,
                                 \\newtheorem, \\newcounter, \\newlength, \\newdimen

Intra-paper duplicate macro names are preserved in source order and flagged in
both the output .tex and the stderr summary. The companion arXiv-YYMM-metadata.json
file (plain-text title + Surname-Name authors) is curated by hand via the
parse-paper skill, not by this script.
"""

from __future__ import annotations

import argparse
import bisect
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARXIV_DIR = REPO_ROOT / "arxiv-papers"

PAPER_FOLDER_RE = re.compile(r"^arXiv-(\d{4})\.\d+v\d+$")

# ---------------------------------------------------------------------------
# Command tables
# ---------------------------------------------------------------------------
# Patterns are space-separated, scanned in order:
#   s -> optional star ("*")
#   o -> optional [...] group
#   r -> required {...} group (the FIRST 'r' yields the defined name)

PACKAGE_PATTERNS: dict[str, str] = {
    "usepackage":           "o r o",
    "RequirePackage":       "o r o",
    "PassOptionsToPackage": "r r",
}

MACRO_PATTERNS: dict[str, str] = {
    "newcommand":          "s r o o r",
    "renewcommand":        "s r o o r",
    "providecommand":      "s r o o r",
    "DeclareMathOperator": "s r r",
    "newenvironment":      "s r o o r r",
    "renewenvironment":    "s r o o r r",
    "newtheorem":          "s r o r o",
    "newcounter":          "r o",
    "newlength":           "r",
}

# Non-standard syntaxes, handled by dedicated consumers.
SPECIAL_DEFS = {"def", "edef", "gdef", "xdef"}
LET_COMMANDS = {"let"}
NEWDIMEN_COMMANDS = {"newdimen"}

ALL_MACRO_CMDS = (
    set(MACRO_PATTERNS) | SPECIAL_DEFS | LET_COMMANDS | NEWDIMEN_COMMANDS
)

# Output grouping for macros.tex. Lower bucket index emits first.
KIND_BUCKET: dict[str, int] = {
    "DeclareMathOperator": 0,
    "newcommand":   1, "renewcommand": 1, "providecommand": 1,
    "def": 1, "edef": 1, "gdef": 1, "xdef": 1, "let": 1,
    "newenvironment": 2, "renewenvironment": 2,
    "newtheorem": 3,
    "newcounter": 4, "newlength": 4, "newdimen": 4,
}

BUCKET_HEADERS: dict[int, str] = {
    0: "Math operators",
    1: "Commands",
    2: "Environments",
    3: "Theorems",
    4: "Counters & lengths",
}


# ---------------------------------------------------------------------------
# Paper-folder resolution
# ---------------------------------------------------------------------------

def resolve_paper_folder(arxiv_dir: Path, user_input: str) -> Path:
    """Resolve a user-supplied paper id to a unique unpacked folder.

    Accepts: full folder name, YYMM (with/without arXiv- prefix), arXiv id
    (with/without version). Mirrors the check-paper skill conventions.
    """
    if not arxiv_dir.is_dir():
        die(f"arxiv-papers/ not found at {arxiv_dir}")

    needle = user_input.strip()
    if needle.startswith("arXiv-"):
        needle = needle[len("arXiv-"):]
    needle = needle.lstrip("/")

    candidates = sorted(
        p for p in arxiv_dir.iterdir()
        if p.is_dir() and PAPER_FOLDER_RE.match(p.name)
    )

    def matches(folder: Path) -> bool:
        stem = folder.name[len("arXiv-"):]              # e.g. "2302.05882v1"
        yymm = stem.split(".", 1)[0]                    # e.g. "2302"
        no_ver = stem.rsplit("v", 1)[0]                 # e.g. "2302.05882"
        return needle in (folder.name, stem, yymm, no_ver)

    hits = [c for c in candidates if matches(c)]
    if not hits:
        die(f"no paper folder matches {user_input!r} under {arxiv_dir}")
    if len(hits) > 1:
        names = "\n  ".join(h.name for h in hits)
        die(f"multiple paper folders match {user_input!r}:\n  {names}")
    return hits[0]


def yymm_of(folder: Path) -> str:
    m = PAPER_FOLDER_RE.match(folder.name)
    if not m:
        die(f"unexpected folder name {folder.name!r}")
    return m.group(1)


# ---------------------------------------------------------------------------
# Main-tex detection
# ---------------------------------------------------------------------------

def find_main_tex(paper_dir: Path) -> Path:
    """Locate the main .tex of a paper, mirroring the check-paper heuristic."""
    top_tex = sorted(p for p in paper_dir.iterdir() if p.is_file() and p.suffix == ".tex")
    if not top_tex:
        die(f"no .tex files at top level of {paper_dir.name}/")

    # Prefer the .tex whose stem matches an existing .bbl.
    bbls = {p.stem for p in paper_dir.iterdir() if p.is_file() and p.suffix == ".bbl"}
    matching = [p for p in top_tex if p.stem in bbls]
    if len(matching) == 1:
        return matching[0]

    if len(top_tex) == 1:
        return top_tex[0]

    # Grep for \documentclass on top-level files.
    with_class = [
        p for p in top_tex
        if re.search(r"^\s*\\documentclass\b", p.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
    ]
    if len(with_class) == 1:
        return with_class[0]

    names = ", ".join(p.name for p in top_tex)
    die(
        f"ambiguous main .tex in {paper_dir.name}/ ({names}); "
        f"please disambiguate manually"
    )


# ---------------------------------------------------------------------------
# Source bookkeeping
# ---------------------------------------------------------------------------

@dataclass
class Source:
    relpath: str
    text: str
    line_starts: list[int] = field(default_factory=list)

    def line_at(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)


def make_source(paper_dir: Path, path: Path) -> Source:
    text = path.read_text(encoding="utf-8", errors="replace")
    starts = [0]
    for i, c in enumerate(text):
        if c == "\n":
            starts.append(i + 1)
    return Source(relpath=str(path.relative_to(paper_dir)), text=text, line_starts=starts)


# ---------------------------------------------------------------------------
# Low-level scanning
# ---------------------------------------------------------------------------

def skip_ws_and_comments(text: str, pos: int) -> int:
    n = len(text)
    while pos < n:
        c = text[pos]
        if c.isspace():
            pos += 1
        elif c == "%":
            nl = text.find("\n", pos)
            pos = (nl + 1) if nl >= 0 else n
        else:
            return pos
    return pos


def skip_balanced_braces(text: str, pos: int) -> int:
    """text[pos] must be '{'. Returns position right after matching '}'."""
    n = len(text)
    assert pos < n and text[pos] == "{"
    depth = 1
    pos += 1
    while pos < n and depth > 0:
        c = text[pos]
        if c == "\\" and pos + 1 < n:
            pos += 2
            continue
        if c == "%":
            nl = text.find("\n", pos)
            pos = (nl + 1) if nl >= 0 else n
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return pos


def skip_balanced_brackets(text: str, pos: int) -> int:
    """text[pos] must be '['. Returns position right after matching ']'.
    Nested {...} groups inside the brackets are skipped."""
    n = len(text)
    assert pos < n and text[pos] == "["
    pos += 1
    while pos < n:
        c = text[pos]
        if c == "\\" and pos + 1 < n:
            pos += 2
            continue
        if c == "%":
            nl = text.find("\n", pos)
            pos = (nl + 1) if nl >= 0 else n
            continue
        if c == "{":
            pos = skip_balanced_braces(text, pos)
            continue
        if c == "]":
            return pos + 1
        pos += 1
    return pos


def read_control_seq(text: str, pos: int) -> tuple[int, str]:
    """Starting at pos pointing at '\\', return (new_pos, name).

    Treats '@' as a letter for control-sequence-name purposes, which matches
    LaTeX behaviour inside \\makeatletter…\\makeatother — the convention
    in .sty / .cls files and in the macro files several papers ship
    (\\@sgnarg, \\@copyrightLine, rep@theorem, …). If '@' is treated as
    non-letter, those names get parsed as '\\@' + literal letters dropped
    into parameter text, which collapses many distinct macros into one.
    """
    n = len(text)
    assert pos < n and text[pos] == "\\"
    j = pos + 1
    if j >= n:
        return j, ""
    if text[j].isalpha() or text[j] == "@":
        start = j
        while j < n and (text[j].isalpha() or text[j] == "@"):
            j += 1
        return j, text[start:j]
    # Single non-letter token
    return j + 1, text[j]


# ---------------------------------------------------------------------------
# Pattern-driven argument consumer
# ---------------------------------------------------------------------------

def consume_pattern(text: str, pos: int, pattern: str) -> tuple[int, str | None]:
    """Walk arg pattern. Returns (end_after_last_consumed_arg, first_required_arg_name).

    The returned position is the index just past the last argument actually
    consumed. Optional slots that don't fire do NOT advance the returned
    position past the whitespace+comments scanned while probing for them —
    otherwise a trailing optional like the [date] slot on
    \\usepackage{foo}[date]? would pull the following provenance comment into
    the captured verbatim when re-scanning our own sidecars.
    """
    name: str | None = None
    end_after_consumed = pos
    for part in pattern.split():
        probe = skip_ws_and_comments(text, pos)
        if probe >= len(text):
            return end_after_consumed, name
        if part == "s":
            if text[probe] == "*":
                pos = probe + 1
                end_after_consumed = pos
        elif part == "o":
            if text[probe] == "[":
                pos = skip_balanced_brackets(text, probe)
                end_after_consumed = pos
        elif part == "r":
            if text[probe] == "{":
                start = probe
                pos = skip_balanced_braces(text, probe)
                end_after_consumed = pos
                if name is None:
                    inner = text[start + 1 : pos - 1].strip()
                    name = _name_from_inner(inner)
            else:
                # Required arg missing — bail out without advancing.
                return end_after_consumed, name
        else:
            raise ValueError(f"unknown pattern element {part!r}")
    return end_after_consumed, name


def _name_from_inner(inner: str) -> str:
    """Extract a defined-command name from the content of the first {...} group."""
    inner = inner.strip()
    if inner.startswith("\\"):
        # \foo bar -> 'foo'; \@start -> '@start'; \\ -> ''
        rest = inner[1:]
        if rest and rest[0].isalpha():
            m = re.match(r"[A-Za-z]+", rest)
            return m.group(0) if m else rest
        return rest[:1] if rest else ""
    return inner


# ---------------------------------------------------------------------------
# Dedicated consumers for non-standard syntaxes
# ---------------------------------------------------------------------------

def consume_def(text: str, pos: int) -> tuple[int, str | None]:
    """\\def\\foo<paramtext>{body} and friends."""
    pos = skip_ws_and_comments(text, pos)
    if pos >= len(text) or text[pos] != "\\":
        return pos, None
    pos, name = read_control_seq(text, pos)
    # Walk parameter text until first un-escaped '{'.
    n = len(text)
    while pos < n:
        c = text[pos]
        if c == "%":
            nl = text.find("\n", pos)
            pos = (nl + 1) if nl >= 0 else n
            continue
        if c == "\\" and pos + 1 < n:
            pos += 2
            continue
        if c == "{":
            break
        pos += 1
    if pos >= n:
        return pos, name
    return skip_balanced_braces(text, pos), name


def consume_let(text: str, pos: int) -> tuple[int, str | None]:
    """\\let\\foo=?\\bar (or \\let\\foo<char>)."""
    pos = skip_ws_and_comments(text, pos)
    if pos >= len(text) or text[pos] != "\\":
        return pos, None
    pos, name = read_control_seq(text, pos)
    pos = skip_ws_and_comments(text, pos)
    if pos < len(text) and text[pos] == "=":
        pos += 1
        pos = skip_ws_and_comments(text, pos)
    # Read one target token: control seq or single char.
    if pos < len(text):
        if text[pos] == "\\":
            pos, _ = read_control_seq(text, pos)
        else:
            pos += 1
    return pos, name


def consume_newdimen(text: str, pos: int) -> tuple[int, str | None]:
    """\\newdimen\\foo (plain-TeX style; no braces)."""
    pos = skip_ws_and_comments(text, pos)
    if pos >= len(text) or text[pos] != "\\":
        return pos, None
    pos, name = read_control_seq(text, pos)
    return pos, name


# ---------------------------------------------------------------------------
# Definition record + scanner
# ---------------------------------------------------------------------------

@dataclass
class Definition:
    kind: str
    name: str | None
    verbatim: str
    source: str       # file relpath
    line: int         # 1-based line in that file
    order: int        # global DFS visit order across all sources


@dataclass
class ScanResult:
    packages: list[Definition] = field(default_factory=list)
    macros: list[Definition] = field(default_factory=list)
    documentclass: str | None = None
    documentclass_source: tuple[str, int] | None = None
    sources_visited: list[str] = field(default_factory=list)


def scan_preamble(paper_dir: Path, main: Path, warn=print) -> ScanResult:
    result = ScanResult()
    seen: set[str] = set()
    counter = [0]

    main_src = make_source(paper_dir, main)
    # Truncate main at first un-commented \begin{document}.
    truncated_text = _truncate_at_begin_document(main_src.text)
    main_src = Source(
        relpath=main_src.relpath,
        text=truncated_text,
        line_starts=main_src.line_starts,
    )

    _scan_source(main_src, paper_dir, seen, result, counter, warn=warn, is_root=True)
    return result


def _truncate_at_begin_document(text: str) -> str:
    """Return text up to (not including) the first un-commented \\begin{document}."""
    n = len(text)
    i = 0
    target = r"\begin{document}"
    while i < n:
        c = text[i]
        if c == "%":
            nl = text.find("\n", i)
            i = (nl + 1) if nl >= 0 else n
            continue
        if c == "\\" and text.startswith(target, i):
            return text[:i]
        i += 1
    return text


def _scan_source(
    src: Source,
    paper_dir: Path,
    seen: set[str],
    result: ScanResult,
    counter: list[int],
    *,
    warn,
    is_root: bool = False,
) -> None:
    if src.relpath in seen:
        return
    seen.add(src.relpath)
    result.sources_visited.append(src.relpath)

    text = src.text
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "%":
            nl = text.find("\n", i)
            i = (nl + 1) if nl >= 0 else n
            continue
        # Treat unattached brace and bracket groups as opaque: anything inside
        # them is the body of some other (possibly unknown) command and must
        # not be scanned for definitions, otherwise we pick up conditional
        # \renewcommand calls living inside macro bodies. Known commands hit
        # their dispatch branch below and consume their own balanced groups
        # via the dedicated consumers.
        if c == "{":
            i = skip_balanced_braces(text, i)
            continue
        if c == "[":
            i = skip_balanced_brackets(text, i)
            continue
        if c != "\\":
            i += 1
            continue
        cmd_start = i
        j, cmd_name = read_control_seq(text, i)
        if not cmd_name:
            i = j
            continue

        # Strip a trailing '*' from the lookup name when probing tables that
        # already include the star in their pattern.
        lookup = cmd_name

        # Preamble \input / \include — recurse into the included file.
        if lookup in ("input", "include"):
            k = skip_ws_and_comments(text, j)
            if k < n and text[k] == "{":
                k_end = skip_balanced_braces(text, k)
                filename = text[k + 1 : k_end - 1].strip()
                sub_path = _resolve_input(paper_dir, filename)
                if sub_path is None:
                    warn(f"[warn]    {src.relpath}:{src.line_at(cmd_start)} "
                         f"\\{lookup}{{{filename}}} not found, skipped",
                         file=sys.stderr)
                else:
                    sub_src = make_source(paper_dir, sub_path)
                    _scan_source(sub_src, paper_dir, seen, result, counter, warn=warn)
                i = k_end
            else:
                i = j
            continue

        # \documentclass — capture for the packages.tex header comment.
        if lookup == "documentclass":
            end, _ = consume_pattern(text, j, "o r")
            if result.documentclass is None:
                result.documentclass = text[cmd_start:end]
                result.documentclass_source = (src.relpath, src.line_at(cmd_start))
            i = end
            continue

        # Package commands.
        if lookup in PACKAGE_PATTERNS:
            end, _ = consume_pattern(text, j, PACKAGE_PATTERNS[lookup])
            counter[0] += 1
            result.packages.append(Definition(
                kind=lookup,
                name=None,
                verbatim=text[cmd_start:end],
                source=src.relpath,
                line=src.line_at(cmd_start),
                order=counter[0],
            ))
            i = end
            continue

        # Macro commands with standard patterns.
        if lookup in MACRO_PATTERNS:
            end, name = consume_pattern(text, j, MACRO_PATTERNS[lookup])
            counter[0] += 1
            result.macros.append(Definition(
                kind=lookup,
                name=name,
                verbatim=text[cmd_start:end],
                source=src.relpath,
                line=src.line_at(cmd_start),
                order=counter[0],
            ))
            i = end
            continue

        if lookup in SPECIAL_DEFS:
            end, name = consume_def(text, j)
            counter[0] += 1
            result.macros.append(Definition(
                kind=lookup,
                name=name,
                verbatim=text[cmd_start:end],
                source=src.relpath,
                line=src.line_at(cmd_start),
                order=counter[0],
            ))
            i = end
            continue

        if lookup in LET_COMMANDS:
            end, name = consume_let(text, j)
            counter[0] += 1
            result.macros.append(Definition(
                kind=lookup,
                name=name,
                verbatim=text[cmd_start:end],
                source=src.relpath,
                line=src.line_at(cmd_start),
                order=counter[0],
            ))
            i = end
            continue

        if lookup in NEWDIMEN_COMMANDS:
            end, name = consume_newdimen(text, j)
            counter[0] += 1
            result.macros.append(Definition(
                kind=lookup,
                name=name,
                verbatim=text[cmd_start:end],
                source=src.relpath,
                line=src.line_at(cmd_start),
                order=counter[0],
            ))
            i = end
            continue

        # Unknown command — advance past its name and keep scanning.
        i = j


def _resolve_input(paper_dir: Path, filename: str) -> Path | None:
    candidates = [paper_dir / filename]
    if not filename.lower().endswith(".tex"):
        candidates.append(paper_dir / (filename + ".tex"))
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------------------
# Duplicate detection + output formatting
# ---------------------------------------------------------------------------

def find_intra_paper_duplicates(macros: list[Definition]) -> dict[str, list[Definition]]:
    by_name: dict[str, list[Definition]] = defaultdict(list)
    for d in macros:
        if d.name:
            by_name[d.name].append(d)
    return {n: defs for n, defs in by_name.items() if len(defs) > 1}


def format_packages_file(paper_folder: str, result: ScanResult, vendored: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"% Auto-generated by scripts/paper_parsing.py from {paper_folder}.")
    lines.append(f"% Sources parsed: {', '.join(result.sources_visited)}.")
    if result.documentclass and result.documentclass_source:
        src, line = result.documentclass_source
        lines.append(f"% Document class ({src}:{line}, not loaded here):")
        for cls_line in result.documentclass.splitlines():
            lines.append(f"%   {cls_line}")
    if vendored:
        lines.append(f"% Vendored package files in the paper directory ({len(vendored)}):")
        for v in vendored:
            lines.append(f"%   {v}")
    else:
        lines.append("% Vendored package files in the paper directory: (none)")
    lines.append("")

    if not result.packages:
        lines.append("% (no package load commands found)")
        lines.append("")
        return "\n".join(lines)

    for d in result.packages:
        lines.append(f"% from {d.source}:{d.line}")
        lines.append(d.verbatim)
    lines.append("")
    return "\n".join(lines)


def format_macros_file(
    paper_folder: str,
    result: ScanResult,
    duplicates: dict[str, list[Definition]],
) -> str:
    lines: list[str] = []
    lines.append(f"% Auto-generated by scripts/paper_parsing.py from {paper_folder}.")
    lines.append(f"% Sources parsed: {', '.join(result.sources_visited)}.")
    if duplicates:
        lines.append(f"% Intra-paper duplicate names ({len(duplicates)}): "
                     + ", ".join(sorted(duplicates)))
    else:
        lines.append("% Intra-paper duplicate names: (none)")
    lines.append("")

    if not result.macros:
        lines.append("% (no macro/environment/theorem definitions found)")
        lines.append("")
        return "\n".join(lines)

    # Build a quick lookup: for any duplicate, list the (source,line) pairs
    # of EARLIER occurrences keyed by the later occurrence's id().
    earlier_for: dict[int, list[tuple[str, int]]] = {}
    for name, defs in duplicates.items():
        defs_sorted = sorted(defs, key=lambda d: d.order)
        for idx, d in enumerate(defs_sorted[1:], start=1):
            earlier_for[id(d)] = [(e.source, e.line) for e in defs_sorted[:idx]]

    # Group by bucket; within bucket, source order (by .order).
    buckets: dict[int, list[Definition]] = defaultdict(list)
    for d in result.macros:
        buckets[KIND_BUCKET.get(d.kind, 99)].append(d)
    for k in buckets:
        buckets[k].sort(key=lambda d: d.order)

    first_section = True
    for bucket_idx in sorted(buckets):
        header = BUCKET_HEADERS.get(bucket_idx, "Other")
        if not first_section:
            lines.append("")
        first_section = False
        lines.append(f"% --- {header} ---")
        for d in buckets[bucket_idx]:
            if id(d) in earlier_for:
                where = ", ".join(f"{s}:{ln}" for s, ln in earlier_for[id(d)])
                lines.append(f"% [intra-paper duplicate] \\{d.name} — also defined at {where}")
            lines.append(f"% from {d.source}:{d.line}")
            lines.append(d.verbatim)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(
    paper_folder: str,
    main: Path,
    result: ScanResult,
    vendored: list[str],
    duplicates: dict[str, list[Definition]],
) -> None:
    pkg_counts = defaultdict(int)
    for p in result.packages:
        pkg_counts[p.kind] += 1
    pkg_summary = ", ".join(
        f"{n} \\{k}" for k, n in sorted(pkg_counts.items())
    ) or "0"

    macro_counts = defaultdict(int)
    for d in result.macros:
        macro_counts[d.kind] += 1
    by_bucket = defaultdict(int)
    for kind, n in macro_counts.items():
        by_bucket[BUCKET_HEADERS.get(KIND_BUCKET.get(kind, 99), "Other")] += n
    macro_summary = ", ".join(f"{n} {label.lower()}" for label, n in sorted(by_bucket.items()))

    print(f"[parsed] {paper_folder}", file=sys.stderr)
    print(f"  main:     {main.name}", file=sys.stderr)
    other = [s for s in result.sources_visited if s != main.name]
    print(f"  inputs:   {', '.join(other) if other else '(none)'}", file=sys.stderr)
    print(f"  packages: {len(result.packages)} ({pkg_summary})", file=sys.stderr)
    print(f"  macros:   {len(result.macros)} ({macro_summary})", file=sys.stderr)
    print(f"  vendored .sty/.cls in folder: {', '.join(vendored) if vendored else '(none)'}",
          file=sys.stderr)
    if duplicates:
        print("  intra-paper duplicates:", file=sys.stderr)
        for name in sorted(duplicates):
            locs = ", ".join(f"{d.source}:{d.line}" for d in sorted(duplicates[name], key=lambda d: d.order))
            print(f"    \\{name} — {locs}", file=sys.stderr)
    else:
        print("  intra-paper duplicates: (none)", file=sys.stderr)


# ---------------------------------------------------------------------------
# Cross-paper collision sweep
# ---------------------------------------------------------------------------

# \newtheorem env names that every math paper redefines; collisions on these
# are expected and get collapsed into one summary line.
STANDARD_THEOREM_ENVS = {
    "theorem", "lemma", "corollary", "proposition", "definition",
    "remark", "example", "conjecture", "assumption", "claim",
    "fact", "observation", "note", "exercise", "problem", "question",
    "rep@theorem",
}

SIDECAR_NAME_RE = re.compile(r"^arXiv-(\d{4})-macros\.tex$")


def scan_sidecar_full(arxiv_dir: Path, sidecar: Path) -> ScanResult:
    """Re-scan an already-written arXiv-YYMM-{packages,macros}.tex sidecar.

    Returns the full ScanResult (packages + macros, each Definition retains
    its verbatim text, source filename, source line, and kind). The scanner
    is the same one used for paper preambles — sidecars have no \\input and
    no \\begin{document}, so DFS recursion never triggers.
    """
    src = make_source(arxiv_dir, sidecar)
    result = ScanResult()
    seen: set[str] = set()
    counter = [0]
    _scan_source(src, arxiv_dir, seen, result, counter, warn=lambda *a, **kw: None)
    return result


def scan_sidecar_macros(arxiv_dir: Path, sidecar: Path) -> dict[str, str]:
    """Backwards-compatible wrapper: name -> kind for an arXiv-YYMM-macros.tex."""
    return {d.name: d.kind for d in scan_sidecar_full(arxiv_dir, sidecar).macros if d.name}


def print_cross_paper_collisions(
    arxiv_dir: Path,
    current_yymm: str,
    current_macros: list[Definition],
) -> None:
    """Compare current paper's macro names against every sibling sidecar.

    Prints to stderr. Reads only arXiv-*-macros.tex sidecars (not paper
    sources), so this is fast and self-contained — no shell, no regex over
    LaTeX backslashes.
    """
    current_by_name: dict[str, str] = {d.name: d.kind for d in current_macros if d.name}
    if not current_by_name:
        return

    siblings = sorted(
        p for p in arxiv_dir.glob("arXiv-*-macros.tex")
        if not p.name.startswith(f"arXiv-{current_yymm}-")
    )
    if not siblings:
        print("  cross-paper collisions: (no prior outputs to compare)",
              file=sys.stderr)
        return

    # name -> sorted list of sibling yymm strings where it's also defined
    collisions: dict[str, list[str]] = defaultdict(list)
    for sibling in siblings:
        m = SIDECAR_NAME_RE.match(sibling.name)
        if not m:
            continue
        ym = m.group(1)
        their_names = scan_sidecar_macros(arxiv_dir, sibling)
        for name in current_by_name.keys() & their_names.keys():
            collisions[name].append(ym)

    # Split boilerplate \newtheorem envs from the rest.
    boilerplate = sorted(
        n for n in collisions
        if current_by_name.get(n) == "newtheorem" and n in STANDARD_THEOREM_ENVS
    )
    real = {n: collisions[n] for n in collisions if n not in set(boilerplate)}

    if not real and not boilerplate:
        print("  cross-paper collisions: (none)", file=sys.stderr)
        return

    print(f"  cross-paper collisions ({len(real)} non-boilerplate):",
          file=sys.stderr)
    for name in sorted(real):
        kind = current_by_name[name]
        ys = ", ".join(sorted(real[name]))
        print(f"    \\{name} ({kind}) — also in {ys}", file=sys.stderr)
    if boilerplate:
        all_ys: set[str] = set()
        for n in boilerplate:
            all_ys.update(collisions[n])
        print(
            f"  boilerplate \\newtheorem envs colliding (expected): "
            f"{', '.join(boilerplate)} (across {', '.join(sorted(all_ys))})",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def die(msg: str, code: int = 2) -> None:
    print(f"paper_parsing: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paper",
                   help="Paper id: folder name (arXiv-2302.05882v1), YYMM (2302 / arXiv-2302), or arXiv id (2302.05882[v1]).")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse and report, but don't write any output files.")
    p.add_argument("--arxiv-dir", type=Path, default=ARXIV_DIR,
                   help=f"Directory containing the unpacked papers (default: {ARXIV_DIR}).")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    paper_dir = resolve_paper_folder(args.arxiv_dir, args.paper)
    yymm = yymm_of(paper_dir)
    main_tex = find_main_tex(paper_dir)

    result = scan_preamble(paper_dir, main_tex)
    duplicates = find_intra_paper_duplicates(result.macros)
    vendored = sorted(
        p.name for p in paper_dir.iterdir()
        if p.is_file() and p.suffix in (".sty", ".cls")
    )

    print_summary(paper_dir.name, main_tex, result, vendored, duplicates)
    print_cross_paper_collisions(args.arxiv_dir, yymm, result.macros)

    packages_out = args.arxiv_dir / f"arXiv-{yymm}-packages.tex"
    macros_out = args.arxiv_dir / f"arXiv-{yymm}-macros.tex"

    packages_text = format_packages_file(paper_dir.name, result, vendored)
    macros_text = format_macros_file(paper_dir.name, result, duplicates)

    if args.dry_run:
        print(f"[dry-run] would write {packages_out.relative_to(REPO_ROOT)}", file=sys.stderr)
        print(f"[dry-run] would write {macros_out.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 0

    write_atomic(packages_out, packages_text)
    write_atomic(macros_out, macros_text)
    print(f"[write]   {packages_out.relative_to(REPO_ROOT)}", file=sys.stderr)
    print(f"[write]   {macros_out.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
