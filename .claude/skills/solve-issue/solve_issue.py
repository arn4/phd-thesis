#!/usr/bin/env python3
"""solve_issue.py — mechanical helper for the /solve-issue skill.

Operates on the git-ignored claude-review/ working set. It does the deterministic
parts (look up a finding, find related findings, run the isolated worktree, land
the commit on main, flip `solved` + record a `resolution`, regenerate the report).
All judgment — whether a comment is a dismissal, what the actual thesis edit
should be, which related findings a fix truly resolves — is done by the caller
(Claude, following SKILL.md), NOT here.

`findings/merged.json` is the single source of truth for solved-state and drives
the report; the raw `findings/agents/*.json` snapshots are left untouched.

Parallelism model
-----------------
Each finding is fixed in its own detached worktree under .claude/worktrees/, so
several instances can work at once without sharing a working tree. No branch is
ever created: the worktree commits to a detached HEAD, and `integrate`
cherry-picks that commit onto main (linear history, no merge commits).

`integrate` is the only critical section, and it is serialized across instances
by an exclusive flock on .git/solve-issue.lock. It covers the cherry-pick AND the
merged.json update AND the report rebuild, in that order — so the review folder
is only ever touched after the change has actually landed on main, and two
instances can never interleave a git merge or a read-modify-write of the JSON.
flock is released automatically when the process exits, even on SIGKILL, so a
crashed instance cannot leave a stale lock behind.

Dirty-main handling
-------------------
`git cherry-pick` refuses whenever a touched file has uncommitted changes, even
when the changes are nowhere near each other — it checks at file granularity.
This is too strict for a thesis you are always mid-edit in, so `integrate`:

  1. previews the merge with `git merge-file` (a pure function — writes nothing)
     for every file that is both touched by the commit and dirty in main;
  2. refuses up front, having touched nothing, if that preview reports a real
     conflict (overlapping or adjacent hunks);
  3. otherwise stashes just those paths, cherry-picks, and pops — which merges
     your uncommitted edits back at hunk granularity.

If the pop conflicts anyway (the preview and the real merge disagreeing), it
rolls main back to where it was and restores the stash, leaving you byte-identical
to before. Uncommitted work is never dropped: a stash is only ever dropped by a
successful pop, and every failure path prints the stash sha to recover by hand.

Subcommands:
  show      CODE                        print one finding as JSON
  related   CODE                        print unsolved findings related to CODE (JSON)
  start     CODE                        create the detached worktree, print its path
  integrate CODE --note "..." [opts]    land the fix + mark solved + rebuild (locked)
              [--worktree PATH] [--dismiss] [--also CODE ...] [--redo] [--no-build]
  abandon   CODE                        remove a worktree without integrating
"""
import argparse, contextlib, fcntl, json, os, re, subprocess, sys, tempfile, time
from datetime import date
from pathlib import Path


# ---------------------------------------------------------------- git plumbing

def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"error: git {' '.join(args)} failed in {cwd}:\n{r.stderr.strip()}")
    return r


def main_root(start=None):
    """Main repo root — correct even when called from inside a linked worktree."""
    start = Path(start or Path.cwd())
    r = git(start, "rev-parse", "--git-common-dir", check=False)
    if r.returncode != 0:
        sys.exit("error: not inside a git repository")
    gcd = Path(r.stdout.strip())
    if not gcd.is_absolute():
        gcd = (start / gcd).resolve()
    return gcd.parent


def porcelain(root):
    """{path: (index_status, worktree_status)} for everything git reports."""
    out = {}
    for line in git(root, "status", "--porcelain").stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:          # rename: take the destination
            path = path.split(" -> ", 1)[1]
        out[path.strip('"')] = (line[0], line[1])
    return out


def touched(root, sha):
    return [p for p in git(root, "show", "--name-only", "--format=", sha)
            .stdout.splitlines() if p.strip()]


