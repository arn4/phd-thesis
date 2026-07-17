#!/usr/bin/env python3
"""merge_findings.py — assemble claude-review/findings/merged.json from the raw
per-agent outputs in claude-review/findings/agents/*.json.

Run ONCE when assembling a fresh review (part of the `deep-review` skill). It
assigns stable ids (F001…), tags each finding with its source agent (from the
filename), normalizes fields, and adds `solved: false`. Do NOT re-run it on a
review you've already started solving — it reassigns ids and resets `solved`.
The raw agents/*.json files are left untouched (frozen record).

Usage:  python3 merge_findings.py            # auto-locate claude-review/
        python3 merge_findings.py --review-dir PATH
"""
import argparse, json, re, sys
from pathlib import Path

SEV_ORDER = {"Critical": 0, "Major": 1, "Minor": 2, "Nitpick": 3}
FIELDS = ["category", "severity", "location", "unit", "quote", "description",
          "suggested_fix", "confidence"]


def locate(explicit=None):
    if explicit:
        p = Path(explicit)
        if (p / "findings" / "agents").is_dir():
            return p
        sys.exit(f"error: no findings/agents/ under {p}")
    for base in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        c = base / "claude-review"
        if (c / "findings" / "agents").is_dir():
            return c
    sys.exit("error: could not locate claude-review/findings/agents/")


def norm_loc(loc):
    m = re.match(r"([^:]+\.tex)", str(loc or ""))
    return m.group(1) if m else str(loc or "").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-dir")
    args = ap.parse_args()
    review = locate(args.review_dir)
    agents_dir = review / "findings" / "agents"

    merged, errors, per_source, fid = [], [], {}, 0
    for p in sorted(agents_dir.glob("*.json")):
        label = p.stem.upper()  # intro-lang -> INTRO-LANG, paper-2302 -> PAPER-2302
        try:
            items = json.loads(p.read_text())
        except Exception as e:
            errors.append(f"PARSE FAIL {p.name}: {e}")
            continue
        if not isinstance(items, list):
            errors.append(f"NOT A LIST {p.name}")
            continue
        per_source[label] = len(items)
        for it in items:
            fid += 1
            row = {"id": f"F{fid:03d}", "source": label}
            for k in FIELDS:
                row[k] = it.get(k, "")
            row["solved"] = False
            merged.append(row)

    sev = {}
    for f in merged:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    by_loc = {}
    for f in merged:
        by_loc.setdefault(norm_loc(f["location"]), set()).add(f["source"])
    multi = {k: sorted(v) for k, v in by_loc.items() if len(v) > 1}

    merged.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["unit"], f["source"]))
    out = review / "findings" / "merged.json"
    out.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"merged {len(merged)} findings from {len(per_source)} agents -> {out}")
    if errors:
        print("ERRORS:", errors)
    print("per source:", {k: per_source[k] for k in sorted(per_source)})
    print("by severity:", {s: sev.get(s, 0) for s in SEV_ORDER})
    print(f"files flagged by >1 agent (corroboration candidates): {len(multi)}")
    for loc, srcs in sorted(multi.items()):
        print(f"  {loc}  {srcs}")


if __name__ == "__main__":
    main()
