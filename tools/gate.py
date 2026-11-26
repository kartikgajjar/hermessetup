#!/usr/bin/env python
"""gate.py: the factory gate (bootstrap v2.6, 2.7). Run locally by dispatch.py integrate and in CI as
the 'factory-gate' job on every pull request and every push to the default branch.

Fails unless:
  1. every changed product file is covered by a verdict record whose product fingerprint matches now;
  2. that verdict is PASS or PASS+NOTES by the role the tier and risk paths require;
  3. each covering row's proof command passes here (on the merge result in CI / --merge-result);
  4. the tier's controls pass: secret scan, privacy scan (T1+), threat model present (T2+),
     an approval U<n> recorded for agent-config (high-risk) changes (T2+), factory self-tests when
     factory files changed.
A change made only by a listed automated pusher on its listed paths is skipped.
A bootstrap merge (no factory profile at the base) is reported and not gated.

  gate.py [--base REF] [--merge-result] [--skip-proof]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factory_common as fc  # noqa: E402

SECRET_RX = [re.compile(p) for p in (
    r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", r"\bghp_[A-Za-z0-9]{30,}", r"\bgithub_pat_[A-Za-z0-9_]{30,}",
    r"\bsk-ant-[A-Za-z0-9_-]{20,}", r"\bsk-[A-Za-z0-9]{32,}", r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"(?i)\b(password|passwd|secret|api[_-]?key|client[_-]?secret)\s*[:=]\s*['\"][^'\"\s$%{]{8,}['\"]")]
EMAIL_RX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RX = re.compile(r"(?<![\w.])\+?\d{1,3}[ .-]\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])")


def read_text(root, f):
    p = Path(root) / f
    if not p.is_file() or p.stat().st_size > 2_000_000:
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def committed_changes(root, base):
    mb = fc.merge_base(root, base)
    files = set(filter(None, fc.git("diff", "--name-only", mb, "HEAD", cwd=root).splitlines()))
    files |= set(filter(None, fc.git("diff", "--name-only", mb, cwd=root).splitlines()))
    return mb, sorted(f for f in files if not fc.matches(f, fc.EXCLUDED))


def pusher_only(root, mb, f, pushers):
    authors = set(filter(None, fc.git("log", "--format=%ae", "%s..HEAD" % mb, "--", f, cwd=root).splitlines()))
    if not authors:
        return False
    for p in pushers:
        if authors <= set(p.get("authors", [])) and fc.matches(f, p.get("paths", [])):
            return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gate.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base")
    ap.add_argument("--merge-result", action="store_true")
    ap.add_argument("--skip-proof", action="store_true")
    a = ap.parse_args(argv)
    root = fc.repo_root()
    prof = fc.profile(root)
    base = a.base or prof.get("default_branch", "main")
    mb, changed = committed_changes(root, base)
    if subprocess.run(["git", "cat-file", "-e", "%s:config/factory-profile.yaml" % mb], cwd=root,
                      capture_output=True).returncode != 0:
        print("factory-gate: bootstrap merge (no factory profile at the base %s); not gated." % mb[:10])
        return 0
    tier = fc.derive_tier(prof["facts"])
    rp = fc.load_json(Path(root) / "config" / "risk-paths.yaml")
    pushers = rp.get("automated_pushers", [])
    failures, notes = [], []
    gated = [f for f in changed if not pusher_only(root, mb, f, pushers)]
    skipped = sorted(set(changed) - set(gated))
    if skipped:
        notes.append("skipped (automated pusher on its paths): %s" % ", ".join(skipped))
    product, support = fc.split_files(gated)

    # 1-2: coverage by standing verdicts
    latest = {}
    for r in fc.load_records(root):
        latest[(r["item"], str(r["row"]))] = r
    standing = {}
    for key in latest:
        try:
            ok, reason, rec = fc.standing(root, key[0], key[1])
        except fc.Refused as e:
            ok, reason, rec = False, str(e), None
        if ok:
            standing[key] = rec
        elif any(f in latest[key].get("product_files", []) for f in product):
            notes.append("%s/%s: %s" % (key[0], key[1], reason))
    covering = {}
    for f in product:
        keys = [k for k, rec in standing.items() if f in rec.get("product_files", [])]
        if not keys:
            failures.append("no standing verdict covers %s" % f)
        for k in keys:
            covering[k] = standing[k]

    # 3: proof on this tree (the merge result in CI)
    if not a.skip_proof:
        bl = fc.backlog(root)
        for (item, row) in sorted(covering):
            proof = fc.find_task(fc.find_item(bl, item), row)["proof"]
            r = subprocess.run(proof, shell=True, cwd=root, capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode != 0:
                failures.append("proof failed for %s/%s%s: %s (exit %d)" % (
                    item, row, " on the merge result" if a.merge_result else "", proof, r.returncode))
            else:
                notes.append("proof passed for %s/%s: %s" % (item, row, proof))

    # 4: tier controls
    allow = set(rp.get("privacy_allow", []))
    for f in gated:
        text = read_text(root, f)
        for rx in SECRET_RX:
            if rx.search(text):
                failures.append("secret scan: %s matches %s" % (f, rx.pattern[:40]))
        if tier != "T0" or prof.get("real_data"):
            if fc.matches(f, rp.get("privacy_skip", [])):
                continue
            hits = [m for m in EMAIL_RX.findall(text)
                    if hashlib.sha256(m.lower().encode()).hexdigest()[:16] not in allow
                    and not m.lower().endswith(("@example.com", "@example.org", "@users.noreply.github.com",
                                                "@anthropic.com"))]
            if hits or PHONE_RX.search(text):
                failures.append("privacy scan: %s contains %s" % (f, (hits or ["a phone number"])[0]))
    waived = {w["control"] for w in prof.get("waived_controls", [])}
    if tier in ("T2", "T3") and "threat-model" not in waived:
        if not (Path(root) / "docs" / "factory" / "threat-model.md").exists():
            failures.append("T2+: docs/factory/threat-model.md is missing")
    hr = [f for f in gated if fc.matches(f, rp.get("high_risk", []))]
    if hr and tier != "T0" and "agent-config-approval" not in waived:
        decisions = read_text(root, "docs/DECISIONS.md")
        approved = [rec for rec in covering.values()
                    if rec.get("approval") and re.search(r"\|\s*%s\s*\|" % re.escape(rec["approval"]), decisions)]
        hr_product = [f for f in hr if f in product]
        if hr_product and not approved:
            failures.append("high-risk change without an approval U<n> in its verdict record: %s"
                            % ", ".join(hr_product))
        if any(f in support for f in hr):
            notes.append("high-risk support files changed: %s" % ", ".join(f for f in hr if f in support))
    if any(fc.matches(f, ["tools/**", "tests/factory/**"]) for f in gated) and tier in ("T1", "T2", "T3"):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests/factory", "-t", ".", "-q"], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            failures.append("factory self-tests failed: %s" % (r.stdout + r.stderr).strip()[-300:])
        else:
            notes.append("factory self-tests passed")

    print("factory-gate tier %s base %s: %d changed (%d product, %d support)"
          % (tier, mb[:10], len(gated), len(product), len(support)))
    for n in notes:
        print("  note: " + n)
    for f in failures:
        print("  FAIL: " + f)
    print("factory-gate: %s" % ("FAIL" if failures else "PASS"))
    if failures:
        try:
            fc.append_event({"kind": "gate-fail", "where": "merge-result" if a.merge_result else "gate",
                             "failures": failures[:5]})
        except Exception:  # noqa: BLE001 (the event log is best effort in CI)
            pass
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
