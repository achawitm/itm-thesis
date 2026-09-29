#!/usr/bin/env python3
"""Claude Code PreToolUse hook: enforce READ-first / PI-authorised WRITE.

Reads the hook event (JSON) on stdin. Exit 0 allows the call; exit 2 blocks it and
sends the reason to the model. Any internal error also blocks (fail closed).

Mode comes from `.brain/grant.json`, written only by the Human PI with brain_grant.py.
No valid, unexpired grant means READ mode. See 00_START_HERE/README.md.
"""
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_lib as lib  # noqa: E402

GRANT_PATH = ".brain/grant.json"
HOW = ("Ask the Human PI to run, in their own terminal (in Claude Code, prefix with `!`): "
       "python3 00_START_HERE/tools/brain_grant.py grant --scope '<glob>' --minutes 120")
SEPARATORS = set(";|&()")


def glob_re(pat):
    if pat.endswith("/**"):
        return re.compile("^" + glob_re(pat[:-3]).pattern[1:-1] + "(?:/.*)?$")
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(rel, patterns):
    return any(glob_re(p).match(rel) for p in patterns)


def load_grant(root, now):
    p = Path(root) / GRANT_PATH
    if not p.is_file():
        return None
    try:
        g = json.loads(p.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(g["expires"].replace("Z", "+00:00"))
        if g.get("granted_by") != "PI" or exp <= now or not g.get("scopes"):
            return None
        return g
    except (ValueError, KeyError, TypeError):
        return None


def locked_paths(root):
    schemas, workflow = lib.load_schemas(root)
    locked = set()
    for name in ("chapters", "artifacts"):
        header, rows, problems = lib.read_csv(Path(root) / schemas[name]["path"])
        if header != schemas[name]["columns"] or problems:
            raise ValueError(f"{schemas[name]['path']} is missing or malformed")
        for r in rows:
            if name == "chapters" and r["phase"] in workflow["translation_gate"]["locked_phases"]:
                locked.add(r["th_path"])
            if name == "artifacts" and r["state"] == "LOCKED":
                locked.add(r["path"])
    return locked, workflow


def write_ok(path_str, cwd, ctx):
    """(allowed, reason) for writing one path."""
    root, cfg, grant = ctx["root"], ctx["cfg"], ctx["grant"]
    p = (Path(cwd) / path_str).resolve()
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        return True, ""  # outside the repository (scratchpad, /dev/null, /tmp)
    if matches(rel, cfg["deny_write"]):
        return False, f"{rel} is protected governance state; only the Human PI edits it (or brain_transition.py for state)"
    try:
        locked, workflow = locked_paths(root)
    except Exception as exc:  # unreadable registries: fail closed
        return False, f"cannot read registries to check locks ({exc}); the Human PI must repair them"
    if rel in locked:
        return False, f"{rel} is LOCKED; change it via a TH_REVISION/unlock transition, not an edit"
    if lib.ethics_block_reasons(lib.parse_project_state(root), ctx["now"].date()):
        if matches(rel, [d + "/**" for d in workflow["ethics"]["participant_data_dirs"]]):
            return False, f"{rel} is participant data and the ethics guard is blocking"
    if grant is None:
        return False, f"READ mode: no PI write grant, so {rel} cannot be written. {HOW}"
    if not matches(rel, grant["scopes"]):
        return False, f"{rel} is outside the granted scope {grant['scopes']}. {HOW}"
    return True, ""


def is_op(tok):
    return tok and all(c in ";|&()<>" for c in tok)


def split_bash(cmd):
    """Return (segments, redirect_targets) or raise ValueError."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    toks = list(lex)
    segs, cur, targets, i = [], [], [], 0
    while i < len(toks):
        t = toks[i]
        if is_op(t) and ("<" in t or ">" in t):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if ">" in t and not (nxt == "/dev/null" or (t.endswith("&") and nxt.isdigit())):
                targets.append(nxt)
            i += 2
            continue
        if is_op(t) and set(t) <= SEPARATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
        i += 1
    if cur:
        segs.append(cur)
    return segs, targets


def bash_ok(cmd, cwd, ctx):
    cfg, grant, root = ctx["cfg"], ctx["grant"], ctx["root"]
    if re.search(r"\.brain\b|brain_grant|brain_guard|guard\.json|\.claude\b|core\.hooksPath|--no-verify", cmd):
        return False, "commands touching the grant, the guard, Claude settings or hook bypasses are never allowed"
    if any(x in cmd for x in ("$(", "`", "<(", ">(", "<<", "\n")):
        return False, "command substitution, heredocs and multi-line commands cannot be inspected; use one plain command"
    try:
        segs, targets = split_bash(cmd)
    except ValueError as exc:
        return False, f"cannot parse command ({exc})"
    for t in targets:
        ok, why = write_ok(t, cwd, ctx)
        if not ok:
            return False, f"redirect to {t}: {why}"
    for words in segs:
        name = words[0]
        args = words[1:]
        nonflag = [a for a in args if not a.startswith("-")]
        if re.match(r"^\w+=", name):
            return False, "environment-variable prefixes are not allowed"
        if name in cfg["bash_readonly"]:
            if name == "find" and any(a in cfg["find_forbidden_flags"] for a in args):
                return False, "find with -delete/-exec/-fprint is not read-only"
        elif name in cfg["bash_write_cmds"]:
            if name == "dd":
                paths = [a.split("=", 1)[1] for a in args if a.startswith("of=")]
            else:
                paths = nonflag[-1:] if name in cfg["bash_last_arg_only"] else nonflag
            if grant is None:
                return False, f"READ mode: `{name}` modifies files. {HOW}"
            for a in paths:
                ok, why = write_ok(a, cwd, ctx)
                if not ok:
                    return False, f"{name} {a}: {why}"
        elif name == "git":
            ok, why = git_ok(args, cwd, ctx)
            if not ok:
                return False, why
        elif name in ("python3", "python"):
            ok, why = python_ok(args, cwd, ctx)
            if not ok:
                return False, why
        elif name in cfg["bash_extra_allow"] and grant is not None:
            continue
        else:
            return False, f"`{name}` is not in the guard allowlist (guard.json bash_readonly / bash_write_cmds); the PI can extend it"
    return True, ""


def git_ok(args, cwd, ctx):
    cfg, grant = ctx["cfg"], ctx["grant"]
    if not args or args[0].startswith("-"):
        return False, "git global options (-C, -c, ...) are not allowed"
    sub, rest = args[0], args[1:]
    if sub in cfg["git_forbidden"] or any(a in cfg["git_forbidden_flags"] for a in rest):
        return False, f"git {sub} with those flags can destroy work or bypass the hooks"
    if sub == "checkout" and "--" in rest:
        return False, "git checkout -- discards changes"
    if sub in cfg["git_read"]:
        return True, ""
    if sub in cfg["git_write"]:
        if grant is None:
            return False, f"READ mode: git {sub} changes the repository. {HOW}"
        if sub in cfg["git_path_args"]:
            for a in [a for a in rest if not a.startswith("-")]:
                ok, why = write_ok(a, cwd, ctx)
                if not ok:
                    return False, f"git {sub} {a}: {why}"
        return True, ""
    return False, f"git {sub} is not in the guard allowlist"


def python_ok(args, cwd, ctx):
    cfg, grant, root = ctx["cfg"], ctx["grant"], ctx["root"]
    if args[:2] == ["-m", "unittest"]:
        return True, ""
    if not args or args[0].startswith("-"):
        return False, "python -c / inline code cannot be inspected"
    script = (Path(cwd) / args[0]).resolve()
    tools = (root / "00_START_HERE/tools").resolve()
    if script.parent != tools:
        return False, f"only the brain_* tools under 00_START_HERE/tools may be run, not {args[0]}"
    if script.name in cfg["python_readonly_tools"]:
        return True, ""
    if script.name == cfg["python_transition_tool"]:
        if grant is None or not grant.get("transitions"):
            return False, f"state transitions need a PI grant made with --transitions. {HOW} --transitions"
        rest = args[1:]
        for i, a in enumerate(rest):
            val = a.split("=", 1)[1] if a.startswith("--actor=") else (rest[i + 1] if a == "--actor" and i + 1 < len(rest) else None)
            if val is not None and not val.startswith("AI:"):
                return False, "the AI must record itself as --actor AI:<session>, never PI"
        return True, ""
    return False, f"{script.name} is not an allowed tool"


def decide(event, root, now=None):
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    ctx = {"root": root, "now": now, "cfg": lib.load_json(root, "guard.json"), "grant": load_grant(root, now)}
    tool, ti = event.get("tool_name"), event.get("tool_input") or {}
    cwd = event.get("cwd") or str(root)
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = ti.get("file_path") or ti.get("notebook_path")
        if not target:
            return False, "write tool call without a target path"
        return write_ok(target, cwd, ctx)
    if tool == "Bash":
        return bash_ok(ti.get("command", ""), cwd, ctx)
    return True, ""


def main():
    try:
        event = json.load(sys.stdin)
        ok, reason = decide(event, lib.DEFAULT_ROOT)
    except Exception as exc:  # exit 1 would not block, so fail closed with exit 2
        print(f"brain_guard: internal error, blocking: {exc}", file=sys.stderr)
        return 2
    if ok:
        return 0
    print(f"brain_guard: BLOCKED. {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
