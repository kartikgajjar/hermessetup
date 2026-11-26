#!/usr/bin/env python
"""dispatch.py: fingerprints, the verifier launcher and integration (bootstrap v2.6, 2.5 and 2.7).

  fingerprint <item> <row> [--base REF]   print the packet's product and support fingerprints
  verify <item> <row> [--base REF] [--author]
        Runs the row's proof command. Without --author it launches hs-verifier in its own context
        (headless 'claude -p --agent hs-verifier --output-format json --json-schema ...'), validates
        its output against tools/schemas/verifier-output.json, and writes one verdict record under
        docs/prd/verdicts/. With --author the record is the author's (only where the bar allows).
  reproof <item> <row>   support-only change: rerun the proof and refresh the record
  integrate <item> [--mode local|pr] [--auto] [--base REF]
        Gate checks on the branch, then: local = merge in a scratch worktree, rerun the proof on the
        merge result, fast-forward the default branch; pr = push the branch and open a pull request.
        --auto (merge with no human step) needs working level A2 or higher on every touched path.
FACTORY_VERIFIER_CMD (tests only) replaces the claude command; it reads the prompt on stdin.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factory_common as fc  # noqa: E402

VERIFIER_TOOLS = ",".join([
    "Read", "Grep", "Glob", "Bash(python tools/check.py *)", "Bash(python tools/check.py)",
    "Bash(python tools/mcp_probe.py *)", "Bash(pwsh -NoProfile *)", "Bash(git diff *)", "Bash(git -C * diff *)",
    "Bash(git log *)", "Bash(git -C * log *)", "Bash(git status *)", "Bash(git -C * status *)",
    "Bash(npm --prefix chrome-mcp test)", "Skill"])


def run_shell(cmd: str, cwd) -> tuple:
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def evidence_path(item, row, tag) -> Path:
    d = fc.main_root() / ".factory" / "evidence"
    d.mkdir(parents=True, exist_ok=True)
    return d / ("%s-r%s-%s-%s.log" % (item, row, fc.now().replace(":", "").replace("+", "p")[:17], tag))


def packet(root, item, row, base):
    it = fc.find_item(fc.backlog(root), item)
    task = fc.find_task(it, row)
    files = fc.changed_files(root, base, task.get("owned_files", []))
    product, support = fc.split_files(files)
    return it, task, product, support


def cmd_fingerprint(a):
    root = fc.repo_root()
    _, _, product, support = packet(root, a.item, a.row, a.base)
    print(json.dumps({"product_files": product, "product_fingerprint": fc.fingerprint(root, product),
                      "support_files": support, "support_fingerprint": fc.fingerprint(root, support)}, indent=2))


def verifier_prompt(item, row, task, product, support, base) -> str:
    return "\n".join([
        "You are hs-verifier. Verify packet %s row %s. You are not its author." % (item, row),
        "Task: %s" % task["task"],
        "Bar: %s | live: %s | verdict: verifier" % (task["proof"], task.get("bar", {}).get("live", "none")),
        "Changed product files: %s" % (", ".join(product) or "none"),
        "Changed support files: %s" % (", ".join(support) or "none"),
        "Diff base: %s (use git diff %s -- <files>)." % (base, base),
        "Use the verify-hermessetup skill: run the bar on the real surface, audit the diff, never edit files.",
        "Return only the JSON your schema asks for."])


def launch_verifier(root, prompt: str, model=None) -> tuple:
    schema = (HERE / "schemas" / "verifier-output.json").read_text(encoding="utf-8")
    override = os.environ.get("FACTORY_VERIFIER_CMD")
    if override:
        r = subprocess.run(override, shell=True, input=prompt, capture_output=True, text=True, cwd=root,
                           encoding="utf-8", errors="replace")
    else:
        if not shutil.which("claude"):
            raise fc.Refused("claude CLI not found; cannot launch the verifier")
        cmd = ["claude", "-p", prompt, "--agent", "hs-verifier", "--output-format", "json",
               "--json-schema", schema, "--permission-mode", "dontAsk", "--allowedTools", VERIFIER_TOOLS]
        if model:
            cmd += ["--model", model]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=root, encoding="utf-8", errors="replace",
                           timeout=1800)
    raw = r.stdout or ""
    try:
        outer = json.loads(raw)
    except ValueError:
        raise fc.Refused("verifier output is not JSON (exit %s): %s" % (r.returncode, raw[:300]))
    out = outer.get("structured_output", outer) if isinstance(outer, dict) else outer
    errs = fc.validate(out, json.loads(schema))
    if errs:
        raise fc.Refused("verifier output fails tools/schemas/verifier-output.json: " + "; ".join(errs[:5]))
    return out, (outer.get("session_id") if isinstance(outer, dict) else None), raw


def write_record(root, rec: dict) -> Path:
    schema = fc.load_json(HERE / "schemas" / "verdict.json")
    errs = fc.validate(rec, schema)
    if errs:
        raise fc.Refused("verdict record fails tools/schemas/verdict.json: " + "; ".join(errs[:5]))
    d = fc.verdict_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamp = rec["time"].replace(":", "").replace("-", "")[:15]
    p = d / ("%s-r%s-%s-%s.json" % (rec["item"], rec["row"], stamp, rec["run_id"][:8]))
    fc.save_json(p, rec)
    return p


def cmd_verify(a):
    root = fc.repo_root()
    it, task, product, support = packet(root, a.item, a.row, a.base)
    if not product and not support:
        raise fc.Refused("the packet changed no files against %s" % a.base)
    need = fc.required_role(root, task, product)
    by = "author" if a.author else "verifier"
    if by == "author" and need != "author":
        raise fc.Refused("the bar for %s/%s needs a verifier verdict (tier and risk paths)" % (a.item, a.row))
    prior = fc.load_records(root, a.item, a.row)
    run_id = uuid.uuid4().hex
    code, out = run_shell(task["proof"], root)
    ev = evidence_path(a.item, a.row, by)
    ev.write_text("$ %s\nexit %d\n%s" % (task["proof"], code, out), encoding="utf-8")
    session = None
    features, notes = [], ""
    if by == "verifier":
        risky = any(fc.matches(f, fc.risk_globs(root)) for f in product)
        model = fc.profile(root).get("models", {}).get("standard") if risky else None
        vout, session, raw = launch_verifier(root, verifier_prompt(a.item, a.row, task, product, support, a.base),
                                             model)
        with open(ev, "a", encoding="utf-8") as f:
            f.write("\n--- verifier output ---\n" + raw)
        verdict = vout["verdict"]
        features, notes = vout.get("feature_ids", []), vout.get("notes", "")
        if code != 0 and verdict != "FAIL":
            verdict, notes = "FAIL", ("launcher proof run failed (exit %d); " % code) + notes
    else:
        verdict = "PASS" if code == 0 else "FAIL"
        features = task.get("bar", {}).get("feature_ids", [])
    rec = {"item": a.item, "row": int(a.row), "verdict": verdict, "by": by, "run_id": run_id,
           "session_id": session, "feature_ids": features, "evidence": str(ev), "notes": notes[:500],
           "product_files": product, "product_fingerprint": fc.fingerprint(root, product),
           "support_files": support, "support_fingerprint": fc.fingerprint(root, support),
           "base_commit": fc.merge_base(root, a.base), "time": fc.now(), "proof": task["proof"],
           "proof_exit": code}
    if a.approval:
        rec["approval"] = a.approval
    p = write_record(root, rec)
    fc.append_event({"kind": "verify", "item": a.item, "row": str(a.row), "by": by, "verdict": verdict})
    if prior:
        fc.append_event({"kind": "reverify", "item": a.item, "row": str(a.row),
                         "cause": "prior %s" % prior[-1].get("verdict")})
    print("%s by %s -> %s" % (verdict, by, os.path.relpath(p, root)))
    return 0 if verdict != "FAIL" else 1


def cmd_reproof(a):
    root = fc.repo_root()
    ok, reason, rec = fc.standing(root, a.item, a.row)
    if not ok or "support files changed" not in reason:
        raise fc.Refused("reproof is for a standing verdict whose support files changed (now: %s)" % reason)
    code, out = run_shell(rec["proof"], root)
    ev = evidence_path(a.item, a.row, "reproof")
    ev.write_text("$ %s\nexit %d\n%s" % (rec["proof"], code, out), encoding="utf-8")
    new = {k: v for k, v in rec.items() if not k.startswith("_")}
    new.update({"run_id": uuid.uuid4().hex, "time": fc.now(), "evidence": str(ev), "proof_exit": code,
                "support_fingerprint": fc.fingerprint(root, rec["support_files"]),
                "refreshed_from": rec["run_id"]})
    if code != 0:
        new["verdict"], new["notes"] = "FAIL", "proof failed after a support-only change"
    write_record(root, new)
    fc.append_event({"kind": "reverify", "item": a.item, "row": str(a.row), "cause": "support-only change"})
    print("reproof %s/%s: %s" % (a.item, a.row, new["verdict"]))
    return 0 if code == 0 else 1


def gate(root, base, extra=()):
    r = subprocess.run([sys.executable, str(HERE / "gate.py"), "--base", base, *extra], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def reopen(item, rows, why):
    import status  # noqa: WPS433 (status.py is the row writer)
    for n in rows:
        status.mutate(item, lambda st, n=n: st["rows"].setdefault(str(n), status.new_row()).update(
            {"status": "todo", "evidence": why[:300], "updated": fc.now()}))


def cmd_integrate(a):
    root = fc.repo_root()
    prof = fc.profile(root)
    base = a.base or prof.get("default_branch", "main")
    branch = fc.git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if branch in (base, "HEAD"):
        raise fc.Refused("integrate runs from the packet's branch, not %s" % branch)
    if fc.git("status", "--porcelain", "--untracked-files=no", cwd=root):
        raise fc.Refused("uncommitted changes on %s: commit the packet first" % branch)
    it = fc.find_item(fc.backlog(root), a.item)
    rows = [t["n"] for t in it.get("tasks", [])]
    files = fc.changed_files(root, base)
    product, _ = fc.split_files(files)
    lv = fc.level_for_repo(root)
    risky = any(fc.matches(f, fc.risk_globs(root)) for f in product)
    level = lv["working_on_risk"] if risky else lv["working_off_risk"]
    if a.auto and fc.LEVELS.index(level) < fc.LEVELS.index("A2"):
        raise fc.Refused("a merge with no human step needs A2; working level here is %s%s"
                         % (level, " (risk paths touched)" if risky else ""))
    mode = a.mode or prof.get("enforcement", {}).get("integration", "local")
    code, out = gate(root, base)
    if code != 0:
        fc.append_event({"kind": "gate-fail", "item": a.item, "where": "branch"})
        print(out)
        raise fc.Refused("gate checks failed on %s" % branch)
    if mode == "pr":
        fc.git("push", "-u", "origin", branch, cwd=root)
        r = subprocess.run(["gh", "pr", "create", "--base", base, "--head", branch, "--fill"], cwd=root,
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        return r.returncode
    # local: merge in a scratch worktree, prove the merge result, then fast-forward the default branch.
    scratch = fc.main_root() / ".factory" / ("merge-" + uuid.uuid4().hex[:8])
    fc.git("worktree", "add", "--detach", str(scratch), base, cwd=root)
    try:
        r = subprocess.run(["git", "-C", str(scratch), "merge", "--no-ff", "--no-edit", branch],
                           capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["git", "-C", str(scratch), "merge", "--abort"], capture_output=True)
            raise fc.Refused("merge conflict against %s: %s" % (base, r.stdout.strip()[-300:]))
        code, out = gate(scratch, fc.git("rev-parse", base, cwd=root), ["--merge-result"])
        merged = fc.git("rev-parse", "HEAD", cwd=scratch)
        if code != 0:
            fc.append_event({"kind": "merge-result-fail", "item": a.item})
            reopen(a.item, rows, "merge-result proof failed: " + out.strip()[-200:])
            print(out)
            raise fc.Refused("proof failed on the merge result; rows reopened")
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(scratch)], capture_output=True)
    holder = None
    for block in fc.git("worktree", "list", "--porcelain", cwd=root).split("\n\n"):
        if "branch refs/heads/%s" % base in block:
            holder = block.splitlines()[0].split(" ", 1)[1]
    if holder:
        r = subprocess.run(["git", "-C", holder, "merge", "--ff-only", merged], capture_output=True, text=True)
        if r.returncode != 0:
            raise fc.Refused("could not fast-forward %s in %s: %s" % (base, holder, r.stderr.strip()))
    else:
        fc.git("update-ref", "refs/heads/%s" % base, merged, cwd=root)
    fc.append_event({"kind": "integrated", "item": a.item, "commit": merged})
    print("merged %s into %s at %s; proof passed on the merge result" % (branch, base, merged[:10]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dispatch.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("fingerprint", "verify", "reproof"):
        s = sub.add_parser(name)
        s.add_argument("item")
        s.add_argument("row")
        s.add_argument("--base", default="main")
        if name == "verify":
            s.add_argument("--author", action="store_true")
            s.add_argument("--approval", help="U<n> approving an agent-config change")
    s = sub.add_parser("integrate")
    s.add_argument("item")
    s.add_argument("--mode", choices=("local", "pr"))
    s.add_argument("--auto", action="store_true")
    s.add_argument("--base")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "fingerprint":
            return cmd_fingerprint(a) or 0
        if a.cmd == "verify":
            return cmd_verify(a)
        if a.cmd == "reproof":
            return cmd_reproof(a)
        if a.cmd == "integrate":
            return cmd_integrate(a)
        ap.print_help()
        return 0
    except fc.Refused as e:
        fc.refuse(str(e))


if __name__ == "__main__":
    sys.exit(main())