def would_conflict(root, sha, path):
    """True if merging the commit's change to `path` into the dirty working copy
    would conflict. Pure preview: uses git's own 3-way merge, writes nothing.

    ours   = the working file (current main + your uncommitted edits)
    base   = the file as the worktree found it  (sha^)
    theirs = the file as the worktree left it   (sha)
    """
    base = git(root, "show", f"{sha}^:{path}", check=False)
    theirs = git(root, "show", f"{sha}:{path}", check=False)
    if base.returncode or theirs.returncode:
        return True, "could not read the before/after blobs for this path"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "base").write_text(base.stdout)
        (td / "theirs").write_text(theirs.stdout)
        try:
            (td / "ours").write_text((root / path).read_text())
        except OSError as e:
            return True, f"could not read the working copy ({e})"
        r = subprocess.run(["git", "merge-file", "-p", "--diff3",
                            str(td / "ours"), str(td / "base"), str(td / "theirs")],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return False, ""
        if r.returncode < 0 or r.returncode > 100:
            return True, f"merge preview failed ({r.stderr.strip()})"
        return True, f"{r.returncode} conflicting hunk(s) with your uncommitted edits"


# --------------------------------------------------------------------- locking

@contextlib.contextmanager
def integrate_lock(root, code, timeout=300):
    """Exclusive, repo-wide, auto-released-on-death lock around the whole
    land-on-main + update-review critical section."""
    path = root / ".git" / "solve-issue.lock"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.time() + timeout
    waited = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > deadline:
                    os.lseek(fd, 0, os.SEEK_SET)
                    holder = os.read(fd, 200).decode(errors="replace").strip()
                    sys.exit(f"error: timed out after {timeout}s waiting for the "
                             f"integrate lock, held by: {holder or 'unknown'}\n"
                             f"       if that instance is dead the lock is already "
                             f"free — just retry.")
                if not waited:
                    os.lseek(fd, 0, os.SEEK_SET)
                    holder = os.read(fd, 200).decode(errors="replace").strip()
                    print(f"waiting for integrate lock (held by {holder or 'another instance'})…",
                          file=sys.stderr)
                    waited = True
                time.sleep(0.4)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{code} pid={os.getpid()} since={time.strftime('%H:%M:%S')}".encode())
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ------------------------------------------------------------ review json bits

def find_review(explicit=None):
    if explicit:
        p = Path(explicit)
        if (p / "findings" / "merged.json").exists():
            return p
        sys.exit(f"error: no findings/merged.json under {p}")
    # claude-review/ is gitignored, so it only exists in the MAIN checkout —
    # never inside a linked worktree. Always resolve it from the main root.
    c = main_root() / "claude-review"
    if (c / "findings" / "merged.json").exists():
        return c
    sys.exit("error: could not locate claude-review/findings/merged.json "
             "(run deep-review first, or pass --review-dir)")


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
    r = subprocess.run([sys.executable, "build_report.py"], cwd=str(review),
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(f"error: build_report.py failed (exit {r.returncode})")


def worktree_path(root, code):
    return root / ".claude" / "worktrees" / f"solve-{norm_code(code).lower()}"


# -------------------------------------------------------------------- commands

def cmd_show(args):
    f = get(load(find_review(args.review_dir)), args.code)
    if not f:
        sys.exit(f"error: finding {norm_code(args.code)} not found")
    print(json.dumps(f, indent=2, ensure_ascii=False))


def cmd_related(args):
    data = load(find_review(args.review_dir))
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


def cmd_start(args):
    root = main_root()
    code = norm_code(args.code)
    wt = worktree_path(root, code)
    if wt.exists():
        sys.exit(f"error: {wt} already exists — {code} is already in flight.\n"
                 f"       finish it, or drop it with: solve_issue.py abandon {code}")
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "--detach", str(wt), args.base)
    print(str(wt))


def land(root, wt, sha):
    """Cherry-pick `sha` onto main, merging around any uncommitted edits.
    Caller must hold the integrate lock."""
    if git(root, "merge-base", "--is-ancestor", sha, "main", check=False).returncode == 0:
        sys.exit(f"error: {sha[:9]} is already an ancestor of main — the worktree has "
                 f"no new commit to integrate (did the edit get committed there?)")

    st, tp = porcelain(root), touched(root, sha)
    overlap = [p for p in tp if p in st]

    staged = [p for p in overlap if st[p][0] not in (" ", "?")]
    if staged:
        sys.exit("error: main has STAGED changes to " + ", ".join(staged) +
                 " — commit or unstage them first (refusing to touch a half-built index).")

    blocked = []
    for p in overlap:
        bad, why = would_conflict(root, sha, p)
        if bad:
            blocked.append(f"  {p}: {why}")
    if blocked:
        sys.exit("error: your uncommitted edits in main genuinely conflict with this fix:\n"
                 + "\n".join(blocked) +
                 f"\n       nothing was touched. Commit/stash those edits, then retry — "
                 f"the fix is safe in the worktree ({wt}).")

    head_before = git(root, "rev-parse", "HEAD").stdout.strip()
    stash_sha = None
    if overlap:
        git(root, "stash", "push", "-q", "--", *overlap)
        stash_sha = git(root, "rev-parse", "stash@{0}", check=False).stdout.strip()
        print(f"temporarily stashed your uncommitted edits to {', '.join(overlap)}")

    def restore_stash():
        if stash_sha and git(root, "stash", "pop", check=False).returncode != 0:
            sys.stderr.write(f"\nWARNING: could not auto-restore your uncommitted edits.\n"
                             f"They are safe — recover with: git stash apply {stash_sha}\n")

    cp = git(root, "cherry-pick", sha, check=False)
    if cp.returncode != 0:
        git(root, "cherry-pick", "--abort", check=False)
        restore_stash()
        sys.exit(f"error: cherry-pick of {sha[:9]} onto main hit a conflict with a "
                 f"COMMITTED change (another finding probably landed in the same lines):\n"
                 f"{cp.stderr.strip()}\n"
                 f"       main is untouched and the fix is still in {wt}. Rebase that "
                 f"worktree on main and redo the edit, or resolve by hand.")

    if stash_sha:
        if git(root, "stash", "pop", check=False).returncode != 0:
            # Preview and reality disagreed. Put everything back exactly as it was.
            git(root, "reset", "--hard", head_before, check=False)
            if git(root, "stash", "pop", check=False).returncode != 0:
                sys.exit(f"error: rolled main back to {head_before[:9]} but could not "
                         f"restore your edits automatically.\n"
                         f"       recover with: git stash apply {stash_sha}")
            sys.exit(f"error: the merge preview said this was safe but the real merge "
                     f"conflicted. Main and your working tree have been rolled back "
                     f"exactly as they were; the fix is still in {wt}.")
    return git(root, "rev-parse", "HEAD").stdout.strip()


def cmd_integrate(args):
    root = main_root()
    review = find_review(args.review_dir)
    code = norm_code(args.code)

    wt = None
    if args.worktree:
        wt = Path(args.worktree).resolve()
    elif not args.dismiss and worktree_path(root, code).exists():
        wt = worktree_path(root, code)
    if wt and not wt.exists():
        sys.exit(f"error: worktree {wt} does not exist")
    if not wt and not args.dismiss:
        sys.exit(f"error: no worktree for {code} and --dismiss not given. A fix must "
                 f"land a real commit; a dismissal must say so explicitly.")

    with integrate_lock(root, code, timeout=args.lock_timeout):
        data = load(review)
        t = get(data, code)
        if not t:
            sys.exit(f"error: finding {code} not found")
        if t.get("solved") and not args.redo:
            sys.exit(f"error: {code} was already solved while you were working on it "
                     f"({t.get('resolution', '')[:80]}) — another instance probably "
                     f"covered it via --also. Nothing was landed; pass --redo to "
                     f"override, or abandon this one.")

        landed = land(root, wt, git(wt, "rev-parse", "HEAD").stdout.strip()) if wt else None

        note = args.note.strip()
        if args.dismiss and not re.match(
                r"(?i)\s*(dismiss|not an issue|false positive|no change)", note):
            note = f"Dismissed — {note}" if note else "Dismissed — not an issue."
        today = date.today().isoformat()
        t["solved"] = True
        t["resolution"] = note
        t["solved_at"] = today
        if landed:
            t["commit"] = landed[:9]

        also_done = []
        for a in (args.also or []):
            af = get(data, norm_code(a))
            if not af:
                sys.stderr.write(f"warn: --also {norm_code(a)} not found, skipping\n")
                continue
            af["solved"] = True
            af["resolution"] = f"Resolved together with {code}." + (f" {note}" if note else "")
            af["solved_at"] = today
            if landed:
                af["commit"] = landed[:9]
            also_done.append(af["id"])

        save(review, data)
        total = len(data)
        solved = sum(1 for f in data if f.get("solved"))
        if landed:
            print(f"landed {landed[:9]} on main")
        print(f"solved {code}: {note or '(no note)'}")
        if also_done:
            print(f"also solved (resolved together): {', '.join(also_done)}")
        print(f"progress: {solved}/{total} solved")
        if not args.no_build:
            rebuild(review)
        else:
            print("(skipped report rebuild: --no-build)")

    # Outside the lock: the worktree is nobody else's business by now.
    if wt and not args.keep_worktree:
        if git(root, "worktree", "remove", str(wt), check=False).returncode != 0:
            git(root, "worktree", "remove", "--force", str(wt), check=False)
        git(root, "worktree", "prune", check=False)
        print(f"removed worktree {wt.name}")


def cmd_abandon(args):
    root = main_root()
    wt = Path(args.worktree).resolve() if args.worktree else worktree_path(root, args.code)
    if not wt.exists():
        sys.exit(f"error: no worktree at {wt}")
    if git(root, "worktree", "remove", str(wt), check=False).returncode != 0:
        if not args.force:
            sys.exit(f"error: {wt} has uncommitted or untracked files. Re-run with "
                     f"--force to discard them.")
        git(root, "worktree", "remove", "--force", str(wt), check=False)
    git(root, "worktree", "prune", check=False)
    print(f"abandoned {wt.name} (no thesis change, finding left unsolved)")


def main():
    ap = argparse.ArgumentParser(description="mechanical helper for /solve-issue")
    ap.add_argument("--review-dir", help="path to claude-review/ (auto-detected if omitted)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show"); s.add_argument("code"); s.set_defaults(fn=cmd_show)
    r = sub.add_parser("related"); r.add_argument("code"); r.set_defaults(fn=cmd_related)

    t = sub.add_parser("start", help="create the detached worktree for CODE")
    t.add_argument("code")
    t.add_argument("--base", default="main", help="commit-ish to branch the worktree off (default: main)")
    t.set_defaults(fn=cmd_start)

    v = sub.add_parser("integrate", help="land the fix on main, mark solved, rebuild (locked)")
    v.add_argument("code")
    v.add_argument("--worktree", help="worktree holding the fix (default: the one for CODE)")
    v.add_argument("--note", default="", help="resolution note (what was fixed / why dismissed)")
    v.add_argument("--dismiss", action="store_true", help="mark solved with no thesis change")
    v.add_argument("--also", nargs="+", default=[], help="related finding ids resolved by the same change")
    v.add_argument("--redo", action="store_true", help="proceed even if already marked solved")
    v.add_argument("--keep-worktree", action="store_true", help="don't remove the worktree afterwards")
    v.add_argument("--no-build", action="store_true", help="don't regenerate the report")
    v.add_argument("--lock-timeout", type=int, default=300, help="seconds to wait for the lock")
    v.set_defaults(fn=cmd_integrate)

    a = sub.add_parser("abandon", help="remove a worktree without integrating")
    a.add_argument("code")
    a.add_argument("--worktree")
    a.add_argument("--force", action="store_true")
    a.set_defaults(fn=cmd_abandon)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
