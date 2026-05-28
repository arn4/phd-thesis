"""Merge per-paper arXiv-YYMM-{packages,macros}.tex sidecars into thesis-wide files.

Run:
    uv run scripts/merge_preambles.py             # interactive (canonical)
    uv run scripts/merge_preambles.py --dry-run   # parse + report, write nothing
    uv run scripts/merge_preambles.py --no-stop   # auto-resolve (most-popular variant)

Outputs (both tracked in git — they ARE the persistent record of editorial decisions):
    papers-dependencies.tex          packages + PassOptionsToPackage preemptions
    papers-macros.tex                merged macro definitions

There is no separate JSON state file. On re-run the script reads the existing
output files: any name with an entry there is considered "decided" and never
re-prompted, even if a new variant appears in the per-paper sidecars. To skip
a name from the merged output, the prompt's [s] choice writes a comment line
"% [skip:<category>] <name>" in the relevant output file; that line is
respected on every subsequent run. Deleting either a definition or a skip
marker by hand brings the name back into the prompt queue on the next run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from paper_parsing import (
    ARXIV_DIR,
    REPO_ROOT,
    BUCKET_HEADERS,
    KIND_BUCKET,
    Definition,
    scan_sidecar_full,
)

DEPS_OUT = REPO_ROOT / "papers-dependencies.tex"
MACROS_OUT = REPO_ROOT / "papers-macros.tex"

# Sort-key overrides for the Packages section of papers-dependencies.tex.
# A package listed here is emitted as if its name were the value, so we can
# honour LaTeX's hard load-order constraints (e.g. cleveref must load AFTER
# hyperref). Everything not listed stays alphabetical.
PACKAGE_ORDER_OVERRIDES: dict[str, str] = {
    # "~" (ASCII 0x7E) sorts after every letter, so this places cleveref
    # immediately after hyperref and before any "i..."-prefixed package.
    "cleveref": "hyperref~",
}

SIDECAR_RE = re.compile(r"^arXiv-(\d{4})-(packages|macros)\.tex$")
USEPACKAGE_RE = re.compile(
    r"\\(?:usepackage|RequirePackage)\*?"
    r"(?:\s*\[(?P<opts>(?:[^\[\]{}]|\{[^{}]*\})*)\])?"
    r"\s*\{(?P<names>[^{}]+)\}"
    r"(?:\s*\[(?P<date>[^\[\]]*)\])?",
    re.DOTALL,
)
PASSOPTS_RE = re.compile(
    r"\\PassOptionsToPackage\s*\{(?P<opts>[^{}]*)\}\s*\{(?P<name>[^{}]+)\}",
    re.DOTALL,
)
SKIP_LINE_RE = re.compile(
    r"^\s*%\s*\[skip:(?P<category>passopts|package|macro)\]\s+(?P<name>\S+)\s*$",
    re.MULTILINE,
)
SPLIT_PROVENANCE_RE = re.compile(
    r"^\s*%\s*renamed from \\(?P<local>[A-Za-z@]+);\s*bodies from "
    r"(?P<papers>.+?)\s*$",
    re.MULTILINE,
)


def rewrite_signature(verbatim: str, local_name: str, global_name: str) -> str:
    """Replace the first \\<local_name> token in `verbatim` with \\<global_name>.

    Used when splitting a macro group: the user picks a new global name for
    a variant and the script rewrites the definition's name slot accordingly.
    Only the first occurrence is rewritten — recursive uses of the local
    name inside the body are left alone (they may genuinely refer to a
    different macro defined elsewhere in the thesis preamble).
    """
    if local_name == global_name:
        return verbatim
    pat = re.compile(r"\\" + re.escape(local_name) + r"(?![A-Za-z@])")
    return pat.sub(f"\\\\{global_name}", verbatim, count=1)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    canonical: str
    verbatim: str
    papers: list[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()[:12]


@dataclass
class Group:
    category: str            # 'passopts' | 'package' | 'macro'
    name: str
    kind: str | None         # macro kind for output bucketing; None otherwise
    variants: list[Variant]


@dataclass
class SplitEntry:
    """One output definition produced by splitting a macro group.

    A split takes a group like \\P (defined two different ways across papers)
    and emits multiple distinct global definitions — one per chosen global
    name — with each variant's body rewritten to use the new name.
    """
    global_name: str
    verbatim: str           # rewritten so the signature names `global_name`
    papers: list[str]       # source papers contributing this body
    original_local_name: str


@dataclass
class Resolution:
    # 'kept' | 'skip' | 'auto-identical' | 'pick' | 'custom' | 'split' | 'pending'
    kind: str
    verbatim: str | None       # None for skip/pending/split
    picked_from: str | None = None
    splits: list[SplitEntry] | None = None  # only set when kind == 'split'


@dataclass
class ExistingState:
    passopts: dict[str, str] = field(default_factory=dict)
    packages: dict[str, str] = field(default_factory=dict)
    macros: dict[str, str] = field(default_factory=dict)
    skip_passopts: set[str] = field(default_factory=set)
    skip_packages: set[str] = field(default_factory=set)
    skip_macros: set[str] = field(default_factory=set)
    # local_name -> list of (global_name, verbatim, source_papers) parsed
    # from "renamed from" provenance comments in papers-macros.tex.
    macro_splits: dict[str, list[tuple[str, str, list[str]]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sidecar loading
# ---------------------------------------------------------------------------

def list_sidecars(arxiv_dir: Path) -> list[tuple[str, str, Path]]:
    out: list[tuple[str, str, Path]] = []
    for p in sorted(arxiv_dir.iterdir()):
        m = SIDECAR_RE.match(p.name)
        if m:
            out.append((m.group(1), m.group(2), p))
    return out


def canonicalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def split_usepackage(d: Definition) -> list[tuple[str, str]]:
    m = USEPACKAGE_RE.match(d.verbatim)
    if not m:
        return []
    opts = m.group("opts")
    date_opt = m.group("date")
    names = [n.strip() for n in m.group("names").split(",") if n.strip()]
    out: list[tuple[str, str]] = []
    for name in names:
        parts = ["\\usepackage"]
        if opts is not None:
            parts.append(f"[{opts}]")
        parts.append(f"{{{name}}}")
        if date_opt is not None:
            parts.append(f"[{date_opt}]")
        out.append((name, "".join(parts)))
    return out


def parse_passopts(d: Definition) -> tuple[str, str] | None:
    m = PASSOPTS_RE.match(d.verbatim)
    if not m:
        return None
    name = m.group("name").strip()
    return name, f"\\PassOptionsToPackage{{{m.group('opts')}}}{{{name}}}"


def load_all_definitions(arxiv_dir: Path) -> tuple[list[Group], list[Group], list[Group]]:
    passopts_entries: list[tuple[str, str, str]] = []
    package_entries: list[tuple[str, str, str]] = []
    macro_entries: list[tuple[str, str, str, str]] = []

    sidecars = list_sidecars(arxiv_dir)
    if not sidecars:
        die(f"no arXiv-*-{{packages,macros}}.tex sidecars found under {arxiv_dir}")

    seen_in_paper: set[tuple[str, str, str]] = set()
    for yymm, kind, path in sidecars:
        result = scan_sidecar_full(arxiv_dir, path)
        if kind == "packages":
            for d in result.packages:
                if d.kind == "PassOptionsToPackage":
                    parsed = parse_passopts(d)
                    if parsed is None:
                        print(f"[warn] {path.name}: unparseable {d.verbatim!r}", file=sys.stderr)
                        continue
                    name, verb = parsed
                    key = ("passopts", yymm, canonicalize(verb))
                    if key in seen_in_paper:
                        continue
                    seen_in_paper.add(key)
                    passopts_entries.append((name, verb, yymm))
                else:
                    for name, verb in split_usepackage(d):
                        key = ("package", yymm, canonicalize(verb))
                        if key in seen_in_paper:
                            continue
                        seen_in_paper.add(key)
                        package_entries.append((name, verb, yymm))
        else:
            for d in result.macros:
                if not d.name:
                    continue
                key = ("macro", yymm, canonicalize(d.verbatim) + "|" + d.name)
                if key in seen_in_paper:
                    continue
                seen_in_paper.add(key)
                macro_entries.append((d.name, d.verbatim, yymm, d.kind))

    return (
        group_entries(passopts_entries, "passopts"),
        group_entries(package_entries, "package"),
        group_macros(macro_entries),
    )


def group_entries(entries: list[tuple[str, str, str]], category: str) -> list[Group]:
    by_name: dict[str, dict[str, Variant]] = defaultdict(dict)
    for name, verbatim, paper in entries:
        canon = canonicalize(verbatim)
        if canon not in by_name[name]:
            by_name[name][canon] = Variant(canonical=canon, verbatim=verbatim)
        by_name[name][canon].papers.append(paper)
    out: list[Group] = []
    for name in sorted(by_name):
        variants = sorted(
            by_name[name].values(),
            key=lambda v: (-len(v.papers), v.canonical),
        )
        out.append(Group(category=category, name=name, kind=None, variants=variants))
    return out


def group_macros(entries: list[tuple[str, str, str, str]]) -> list[Group]:
    by_name: dict[str, dict[str, Variant]] = defaultdict(dict)
    kind_of: dict[str, str] = {}
    for name, verbatim, paper, kind in entries:
        canon = canonicalize(verbatim)
        if canon not in by_name[name]:
            by_name[name][canon] = Variant(canonical=canon, verbatim=verbatim)
        by_name[name][canon].papers.append(paper)
        kind_of.setdefault(name, kind)
    out: list[Group] = []
    for name in sorted(by_name):
        variants = sorted(
            by_name[name].values(),
            key=lambda v: (-len(v.papers), v.canonical),
        )
        out.append(Group(category="macro", name=name, kind=kind_of[name], variants=variants))
    return out


# ---------------------------------------------------------------------------
# Existing state — read directly from the merged .tex files
# ---------------------------------------------------------------------------

def load_existing_state() -> ExistingState:
    state = ExistingState()

    if DEPS_OUT.exists():
        text = DEPS_OUT.read_text(encoding="utf-8")
        for m in SKIP_LINE_RE.finditer(text):
            cat, name = m.group("category"), m.group("name")
            if cat == "passopts":
                state.skip_passopts.add(name)
            elif cat == "package":
                state.skip_packages.add(name)
        scan = scan_sidecar_full(DEPS_OUT.parent, DEPS_OUT)
        for d in scan.packages:
            if d.kind == "PassOptionsToPackage":
                parsed = parse_passopts(d)
                if parsed:
                    state.passopts[parsed[0]] = parsed[1]
            else:
                for name, verb in split_usepackage(d):
                    state.packages[name] = verb

    if MACROS_OUT.exists():
        text = MACROS_OUT.read_text(encoding="utf-8")
        for m in SKIP_LINE_RE.finditer(text):
            if m.group("category") == "macro":
                state.skip_macros.add(m.group("name"))
        scan = scan_sidecar_full(MACROS_OUT.parent, MACROS_OUT)
        defs = [d for d in scan.macros if d.name]
        lines = text.splitlines()
        n_lines = len(lines)
        splits: dict[str, list[tuple[str, str, list[str]]]] = defaultdict(list)
        for i, d in enumerate(defs):
            state.macros[d.name] = d.verbatim
            # Walk lines between this def's start and the next def's start
            # looking for a "% renamed from \X; bodies from arXiv-YYMM, ..." marker.
            next_line = defs[i + 1].line if i + 1 < len(defs) else n_lines + 1
            for line_no in range(d.line, min(next_line - 1, n_lines)):
                line = lines[line_no] if line_no < n_lines else ""
                m = SPLIT_PROVENANCE_RE.match(line)
                if m:
                    local_name = m.group("local")
                    papers = re.findall(r"arXiv-(\d{4})", m.group("papers"))
                    splits[local_name].append((d.name, d.verbatim, papers))
                    break
        state.macro_splits = dict(splits)

    return state


# ---------------------------------------------------------------------------
# Resolution: prompt / auto / kept
# ---------------------------------------------------------------------------

class QuitRequested(Exception):
    pass


def auto_pick_popular(group: Group) -> Resolution:
    best = max(
        group.variants,
        key=lambda v: (len(v.papers), len(v.verbatim), v.canonical),
    )
    return Resolution(
        kind="pick",
        verbatim=best.verbatim,
        picked_from=sorted(set(best.papers))[0],
    )


def prompt_user(group: Group, idx: int, total: int) -> Resolution:
    n = len(group.variants)
    total_papers = sum(len(v.papers) for v in group.variants)
    bar = "=" * 72
    prefix = "\\" if group.category == "macro" else ""
    print(bar, file=sys.stderr)
    print(
        f"{prefix}{group.name} ({group.category}) — {n} distinct variants across "
        f"{total_papers} papers   ({idx} of {total} conflicts)",
        file=sys.stderr,
    )
    print(bar, file=sys.stderr)
    for i, v in enumerate(group.variants, 1):
        froms = ", ".join(f"arXiv-{p}" for p in sorted(set(v.papers)))
        suffix = "s" if len(v.papers) != 1 else ""
        print(f"[{i}] in {froms} ({len(v.papers)} paper{suffix}):", file=sys.stderr)
        for line in v.verbatim.splitlines() or [v.verbatim]:
            print(f"    {line}", file=sys.stderr)
        print(file=sys.stderr)
    print("[c] write a custom replacement (end with a line containing only END)", file=sys.stderr)
    if group.category == "macro":
        print("[d] split into distinct global names (one per variant; rename signatures)",
              file=sys.stderr)
    print("[s] skip — don't include in merged output", file=sys.stderr)
    print("[q] quit (the decisions already made stay in the .tex files)", file=sys.stderr)

    choices = f"1-{n}/c/" + ("d/" if group.category == "macro" else "") + "s/q"
    while True:
        try:
            ans = input(f"Choice [{choices}]: ").strip().lower()
        except EOFError:
            raise QuitRequested()
        if ans == "q":
            raise QuitRequested()
        if ans == "s":
            return Resolution(kind="skip", verbatim=None)
        if ans == "c":
            print("Enter replacement (end with a line containing only END):", file=sys.stderr)
            lines: list[str] = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip() == "END":
                    break
                lines.append(line)
            return Resolution(kind="custom", verbatim="\n".join(lines))
        if ans == "d" and group.category == "macro":
            return prompt_split(group)
        if ans.isdigit():
            i = int(ans)
            if 1 <= i <= n:
                v = group.variants[i - 1]
                return Resolution(
                    kind="pick",
                    verbatim=v.verbatim,
                    picked_from=sorted(set(v.papers))[0],
                )
        print(f"Please enter {choices}.", file=sys.stderr)


def prompt_split(group: Group) -> Resolution:
    """Walk each variant, asking for a new global name; build a split Resolution.

    The user can re-enter the assignment loop by answering 'n' at the confirm
    prompt. Returns Resolution(kind='split'). On EOF, raises QuitRequested.
    """
    while True:
        print(
            f"\nSplitting \\{group.name} into per-variant global macros.",
            file=sys.stderr,
        )
        print(
            "For each variant, type a new global name (or press Enter to keep the original).",
            file=sys.stderr,
        )
        splits: list[SplitEntry] = []
        chosen_names: dict[str, SplitEntry] = {}
        for i, v in enumerate(group.variants, 1):
            froms = ", ".join(f"arXiv-{p}" for p in sorted(set(v.papers)))
            print(f"\nVariant [{i}] (in {froms}):", file=sys.stderr)
            for line in v.verbatim.splitlines() or [v.verbatim]:
                print(f"    {line}", file=sys.stderr)
            try:
                raw = input(f"  Global name [{group.name}]: ").strip()
            except EOFError:
                raise QuitRequested()
            if raw.startswith("\\"):
                raw = raw[1:]
            global_name = raw if raw else group.name
            if not re.fullmatch(r"[A-Za-z@]+", global_name):
                print(
                    f"  '{global_name}' isn't a valid TeX control-sequence name "
                    f"(letters and @ only); keeping the original",
                    file=sys.stderr,
                )
                global_name = group.name
            new_verbatim = rewrite_signature(v.verbatim, group.name, global_name)
            if global_name in chosen_names:
                chosen_names[global_name].papers.extend(v.papers)
            else:
                entry = SplitEntry(
                    global_name=global_name,
                    verbatim=new_verbatim,
                    papers=list(v.papers),
                    original_local_name=group.name,
                )
                splits.append(entry)
                chosen_names[global_name] = entry
        print(file=sys.stderr)
        print("Confirm split:", file=sys.stderr)
        for sp in splits:
            ps = ", ".join(f"arXiv-{p}" for p in sorted(set(sp.papers)))
            print(f"  -> \\{sp.global_name} from {ps}", file=sys.stderr)
            for line in sp.verbatim.splitlines() or [sp.verbatim]:
                print(f"     {line}", file=sys.stderr)
        try:
            ans = input("Proceed? [y/n] (n re-enters the names): ").strip().lower()
        except EOFError:
            raise QuitRequested()
        if ans in ("y", "yes", ""):
            return Resolution(kind="split", verbatim=None, splits=splits)
        print("Split cancelled — entering names again.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def existing_for_category(category: str, state: ExistingState) -> tuple[dict[str, str], set[str]]:
    if category == "passopts":
        return state.passopts, state.skip_passopts
    if category == "package":
        return state.packages, state.skip_packages
    return state.macros, state.skip_macros


def resolve_group(
    g: Group,
    state: ExistingState,
    *,
    no_stop: bool,
    dry_run: bool,
    conflict_idx: list[int],
    conflict_total: int,
    stats: dict,
) -> Resolution:
    existing_kept, existing_skip = existing_for_category(g.category, state)

    # Splits live in the macros file only; reconstruct them first because a
    # split-target global name can collide with one of the variants' names.
    if g.category == "macro" and g.name in state.macro_splits:
        prior = state.macro_splits[g.name]
        splits = [
            SplitEntry(
                global_name=gn,
                verbatim=verbatim,
                papers=list(papers),
                original_local_name=g.name,
            )
            for gn, verbatim, papers in prior
        ]
        stats["kept_split"] += 1
        return Resolution(kind="split", verbatim=None, splits=splits)

    if g.name in existing_skip:
        stats["kept_skip"] += 1
        return Resolution(kind="skip", verbatim=None)
    if g.name in existing_kept:
        # Check whether new variants have appeared since the user decided.
        kept_canon = canonicalize(existing_kept[g.name])
        variant_canons = {v.canonical for v in g.variants}
        if kept_canon not in variant_canons and len(g.variants) > 0:
            # User's recorded body doesn't match any current sidecar variant; could be a
            # custom decision, or all variants changed since. Log informationally but keep
            # the user's verbatim — their decision is definitive.
            print(
                f"[stale] {g.category} {g.name}: kept entry no longer matches any current "
                f"sidecar variant; respecting prior decision",
                file=sys.stderr,
            )
        stats["kept"] += 1
        return Resolution(kind="kept", verbatim=existing_kept[g.name])

    if len(g.variants) == 1:
        stats["auto_identical"] += 1
        return Resolution(kind="auto-identical", verbatim=g.variants[0].verbatim)

    if dry_run:
        stats["pending"] += 1
        print_conflict_preview(g)
        return Resolution(kind="pending", verbatim=None)

    if no_stop:
        stats["auto_popular"] += 1
        r = auto_pick_popular(g)
        ps = ", ".join(f"arXiv-{p}" for p in sorted(set(g.variants[0].papers)))
        print(
            f"[auto:popular] {g.category} {g.name}: picked variant from {ps} "
            f"(over {len(g.variants) - 1} alternative(s))",
            file=sys.stderr,
        )
        return r

    conflict_idx[0] += 1
    r = prompt_user(g, conflict_idx[0], conflict_total)
    stats[f"chose_{r.kind}"] += 1
    # Persist immediately so quit-resume works: rewrite the output files
    # after each decision. The decision is now physically present in the .tex.
    # (Actual save happens at the top of the next outer call.)
    return r


def print_conflict_preview(g: Group) -> None:
    bar = "-" * 60
    prefix = "\\" if g.category == "macro" else ""
    print(f"{bar}\n{prefix}{g.name} ({g.category}) — {len(g.variants)} variants",
          file=sys.stderr)
    for i, v in enumerate(g.variants, 1):
        froms = ", ".join(f"arXiv-{p}" for p in sorted(set(v.papers)))
        first_line = v.verbatim.splitlines()[0] if v.verbatim else v.verbatim
        print(f"  [{i}] {first_line!s}   ({froms})", file=sys.stderr)


def precount_pending(groups: list[Group], state: ExistingState) -> int:
    n = 0
    for g in groups:
        kept, skip = existing_for_category(g.category, state)
        if g.name in kept or g.name in skip:
            continue
        if len(g.variants) <= 1:
            continue
        n += 1
    return n


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def provenance_comment(r: Resolution, g: Group) -> str:
    if r.kind == "custom":
        all_ps = sorted({p for v in g.variants for p in v.papers})
        return f"% custom replacement; overrides variants from {', '.join(f'arXiv-{p}' for p in all_ps)}"
    if r.verbatim is None:
        return ""
    # For auto-identical, kept, and pick: derive the comment from the current
    # sidecar variants. This makes "kept" and "pick" produce identical output
    # when the kept verbatim still matches a variant, so a second --no-stop
    # run is byte-identical to the first.
    target = canonicalize(r.verbatim)
    matching: set[str] = set()
    losers: list[str] = []
    for v in g.variants:
        if canonicalize(v.verbatim) == target:
            matching.update(v.papers)
        else:
            ps = ", ".join(f"arXiv-{p}" for p in sorted(set(v.papers)))
            first = v.verbatim.splitlines()[0] if v.verbatim else "?"
            losers.append(f"{first} in {ps}")
    if not matching:
        sidecars = ", ".join(f"arXiv-{p}" for p in sorted({p for v in g.variants for p in v.papers}))
        if not sidecars:
            return "% manual entry (no current sidecar variant)"
        return f"% manual entry (no current sidecar variant matches; sidecars carried: {sidecars})"
    winner_str = ", ".join(f"arXiv-{p}" for p in sorted(matching))
    if not losers:
        return f"% auto: identical in {winner_str}"
    return f"% picked from {winner_str} — conflicts: {'; '.join(losers)}"


def write_dependencies(
    passopts_groups: list[Group],
    package_groups: list[Group],
    resolutions: dict[tuple[str, str], Resolution],
) -> None:
    lines: list[str] = []
    lines.append("% Generated by scripts/merge_preambles.py — re-runnable, but edits made")
    lines.append("% directly to this file (changing a verbatim, deleting an entry, or")
    lines.append("% writing a '% [skip:passopts] name' / '% [skip:package] name' line)")
    lines.append("% ARE the persistent record of editorial decisions; the script honours")
    lines.append("% them on the next run.")
    lines.append("")

    def emit_section(header: str, groups: list[Group], category: str) -> None:
        live = [g for g in groups if (category, g.name) in resolutions]
        if not live:
            return
        lines.append(f"% --- {header} ---")
        sort_key = (
            (lambda x: PACKAGE_ORDER_OVERRIDES.get(x.name, x.name))
            if category == "package"
            else (lambda x: x.name)
        )
        for g in sorted(live, key=sort_key):
            r = resolutions[(category, g.name)]
            if r.kind == "skip":
                lines.append(f"% [skip:{category}] {g.name}")
                lines.append("")
                continue
            if r.kind == "pending" or r.verbatim is None:
                continue
            lines.append(r.verbatim)
            lines.append(provenance_comment(r, g))
            lines.append("")

    emit_section("PassOptionsToPackage preemptions", passopts_groups, "passopts")
    emit_section("Packages", package_groups, "package")
    DEPS_OUT.write_text("\n".join(lines), encoding="utf-8")


def write_macros(
    macro_groups: list[Group],
    resolutions: dict[tuple[str, str], Resolution],
) -> None:
    lines: list[str] = []
    lines.append("% Generated by scripts/merge_preambles.py — re-runnable, but edits made")
    lines.append("% directly to this file (changing a verbatim, deleting an entry, or")
    lines.append("% writing a '% [skip:macro] name' line) ARE the persistent record of")
    lines.append("% editorial decisions; the script honours them on the next run.")
    lines.append("")

    buckets: dict[int, list[Group]] = defaultdict(list)
    for g in macro_groups:
        if ("macro", g.name) not in resolutions:
            continue
        buckets[KIND_BUCKET.get(g.kind or "", 99)].append(g)

    first = True
    for bucket_idx in sorted(buckets):
        header = BUCKET_HEADERS.get(bucket_idx, "Other")
        if not first:
            lines.append("")
        first = False
        lines.append(f"% --- {header} ---")
        for g in sorted(buckets[bucket_idx], key=lambda x: x.name):
            r = resolutions[("macro", g.name)]
            if r.kind == "skip":
                lines.append(f"% [skip:macro] {g.name}")
                lines.append("")
                continue
            if r.kind == "split" and r.splits:
                for sp in sorted(r.splits, key=lambda s: s.global_name):
                    lines.append(sp.verbatim)
                    ps = ", ".join(f"arXiv-{p}" for p in sorted(set(sp.papers)))
                    lines.append(f"% renamed from \\{sp.original_local_name}; bodies from {ps}")
                    lines.append("")
                continue
            if r.kind == "pending" or r.verbatim is None:
                continue
            lines.append(r.verbatim)
            lines.append(provenance_comment(r, g))
            lines.append("")
    MACROS_OUT.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--no-stop", action="store_true",
                   help="Auto-resolve every undecided conflict using the most-popular-variant heuristic.")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse and report pending conflicts; write nothing.")
    p.add_argument("--arxiv-dir", type=Path, default=ARXIV_DIR,
                   help=f"Directory containing the per-paper sidecars (default: {ARXIV_DIR}).")
    return p.parse_args()


def die(msg: str, code: int = 2) -> None:
    print(f"merge_preambles: {msg}", file=sys.stderr)
    raise SystemExit(code)


def commit_progress(
    passopts_groups: list[Group],
    package_groups: list[Group],
    macro_groups: list[Group],
    resolutions: dict[tuple[str, str], Resolution],
    arxiv_dir: Path,
) -> None:
    """Flush the current resolution map to disk. Called after every prompted answer."""
    write_dependencies(passopts_groups, package_groups, resolutions)
    write_macros(macro_groups, resolutions)
    write_macro_maps(macro_groups, resolutions, arxiv_dir)


def write_macro_maps(
    macro_groups: list[Group],
    resolutions: dict[tuple[str, str], Resolution],
    arxiv_dir: Path,
) -> None:
    """Emit one arXiv-YYMM-macro-map.json per paper, recording local->global.

    Schema: {"paper": "arXiv-YYMM", "mappings": {<local_name>: <global_name | null>}}.
    Only papers that contribute at least one macro get a file. Identity
    mappings ARE recorded (so consumers can see "this paper's \\X was reviewed
    and left alone") — mirrors the bib citation-map convention. Null means
    the name was skipped from the merged preamble.
    """
    per_paper: dict[str, dict[str, str | None]] = defaultdict(dict)
    for g in macro_groups:
        r = resolutions.get(("macro", g.name))
        if r is None or r.kind == "pending":
            continue
        if r.kind == "skip":
            for v in g.variants:
                for p in set(v.papers):
                    per_paper[p][g.name] = None
            continue
        if r.kind == "split" and r.splits:
            for sp in r.splits:
                for p in set(sp.papers):
                    per_paper[p][g.name] = sp.global_name
            continue
        # kept / auto-identical / pick / custom — local name unchanged
        for v in g.variants:
            for p in set(v.papers):
                per_paper[p][g.name] = g.name

    for yymm, mappings in per_paper.items():
        out_path = arxiv_dir / f"arXiv-{yymm}-macro-map.json"
        data = {
            "paper": f"arXiv-{yymm}",
            "mappings": dict(sorted(mappings.items())),
        }
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(out_path)


def main() -> int:
    args = parse_args()

    passopts_groups, package_groups, macro_groups = load_all_definitions(args.arxiv_dir)
    state = load_existing_state()

    resolutions: dict[tuple[str, str], Resolution] = {}
    stats: dict = defaultdict(int)

    pending_total = (
        precount_pending(passopts_groups, state)
        + precount_pending(package_groups, state)
        + precount_pending(macro_groups, state)
    )

    if args.dry_run:
        print(
            f"[dry-run] {pending_total} conflict(s) still need a decision "
            f"(passopts groups: {len(passopts_groups)}, "
            f"packages: {len(package_groups)}, macros: {len(macro_groups)})",
            file=sys.stderr,
        )
    elif pending_total and not args.no_stop:
        print(
            f"[merge] {pending_total} conflict(s) need interactive resolution.",
            file=sys.stderr,
        )

    conflict_idx = [0]

    def process(groups: list[Group]) -> None:
        for g in groups:
            r = resolve_group(
                g, state,
                no_stop=args.no_stop, dry_run=args.dry_run,
                conflict_idx=conflict_idx, conflict_total=pending_total, stats=stats,
            )
            resolutions[(g.category, g.name)] = r
            # Persist immediately on user-driven decisions so quit-mid-run is safe.
            if r.kind in ("pick", "custom", "skip") and not args.dry_run:
                commit_progress(passopts_groups, package_groups, macro_groups, resolutions, args.arxiv_dir)

    try:
        process(passopts_groups)
        process(package_groups)
        process(macro_groups)
    except QuitRequested:
        commit_progress(passopts_groups, package_groups, macro_groups, resolutions, args.arxiv_dir)
        print(
            "Aborted by user. Decisions made so far are saved in "
            f"{DEPS_OUT.name}/{MACROS_OUT.name}; re-run to continue.",
            file=sys.stderr,
        )
        return 3

    if args.dry_run:
        print(
            f"[dry-run] kept: {stats['kept']}, kept-skip: {stats['kept_skip']}, "
            f"auto-identical: {stats['auto_identical']}, pending: {stats['pending']}.",
            file=sys.stderr,
        )
        return 0

    commit_progress(passopts_groups, package_groups, macro_groups, resolutions, args.arxiv_dir)

    n_pass = sum(1 for k, r in resolutions.items() if k[0] == "passopts" and r.kind != "skip" and r.verbatim)
    n_pkg = sum(1 for k, r in resolutions.items() if k[0] == "package" and r.kind != "skip" and r.verbatim)
    n_mac = sum(1 for k, r in resolutions.items() if k[0] == "macro" and r.kind != "skip" and r.verbatim)
    print(
        f"[merge] kept: {stats['kept']}, kept-skip: {stats['kept_skip']}, "
        f"auto-identical: {stats['auto_identical']}, "
        f"auto-popular: {stats['auto_popular']}, "
        f"picked: {stats.get('chose_pick', 0)}, "
        f"custom: {stats.get('chose_custom', 0)}, "
        f"new-skip: {stats.get('chose_skip', 0)}",
        file=sys.stderr,
    )
    print(f"[write] {DEPS_OUT.relative_to(REPO_ROOT)} ({n_pass} preempt + {n_pkg} packages)", file=sys.stderr)
    print(f"[write] {MACROS_OUT.relative_to(REPO_ROOT)} ({n_mac} macros)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
