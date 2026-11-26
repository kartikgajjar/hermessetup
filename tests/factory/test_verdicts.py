import json
import unittest

from tests.factory.helpers import RepoCase, run, write, git


class VerdictRuleTest(RepoCase):
    def verify(self):
        r = run("dispatch.py", "verify", "it1", "1", "--author", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def standing(self):
        import sys
        from tests.factory.helpers import TOOLS
        sys.path.insert(0, str(TOOLS))
        import factory_common as fc
        return fc.standing(self.root, "it1", 1)

    def test_done_with_verdict_then_product_change_voids(self):
        self.branch()
        self.change_app()
        self.verify()
        self.assertEqual(run("status.py", "set", "it1", "1", "--status", "done", cwd=self.root).returncode, 0)
        write(self.root, "app.ps1", "Write-Host 'ok' # changed\n")
        ok, reason, _ = self.standing()
        self.assertFalse(ok)
        self.assertIn("void", reason)

    def test_whitespace_only_product_change_voids(self):
        self.branch()
        self.change_app()
        self.verify()
        write(self.root, "app.ps1", "Write-Host 'ok'  \n")
        ok, reason, _ = self.standing()
        self.assertFalse(ok)
        r = run("status.py", "set", "it1", "1", "--status", "done", cwd=self.root)
        self.assertEqual(r.returncode, 1)

    def test_support_only_change_keeps_live_result(self):
        self.branch()
        self.change_app()
        write(self.root, "docs/notes.md", "a\n")
        git(self.root, "add", "docs/notes.md")
        git(self.root, "commit", "-q", "-m", "doc")
        self.verify()
        write(self.root, "docs/notes.md", "b\n")
        ok, reason, _ = self.standing()
        self.assertTrue(ok)
        self.assertIn("support files changed", reason)
        r = run("dispatch.py", "reproof", "it1", "1", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        ok, reason, _ = self.standing()
        self.assertEqual(reason, "stands")

    def test_rebase_that_did_not_touch_the_packet_keeps_it(self):
        self.branch()
        self.change_app()
        self.verify()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "verdict")
        git(self.root, "switch", "-q", "main")
        write(self.root, "other.ps1", "x\n")
        git(self.root, "add", "other.ps1")
        git(self.root, "commit", "-q", "-m", "other")
        git(self.root, "switch", "-q", "it1-r1")
        git(self.root, "rebase", "-q", "main")
        ok, reason, _ = self.standing()
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
