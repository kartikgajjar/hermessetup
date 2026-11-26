#!/usr/bin/env python
"""ship.py: the release step (bootstrap v2.6, 2.7). Always the user's go (on the permission ask list).

  ship.py <release> --version X.Y.Z (--audit | --audit-waived REASON) [--rollback-rehearsed EVIDENCE]
          [--dry-run]
Refuses: leftovers in the tree (tracked changes), any row of the release without a standing verdict.
T2 controls at release: security review + dependency/licence audit (advisory: --audit runs
'npm audit --omit=dev' in chrome-mcp, an external pull), rollback rehearsed for a risky release
(a release touching risk paths needs --rollback-rehearsed). Writes VERSION and CHANGELOG.md (newest
first), marks the items shipped and commits on the default branch.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factory_common as fc  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ship.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release")
    ap.add_argument("--version", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audit", action="store_true")
    g.add_argument("--audit-waived")
    ap.add_argument("--rollback-rehearsed")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        root = fc.repo_root()
        prof = fc.profile(root)
        base = prof.get("default_branch", "main")
        if fc.git("rev-parse", "--abbrev-ref", "HEAD", cwd=root) != base:
            raise fc.Refused("ship runs on %s" % base)
        if fc.git("status", "--porcelain", "--untracked-files=no", cwd=root):
            raise fc.Refused("leftovers: tracked changes in the tree; commit or revert them first")
        bl = fc.backlog(root)
        items = [it for it in bl.get("items", []) if it.get("release") == a.release]
        if not items:
            raise fc.Refused("no items in release %s" % a.release)
        import status
        risky = False
        for it in items:
            st = status.load_item_state(it["id"])
            for t in it.get("tasks", []):
                r = st["rows"].get(str(t["n"]))
                if not r or r["status"] != "done":
                    raise fc.Refused("%s/%s is not done" % (it["id"], t["n"]))
                ok, reason, rec = fc.standing(root, it["id"], t["n"])
                if not ok:
                    raise fc.Refused("%s/%s: %s" % (it["id"], t["n"], reason))
                if any(fc.matches(f, fc.risk_globs(root)) for f in rec.get("product_files", [])):
                    risky = True
        if risky and not a.rollback_rehearsed and fc.derive_tier(prof["facts"]) in ("T2", "T3"):
            raise fc.Refused("a release touching risk paths needs --rollback-rehearsed <evidence> at T2+")
        audit = "waived: %s" % a.audit_waived if a.audit_waived else ""
        if a.audit and not a.dry_run:
            r = subprocess.run("npm --prefix chrome-mcp audit --omit=dev", shell=True, cwd=root,
                               capture_output=True, text=True)
            audit = "npm audit exit %d: %s" % (r.returncode, (r.stdout.strip().splitlines() or ["?"])[-1])
        if a.dry_run:
            print("ship %s %s: would ship %d items (dry run)" % (a.release, a.version, len(items)))
            return 0
        (Path(root) / "VERSION").write_text(a.version + "\n", encoding="utf-8")
        cl = Path(root) / "CHANGELOG.md"
        old = cl.read_text(encoding="utf-8") if cl.exists() else "# Changelog\n"
        head, _, rest = old.partition("\n")
        entry = ["", "## %s (%s), release %s" % (a.version, fc.now()[:10], a.release),
                 "approver: %s | audit: %s | rollback: %s" % (prof.get("approver", "?"), audit or "n/a",
                                                               a.rollback_rehearsed or "n/a")]
        entry += ["- %s: %s" % (it["id"], it.get("title", "")) for it in items]
        cl.write_text(head + "\n" + "\n".join(entry) + "\n" + rest, encoding="utf-8")
        for it in items:
            status.mutate(it["id"], lambda st: st.update({"status": "shipped"}))
        fc.git("add", "VERSION", "CHANGELOG.md", cwd=root)
        fc.git("commit", "-m", "Release %s (%s)" % (a.version, a.release), cwd=root)
        fc.append_event({"kind": "ship", "release": a.release, "version": a.version})
        print("shipped %s as %s" % (a.release, a.version))
        return 0
    except fc.Refused as e:
        fc.refuse(str(e))


if __name__ == "__main__":
    sys.exit(main())
