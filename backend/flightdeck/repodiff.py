"""
repo-diff — a local, read-only git diff viewer, adopted as a FlightDeck tool.

Idea from doc-drafts/deliverable-vs-odoo12CE_legal.html (a one-off GitHub-style
deliverable diff) turned into a live "Diff" instrument: pick a repo + base/head
refs, browse the changed-file tree, read a side-by-side diff, and scope the
diff to a span of commits.

SAFETY: only git subcommands from an explicit allowlist ever run. The ONLY
commands that touch the network are `fetch` (--prune) and `pull` (--ff-only).
There is no path to push / rebase / reset / checkout / merge / commit / gc /
branch-write. git always runs with a fixed argv list (never a shell string),
cwd pinned to a repo that resolves inside the workspace.
"""
import os
import subprocess

# Workspace root that holds the local repos. On the host this is the Projects
# dir (parent of token-audit/); overridable for the container mount.
WORKSPACE = os.path.realpath(
    os.environ.get("FLIGHTDECK_WORKSPACE")
    or os.path.join(os.path.dirname(__file__), "..", "..")
)

READ_CMDS = {
    "rev-parse", "for-each-ref", "branch", "log", "diff", "show",
    "ls-tree", "cat-file", "merge-base", "status", "worktree",
    "symbolic-ref", "rev-list",
}
NET_CMDS = {"fetch", "pull"}
ALLOWED = READ_CMDS | NET_CMDS
FORBIDDEN_TOKENS = {
    "push", "rebase", "reset", "checkout", "switch", "merge", "commit",
    "cherry-pick", "revert", "clean", "gc", "am", "apply", "stash",
    "update-ref", "filter-branch", "reflog", "tag", "remote", "config",
    "--exec", "--upload-pack", "--receive-pack", "-c",
}


# When the workspace is bind-mounted into a container, the repos are owned by
# the host uid and git refuses with "dubious ownership". Inject safe.directory
# via env (GIT_CONFIG_*) rather than a `-c` flag, which the allowlist forbids.
# Harmless on the host. Read-only ops only; nothing here writes config on disk.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
    "GIT_TERMINAL_PROMPT": "0",  # never block on a credential prompt
}


class GitError(Exception):
    pass


def _repo_dir(name):
    """Resolve a repo NAME (top-level workspace entry) to its working dir iff it
    is a git repo living inside the workspace. Rejects traversal."""
    if not name or "/" in name or name.startswith("."):
        return None
    real = os.path.realpath(os.path.join(WORKSPACE, name))
    if real != WORKSPACE and not real.startswith(WORKSPACE + os.sep):
        return None
    if not os.path.isdir(real):
        return None
    try:
        out = subprocess.run(["git", "-C", real, "rev-parse", "--is-inside-work-tree"],
                             capture_output=True, text=True, timeout=10, env=_GIT_ENV)
    except Exception:
        return None
    return real if out.returncode == 0 and out.stdout.strip() == "true" else None


def git(repo_real, args, timeout=60):
    if not args:
        raise GitError("empty git command")
    sub = args[0]
    if sub not in ALLOWED:
        raise GitError(f"git '{sub}' is not allowed")
    for tok in args:
        if str(tok).lower() in FORBIDDEN_TOKENS:
            raise GitError(f"forbidden token: {tok}")
    if sub == "pull":
        args = ["pull", "--ff-only"] + args[1:]
    if sub == "fetch":
        args = ["fetch", "--prune"] + args[1:]
    proc = subprocess.run(["git", "-C", repo_real] + args,
                          capture_output=True, text=True, timeout=timeout, env=_GIT_ENV)
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def _need(name):
    real = _repo_dir(name)
    if not real:
        raise GitError("unknown or invalid repo")
    return real


# --- public API used by the FastAPI routes ---------------------------------
def list_repos():
    repos, seen = [], set()
    try:
        entries = sorted(os.listdir(WORKSPACE))
    except OSError:
        entries = []
    for name in entries:
        real = _repo_dir(name)
        if not real:
            continue
        try:
            head = git(real, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
            short = git(real, ["rev-parse", "--short", "HEAD"]).strip()
        except GitError:
            head, short = "?", "?"
        key = os.path.realpath(real)
        if key in seen:
            continue
        seen.add(key)
        repos.append({"name": name, "head": head, "short": short})
    return {"repos": repos}


def list_refs(name):
    real = _need(name)
    fmt = "%(refname:short)\t%(objectname:short)"

    def parse(scope):
        out = []
        for ln in git(real, ["for-each-ref", "--sort=-committerdate",
                             f"--format={fmt}", scope]).splitlines():
            p = ln.split("\t")
            if len(p) >= 2 and not p[0].endswith("/HEAD"):
                out.append({"name": p[0], "sha": p[1]})
        return out

    return {"local": parse("refs/heads"), "remote": parse("refs/remotes")}


def _numstat(real, base, head):
    out = {}
    for ln in git(real, ["diff", "--numstat", base, head]).splitlines():
        p = ln.split("\t")
        if len(p) >= 3:
            out[p[2]] = (None if p[0] == "-" else int(p[0]),
                         None if p[1] == "-" else int(p[1]))
    return out


def compare(name, base, head):
    real = _need(name)
    base_sha = git(real, ["rev-parse", base]).strip()
    head_sha = git(real, ["rev-parse", head]).strip()

    commits = []
    for ln in git(real, ["log", "--reverse", "--pretty=%H\t%h\t%an\t%ad\t%s",
                         "--date=short", f"{base}..{head}"]).splitlines():
        p = ln.split("\t", 4)
        if len(p) == 5:
            commits.append({"sha": p[0], "short": p[1], "author": p[2],
                            "date": p[3], "subject": p[4], "idx": len(commits)})
    commits_display = list(reversed(commits))  # newest first

    ns = _numstat(real, base, head)
    files, add, dele = [], 0, 0
    for ln in git(real, ["diff", "--name-status", "-M", base, head]).splitlines():
        p = ln.split("\t")
        if p[0].startswith("R") and len(p) >= 3:
            old_path, path, st = p[1], p[2], "R"
        else:
            old_path, path, st = None, p[-1], p[0][0]
        a, d = ns.get(path, (0, 0))
        a, d = a or 0, d or 0
        add += a
        dele += d
        files.append({"path": path, "old_path": old_path, "status": st,
                      "additions": a, "deletions": d})
    files.sort(key=lambda f: f["path"])
    return {"base": base, "head": head, "base_sha": base_sha, "head_sha": head_sha,
            "commits": commits_display, "files": files,
            "shortstat": {"files": len(files), "additions": add, "deletions": dele}}


def file_diff(name, base, head, path):
    real = _need(name)
    txt = git(real, ["diff", "--no-color", "-M", base, head, "--", path])
    return {"path": path, "diff": txt, "lines": txt.count("\n")}


def do_fetch(name):
    return {"ok": True, "output": git(_need(name), ["fetch", "--all"], timeout=120) or "fetched"}


def do_pull(name):
    return {"ok": True, "output": git(_need(name), ["pull"], timeout=120)}
