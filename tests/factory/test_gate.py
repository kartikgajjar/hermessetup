import json
import unittest

from tests.factory.helpers import RepoCase, run, write, git, RISK, stub_verifier


class GateTest(RepoCase):
    def gate(self):
        return run("gate.py", "--base", "main", cwd=self.root)

    def test_fails_missing_then_stale_then_passes(self):
        self.branch()
        self.change_app()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no standing verdict covers app.ps1", r.stdout)
        run("dispatch.py", "verify", "it1", "1", "--author", cwd=self.root)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "verdict")
        self.change_app("Write-Host 'ok' \n")  # whitespace: stale fingerprint
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("void", r.stdout)
        self.change_app("Write-Host 'ok'\n")  # back to the verified content
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("proof passed for it1/1", r.stdout)

    def test_secret_scan(self):
        self.branch()
        write(self.root, "docs/x.md", "key = 'sk-ant-abcdefghijklmnopqrstuvwxyz'\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "s")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("secret scan", r.stdout)

    def test_privacy_scan(self):
        self.branch()
        write(self.root, "docs/x.md", "mail jane.doe@contoso.com\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "p")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("privacy scan", r.stdout)

    def test_high_risk_needs_approval(self):
        self.branch()
        bl = json.loads((self.root / "docs/prd/backlog.json").read_text())
        bl["items"][0]["tasks"][0]["owned_files"] = ["AGENTS.md", ".claude/x.json"]
        write(self.root, "docs/prd/backlog.json", json.dumps(bl))
        write(self.root, ".claude/x.json", "{}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "hr")
        stub = stub_verifier(self.tmp, json.dumps({"structured_output": {"verdict": "PASS", "feature_ids": [],
                                                                          "evidence": "e", "notes": "", "findings": []}}))
        write(self.root, "app.ps1", "Write-Host 'ok'\n")
        git(self.root, "commit", "-q", "-am", "ok")
        run("dispatch.py", "verify", "it1", "1", cwd=self.root, env={"FACTORY_VERIFIER_CMD": stub})
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "v")
        r = self.gate()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("without an approval", r.stdout)
        run("dispatch.py", "verify", "it1", "1", "--approval", "U1", cwd=self.root,
            env={"FACTORY_VERIFIER_CMD": stub})
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "v2")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_automated_pusher_on_its_paths_is_skipped(self):
        risk = dict(RISK, automated_pushers=[{"authors": ["bot@example.com"], "paths": ["data/**"]}])
        write(self.root, "config/risk-paths.yaml", json.dumps(risk))
        git(self.root, "commit", "-q", "-am", "pusher")
        self.branch()
        write(self.root, "data/feed.ps1", "1\n")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.email=bot@example.com", "commit", "-q", "-m", "bot")
        self.assertEqual(self.gate().returncode, 0)
        write(self.root, "data/feed.ps1", "2\n")
        git(self.root, "commit", "-q", "-am", "human")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("data/feed.ps1", r.stdout)

    def test_bootstrap_merge_not_gated(self):
        prof = (self.root / "config/factory-profile.yaml").read_text()
        git(self.root, "rm", "-q", "config/factory-profile.yaml")
        git(self.root, "commit", "-q", "-m", "base without profile")
        self.branch()
        write(self.root, "config/factory-profile.yaml", prof)
        self.change_app("Write-Host 'anything'\n", commit=False)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "install factory")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("bootstrap merge", r.stdout)

if __name__ == "__main__":
    unittest.main()
