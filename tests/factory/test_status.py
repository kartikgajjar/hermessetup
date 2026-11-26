import json
import subprocess
import sys
import unittest

from tests.factory.helpers import RepoCase, run, write, git, PY, TOOLS


class StatusTest(RepoCase):
    def test_refuses_bad_values(self):
        for args in (["set", "it1", "1", "--status", "finished"],
                     ["set", "it1", "1", "--status", "doing", "--model-class", "huge"],
                     ["set", "it1", "1", "--status", "doing", "--evidence", "x" * 301],
                     ["set", "it1", "9", "--status", "doing"],
                     ["set", "it1", "1", "--status", "doing", "--round"],
                     ["item-status", "it1", "done"]):
            r = run("status.py", *args, cwd=self.root)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn("REFUSED", r.stderr, args)

    def test_good_value_and_help_does_nothing(self):
        self.assertEqual(run("status.py", "set", "it1", "1", "--status", "doing", "--eta", "20",
                             cwd=self.root).returncode, 0)
        st = json.loads((self.root / ".factory/state/it1.json").read_text())
        self.assertEqual(st["rows"]["1"]["status"], "doing")
        self.assertEqual(st["status"], "in work")
        before = (self.root / ".factory/state/it1.json").read_text()
        r = run("status.py", "--help", cwd=self.root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(before, (self.root / ".factory/state/it1.json").read_text())

    def test_done_refused_without_verdict(self):
        r = run("status.py", "set", "it1", "1", "--status", "done", cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("standing verdict", r.stderr)

    def test_verdict_subcommand_refused(self):
        r = run("status.py", "verdict", cwd=self.root)
        self.assertEqual(r.returncode, 1)

    def test_add_row_and_ready(self):
        r = run("status.py", "add-row", "it1", "--task", "second", "--owner", "hs-dev", "--model-class", "cheap",
                "--owned", "risky.ps1", "--proof", "python -c 0", "--depends", "1", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("status.py", "ready", cwd=self.root)
        self.assertIn("it1/1", r.stdout)
        self.assertNotIn("it1/2", r.stdout)  # depends on row 1
        self.assertIn("verdict: author", r.stdout)

    def test_concurrent_writers_lose_nothing(self):
        tasks = [{"n": n, "task": "t", "owner": "hs-dev", "model_class": "cheap", "owned_files": ["f%d" % n],
                  "depends_on": [], "proof": "x", "bar": {"proof": "x", "live": "none", "verdict": "author"}}
                 for n in range(1, 9)]
        bl = json.loads((self.root / "docs/prd/backlog.json").read_text())
        bl["items"][0]["tasks"] = tasks
        write(self.root, "docs/prd/backlog.json", json.dumps(bl))
        procs = []
        for n in range(1, 9):
            for _ in range(3):
                procs.append(subprocess.Popen([PY, str(TOOLS / "status.py"), "set", "it1", str(n), "--status",
                                               "doing", "--evidence", "w%d" % n], cwd=self.root,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        codes, errs = [], []
        for p in procs:
            _, err = p.communicate()
            codes.append(p.returncode)
            errs.append(err.decode(errors="replace"))
        self.assertEqual(codes, [0] * len(procs), [e for e in errs if e])
        st = json.loads((self.root / ".factory/state/it1.json").read_text())
        self.assertEqual(sorted(st["rows"]), [str(n) for n in range(1, 9)])
        self.assertTrue(all(st["rows"][str(n)]["evidence"] == "w%d" % n for n in range(1, 9)))

    def test_worktree_writes_the_shared_store(self):
        wt = self.tmp / "wt"
        git(self.root, "worktree", "add", "-q", "-b", "lane", str(wt))
        self.assertEqual(run("status.py", "set", "it1", "1", "--status", "doing", cwd=wt).returncode, 0)
        self.assertTrue((self.root / ".factory/state/it1.json").exists())
        self.assertFalse((wt / ".factory").exists())

    def test_measures_runs(self):
        r = run("status.py", "measures", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("first-time-right", r.stdout)
        self.assertIn("next level (A1)", r.stdout)


if __name__ == "__main__":
    unittest.main()
