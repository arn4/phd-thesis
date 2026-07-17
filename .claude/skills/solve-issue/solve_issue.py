#!/usr/bin/env python3
"""solve_issue.py — mechanical helper for the /solve-issue skill.

Operates on the git-ignored claude-review/ working set. It does the deterministic
parts (look up a finding, find related findings, flip `solved` + record a
`resolution`, regenerate the report). All judgment — whether a comment is a
dismissal, what the actual thesis edit should be, which related findings a fix
truly resolves — is done by the caller (Claude, following SKILL.md), NOT here.

`findings/merged.json` is the single source of truth for solved-state and drives
the report; the raw `findings/agents/*.json` snapshots are left untouched.

Subcommands:
  show    CODE                       print one finding as JSON
  related CODE                       print unsolved findings related to CODE (JSON)
  solve   CODE --note "..." [opts]   mark CODE solved (+resolution) and rebuild
            [--dismiss] [--also CODE ...] [--no-build]
"""
import argparse, json, re, subprocess, sys
from datetime import date
from pathlib import Path


def find_review(explicit=None):
    if explicit:
        p = Path(explicit)
        if (p / "findings" / "merged.json").exists():
            return p
        sys.exit(f"error: no findings/merged.json under {p}")
    seen = []
    for base in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        c = base / "claude-review"
        if c not in seen:
            seen.append(c)
            if (c / "findings" / "merged.json").exists():
                return c
    sys.exit("error: could not locate claude-review/findings/merged.json "
             "(run from the repo root, or pass --review-dir)")


def load(review):
    return json.loads((review / "findings" / "merged.json").read_text())


def save(review, data):
    (review / "findings" / "merged.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def norm_code(code):
    c = str(code).strip().upper()
    m = re.match(r"^F?0*(\d+)$", c)
    return f"F{int(m.group(1)):03d}" if m else c


def get(data, code):
    code = norm_code(code)
    for f in data:
        if f.get("id") == code:
            return f
    return None


def file_of(loc):
    m = re.match(r"([^:]+\.tex)", str(loc or ""))
    return m.group(1) if m else None


def line_of(loc):
    m = re.search(r"\.tex:(\d+)", str(loc or ""))
    return int(m.group(1)) if m else None


def words(s):
    return set(w for w in re.findall(r"[a-z0-9\\]+", str(s or "").lower()) if len(w) > 2)


def rebuild(review):
    r = subprocess.run([sys.executable, "build_report.py"], cwd=review,
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(f"error: build_report.py failed (exit {r.returncode})")


def cmd_show(args):
    review = find_review(args.review_dir)
    f = get(load(review), args.code)
    if not f:
        sys.exit(f"error: finding {norm_code(args.code)} not found")
    print(json.dumps(f, indent=2, ensure_ascii=False))


def cmd_related(args):
    review = find_review(args.review_dir)
    data = load(review)
    t = get(data, args.code)
    if not t:
        sys.exit(f"error: finding {norm_code(args.code)} not found")
    tfile, tline, tw = file_of(t["location"]), line_of(t["location"]), words(t.get("quote"))
    out = []
    for f in data:
        if f["id"] == t["id"] or f.get("solved"):
            continue
        reasons = []
        if f["location"] == t["location"]:
            reasons.append("identical location")
        elif tfile and file_of(f["location"]) == tfile:
            fl = line_of(f["location"])
            if tline and fl and abs(fl - tline) <= 3:
                reasons.append(f"same file, line within 3 (±{abs(fl-tline)})")
            fw = words(f.get("quote"))
            if tw and fw:
                j = len(tw & fw) / len(tw | fw)
                if j >= 0.34:
                    reasons.append(f"quote overlap {j:.2f}")
                elif not reasons and (t.get("quote") and f.get("quote") and
                        (t["quote"] in f["quote"] or f["quote"] in t["quote"])):
                    reasons.append("quote substring")
        if reasons:
            out.append({"id": f["id"], "source": f["source"], "severity": f["severity"],
                        "category": f["category"], "location": f["location"],
                        "quote": (f.get("quote") or "")[:120],
                        "description": (f.get("description") or "")[:200],
                        "reason": "; ".join(reasons)})
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_solve(args):
    review = find_review(args.review_dir)
    data = load(review)
    code = norm_code(args.code)
    t = get(data, code)
    if not t:
        sys.exit(f"error: finding {code} not found")
    note = args.note.strip()
    if args.dismiss and not re.match(r"(?i)\s*(dismiss|not an issue|false positive|no change)", note):
        note = f"Dismissed — {note}" if note else "Dismissed — not an issue."
    today = date.today().isoformat()
    already = " (was already solved)" if t.get("solved") else ""
    t["solved"] = True
    t["resolution"] = note
    t["solved_at"] = today

    also_done = []
    for a in (args.also or []):
        af = get(data, norm_code(a))
        if not af:
            sys.stderr.write(f"warn: --also {norm_code(a)} not found, skipping\n")
            continue
        af["solved"] = True
        af["resolution"] = f"Resolved together with {code}." + (f" {note}" if note else "")
        af["solved_at"] = today
        also_done.append(af["id"])

    save(review, data)

    total = len(data)
    solved = sum(1 for f in data if f.get("solved"))
    print(f"solved {code}{already}: {note or '(no note)'}")
    if also_done:
        print(f"also solved (resolved together): {', '.join(also_done)}")
    print(f"progress: {solved}/{total} solved")
    if not args.no_build:
        rebuild(review)
    else:
        print("(skipped report rebuild: --no-build)")


def main():
    ap = argparse.ArgumentParser(description="mechanical helper for /solve-issue")
    ap.add_argument("--review-dir", help="path to claude-review/ (auto-detected if omitted)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show"); s.add_argument("code"); s.set_defaults(fn=cmd_show)
    r = sub.add_parser("related"); r.add_argument("code"); r.set_defaults(fn=cmd_related)
    v = sub.add_parser("solve")
    v.add_argument("code")
    v.add_argument("--note", default="", help="resolution note (what was fixed / why dismissed)")
    v.add_argument("--dismiss", action="store_true", help="mark solved with no thesis change")
    v.add_argument("--also", nargs="+", default=[], help="related finding ids resolved by the same change")
    v.add_argument("--no-build", action="store_true", help="don't regenerate the report")
    v.set_defaults(fn=cmd_solve)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
