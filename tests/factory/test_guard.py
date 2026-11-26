import json
import subprocess
import unittest

from tests.factory.helpers import RepoCase, git, PY, TOOLS

GUARD = str(TOOLS / "hooks" / "guard.py")


class GuardTest(RepoCase):
    def call(self, tool, cwd=None, agent=False, **ti):
        data = {"tool_name": tool, "tool_input": ti, "cwd": str(cwd or self.root), "session_id": "s"}
        if agent:
            data["agent_id"], data["agent_type"] = "a1", "hs-dev"
        r = subprocess.run([PY, GUARD], input=json.dumps(data), capture_output=True, text=True)
        return r.returncode, r.stderr

    def blocked(self, cmd, cwd=None, tool="Bash"):
        code, err = self.call(tool, cwd=cwd, command=cmd)
        self.assertEqual(code, 2, "expected block: %s" % cmd)
        self.assertIn("BLOCKED", err)

    def allowed(self, cmd, cwd=None, tool="Bash"):
        code, err = self.call(tool, cwd=cwd, command=cmd)
        self.assertEqual(code, 0, "expected allow: %s (%s)" % (cmd, err))

    def test_cd(self):
        self.blocked("cd /tmp && ls")
        self.blocked("Set-Location C:/x; Get-ChildItem", tool="PowerShell")
        self.blocked("ls && pushd x")
        self.allowed("ls /tmp")
        self.allowed("git -C /tmp status")

    def test_ai_trailer(self):
        git(self.root, "switch", "-q", "-c", "feature")
        self.blocked('git commit -m "fix\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>"')
        self.blocked('git commit -m "x" -m "Generated with [Claude Code](https://claude.com)"')
        self.allowed('git commit -m "fix\n\nCo-Authored-By: Jane <jane@example.com>"')

    def test_polling(self):
        self.blocked("while true; do sleep 5; done")
        self.blocked("tail -f out.log")
        self.blocked("Get-Content out.log -Wait", tool="PowerShell")
        self.blocked("while ($true) { Start-Sleep 2 }", tool="PowerShell")
        self.allowed("sleep 2")
        self.allowed("tail -n 20 out.log")

    def test_state_writes(self):
        self.blocked("echo x > .factory/state/it1.json")
        self.blocked("rm docs/prd/verdicts/a.json")
        self.blocked("python tools/status.py verdict it1 1")
        self.allowed("python tools/status.py set it1 1 --status doing")
        self.allowed("python tools/dispatch.py verify it1 1")
        self.allowed("cat .factory/state/it1.json")
        code, _ = self.call("Write", file_path=str(self.root / ".factory/state/x.json"))
        self.assertEqual(code, 2)
        code, _ = self.call("Edit", file_path=str(self.root / "docs/prd/verdicts/x.json"))
        self.assertEqual(code, 2)

    def test_default_branch_git(self):
        self.blocked("git commit -m x")
        self.blocked("git merge feature")
        self.blocked("git push origin main")
        self.blocked("git -C %s push origin main" % self.root, cwd=self.tmp)
        self.blocked("git -c core.x=y -C %s commit -m x" % self.root, cwd=self.tmp)
        self.allowed("git status")
        self.allowed("git -C %s log -1" % self.root, cwd=self.tmp)
        git(self.root, "switch", "-q", "-c", "feature")
        self.allowed("git commit -m x")
        self.allowed("git -C %s commit -m x" % self.root, cwd=self.tmp)

    def test_minus_c_reads_branch_of_named_dir(self):
        git(self.root, "switch", "-q", "-c", "feature")  # the session's own dir is not on main
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        self.blocked("git -C %s commit -m x" % other)
        self.allowed("git commit -m x")

    def test_worktree_rules(self):
        wt = self.tmp / "wt"
        git(self.root, "worktree", "add", "-q", "-b", "lane", str(wt))
        self.blocked("git stash", cwd=wt)
        self.blocked("git commit -a -m x", cwd=wt)
        self.blocked("git commit -am x", cwd=wt)
        self.blocked("git push origin lane", cwd=wt)
        self.blocked("git -C %s stash" % wt)
        self.allowed("git commit -m x", cwd=wt)
        self.allowed("git commit --amend -m x", cwd=wt)
        code, _ = self.call("Edit", cwd=wt, file_path=str(wt / "docs/prd/backlog.json"))
        self.assertEqual(code, 2)
        code, _ = self.call("Edit", cwd=wt, file_path=str(wt / "app.ps1"))
        self.assertEqual(code, 0)

    def test_agent_high_risk_edit(self):
        code, _ = self.call("Edit", agent=True, file_path=str(self.root / "AGENTS.md"))
        self.assertEqual(code, 2)
        code, _ = self.call("Edit", agent=True, file_path=str(self.root / "app.ps1"))
        self.assertEqual(code, 0)
        code, _ = self.call("Edit", agent=False, file_path=str(self.root / "AGENTS.md"))
        self.assertEqual(code, 0)

    def test_bad_input_fails_open(self):
        r = subprocess.run([PY, GUARD], input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
