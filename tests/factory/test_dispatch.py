import json
import unittest

from tests.factory.helpers import RepoCase, run, write, git, OK_PROOF, stub_verifier

GOOD = json.dumps({"session_id": "s1", "structured_output": {
    "verdict": "PASS", "feature_ids": ["F-x"], "evidence": "ran it", "notes": "", "findings": []}})
BAD = json.dumps({"structured_output": {"verdict": "LOOKS FINE", "evidence": "x"}})


class VerifyTest(RepoCase):
    def test_schema_failure_is_refused(self):
        self.branch()
        self.change_app()
        r = run("dispatch.py", "verify", "it1", "1", cwd=self.root, env={"FACTORY_VERIFIER_CMD": stub_verifier(self.tmp, BAD)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("fails tools/schemas/verifier-output.json", r.stderr)
        self.assertFalse(list((self.root / "docs/prd/verdicts").glob("*.json")) if
                         (self.root / "docs/prd/verdicts").exists() else [])

    def test_verifier_record_written(self):
        self.branch()
        self.change_app()
        r = run("dispatch.py", "verify", "it1", "1", cwd=self.root, env={"FACTORY_VERIFIER_CMD": stub_verifier(self.tmp, GOOD)})
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = list((self.root / "docs/prd/verdicts").glob("*.json"))
        self.assertEqual(len(recs), 1)
        rec = json.loads(recs[0].read_text())
        self.assertEqual((rec["by"], rec["verdict"], rec["session_id"]), ("verifier", "PASS", "s1"))
        self.assertEqual(rec["product_files"], ["app.ps1"])

    def test_verifier_pass_but_proof_fails_is_fail(self):
        self.branch()
        self.change_app("Write-Host 'nope'\n")
        r = run("dispatch.py", "verify", "it1", "1", cwd=self.root, env={"FACTORY_VERIFIER_CMD": stub_verifier(self.tmp, GOOD)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL by verifier", r.stdout)

    def test_risk_path_needs_verifier(self):
        bl = json.loads((self.root / "docs/prd/backlog.json").read_text())
        bl["items"][0]["tasks"][0]["owned_files"] = ["risky.ps1"]
        write(self.root, "docs/prd/backlog.json", json.dumps(bl))
        git(self.root, "commit", "-q", "-am", "own risky")
        self.branch()
        write(self.root, "risky.ps1", "x\n")
        write(self.root, "app.ps1", "Write-Host 'ok'\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "risky")
        r = run("dispatch.py", "verify", "it1", "1", "--author", cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs a verifier verdict", r.stderr)


class IntegrateTest(RepoCase):
    def ready_packet(self):
        self.branch()
        self.change_app()
        self.assertEqual(run("dispatch.py", "verify", "it1", "1", "--author", cwd=self.root).returncode, 0)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "verdict")

    def test_auto_merge_refused_below_a2(self):
        self.ready_packet()
        r = run("dispatch.py", "integrate", "it1", "--auto", cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs A2", r.stderr)

    def test_local_merge_proves_merge_result(self):
        self.ready_packet()
        r = run("dispatch.py", "integrate", "it1", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("proof passed on the merge result", r.stdout)
        self.assertIn("ok", git(self.root, "show", "main:app.ps1"))

    def test_merge_result_failure_reopens_row(self):
        bad = 'python -c "import os,sys; sys.exit(1 if os.path.exists(\'bad.txt\') else 0)"'
        bl = json.loads((self.root / "docs/prd/backlog.json").read_text())
        bl["items"][0]["tasks"][0]["proof"] = bad
        bl["items"][0]["tasks"][0]["bar"]["proof"] = bad
        write(self.root, "docs/prd/backlog.json", json.dumps(bl))
        git(self.root, "commit", "-q", "-am", "proof")
        self.ready_packet()
        run("status.py", "set", "it1", "1", "--status", "done", cwd=self.root)
        git(self.root, "switch", "-q", "main")
        write(self.root, "bad.txt", "x\n")
        git(self.root, "add", "bad.txt")
        git(self.root, "commit", "-q", "-m", "main moved")
        git(self.root, "switch", "-q", "it1-r1")
        r = run("dispatch.py", "integrate", "it1", cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("rows reopened", r.stderr)
        st = json.loads((self.root / ".factory/state/it1.json").read_text())
        self.assertEqual(st["rows"]["1"]["status"], "todo")

    def test_refuses_from_default_branch(self):
        r = run("dispatch.py", "integrate", "it1", cwd=self.root)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
