import json
import shutil
import subprocess
import unittest

from tests.factory.helpers import RepoCase, git, run, write, PY, TOOLS

STOP = str(TOOLS / "hooks" / "stop_guard.py")


def stop(data):
    r = subprocess.run([PY, STOP], input=json.dumps(data), capture_output=True, text=True)
    return r.returncode, r.stderr


class StopGuardTest(unittest.TestCase):
    def test_blocks_once_then_releases(self):
        running = {"hook_event_name": "Stop", "stop_hook_active": False,
                   "background_tasks": [{"id": "b1", "status": "running", "description": "dev server"}]}
        code, err = stop(running)
        self.assertEqual(code, 2)
        self.assertIn("dev server", err)
        code, _ = stop(dict(running, stop_hook_active=True))
        self.assertEqual(code, 0)
        self.assertEqual(stop({"hook_event_name": "Stop", "background_tasks": []})[0], 0)
        done = {"hook_event_name": "Stop", "background_tasks": [{"id": "b1", "status": "completed"}]}
        self.assertEqual(stop(done)[0], 0)

    def test_factory_subagent_must_state_none(self):
        base = {"hook_event_name": "SubagentStop", "agent_type": "hs-dev", "stop_hook_active": False}
        self.assertEqual(stop(dict(base, last_assistant_message="row: it1/1 done"))[0], 2)
        self.assertEqual(stop(dict(base, last_assistant_message="...\nbackground tasks: none"))[0], 0)
        self.assertEqual(stop(dict(base, agent_type="Explore", last_assistant_message="x"))[0], 0)


class CommitMsgHookTest(RepoCase):
    def test_hook_refuses_trailer(self):
        r = run("setup_clone.py", cwd=self.root)
        hook = self.root / ".git" / "hooks" / "commit-msg"
        self.assertTrue(hook.exists(), r.stdout + r.stderr)
        git(self.root, "switch", "-q", "-c", "feature")
        write(self.root, "a.txt", "a\n")
        git(self.root, "add", "a.txt")
        bad = subprocess.run(["git", "commit", "-q", "-m", "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
                             cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0, bad.stderr)
        good = subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)

    def test_setup_refuses_different_existing_hook(self):
        write(self.root, ".git/hooks/commit-msg", "#!/bin/sh\nexit 0\n")
        r = run("setup_clone.py", cwd=self.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exists and differs", r.stderr)


class McpProbeTest(unittest.TestCase):
    def test_probe_lists_tools_and_flags_missing(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        srv = d / "srv.py"
        srv.write_text(
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    m = json.loads(line)\n"
            "    if m.get('method') == 'initialize':\n"
            "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'serverInfo':{'name':'fake'}}}), flush=True)\n"
            "    elif m.get('method') == 'tools/list':\n"
            "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'tools':[{'name':'a'},{'name':'b'}]}}), flush=True)\n")
        r = run("mcp_probe.py", "--expect", "a,b", "--timeout", "20", "--", PY, str(srv), cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"fake"', r.stdout)
        r = run("mcp_probe.py", "--expect", "a,c", "--timeout", "20", "--", PY, str(srv), cwd=d)
        self.assertEqual(r.returncode, 1)
        self.assertIn('"missing": ["c"]', r.stdout)
        shutil.rmtree(d, ignore_errors=True)


class CheckTest(RepoCase):
    def test_parse_failure_then_pass(self):
        write(self.root, "bad.ps1", "function f { \n")
        git(self.root, "add", "bad.ps1")
        r = run("check.py", cwd=self.root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("PARSE", r.stdout)
        write(self.root, "bad.ps1", "function f { }\n")
        r = run("check.py", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stdout)


class ShipTest(RepoCase):
    def test_refuses_leftovers_and_unverified_rows(self):
        write(self.root, "app.ps1", "dirty\n")
        r = run("ship.py", "r1", "--version", "0.1.0", "--audit-waived", "test", "--dry-run", cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("leftovers", r.stderr)
        git(self.root, "checkout", "--", "app.ps1")
        r = run("ship.py", "r1", "--version", "0.1.0", "--audit-waived", "test", "--dry-run", cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not done", r.stderr)


class GenTest(unittest.TestCase):
    def test_copies_match_and_shared_block_identical(self):
        from tests.factory.helpers import REAL
        r = subprocess.run([PY, str(TOOLS / "gen.py"), "--check"], cwd=REAL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        shared = (REAL / "tools/agents/_shared.md").read_text(encoding="utf-8").strip()
        agents = list((REAL / ".claude/agents").glob("hs-*.md"))
        self.assertGreaterEqual(len(agents), 3)
        for a in agents:
            self.assertIn(shared, a.read_text(encoding="utf-8"), a.name)


if __name__ == "__main__":
    unittest.main()
