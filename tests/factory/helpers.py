"""Throwaway git repos for the factory self-tests."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REAL = Path(__file__).resolve().parents[2]
TOOLS = REAL / "tools"
PY = sys.executable
OK_PROOF = 'python -c "import sys; sys.exit(0 if \'ok\' in open(\'app.ps1\').read() else 1)"'


def _rm(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError("git %s: %s" % (args, r.stderr))
    return r.stdout.strip()


def run(tool, *args, cwd, env=None, stdin=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([PY, str(TOOLS / tool), *args], cwd=cwd, capture_output=True, text=True, env=e,
                          input=stdin, encoding="utf-8", errors="replace")


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


PROFILE = {"default_branch": "main", "facts": {
    "lifetime": {"value": "kept"}, "exposure": {"value": "internal"}, "data": {"value": "internal metadata"},
    "transactions": {"value": "internal writes"}, "availability": {"value": "best effort"},
    "regulation": {"value": "none"}},
    "approver": "tester", "waived_controls": [], "autonomy": {"ceiling": "A3", "earned": "A0"},
    "enforcement": {"main_protected": False, "integration": "local"}, "models": {"standard": "x"}}
RISK = {"paths": ["risky.ps1", ".claude/**", "config/**"], "high_risk": [".claude/**", "config/**", "AGENTS.md"],
        "automated_pushers": [], "privacy_allow": [], "privacy_skip": ["docs/prd/**"]}


def stub_verifier(dirpath, payload: str) -> str:
    """A fake verifier command (FACTORY_VERIFIER_CMD) that prints payload."""
    p = Path(dirpath) / ("stub_%d.py" % (abs(hash(payload)) % 10**8))
    p.write_text("import sys\nsys.stdin.read()\nprint(%r)\n" % payload, encoding="utf-8")
    return '"%s" "%s"' % (PY, p)


class RepoCase(unittest.TestCase):
    """A fresh throwaway repo on main with a factory profile, one item with one row, and app.ps1."""

    profile = PROFILE
    tasks = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-test-"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "core.autocrlf", "false")
        write(self.root, "config/factory-profile.yaml", json.dumps(self.profile))
        write(self.root, "config/risk-paths.yaml", json.dumps(RISK))
        write(self.root, "docs/prd/backlog.schema.json", (REAL / "docs/prd/backlog.schema.json").read_text())
        tasks = self.tasks or [{"n": 1, "task": "t", "owner": "hs-dev", "model_class": "cheap",
                                "owned_files": ["app.ps1"], "depends_on": [], "proof": OK_PROOF,
                                "bar": {"proof": OK_PROOF, "live": "none: test", "verdict": "author"}}]
        write(self.root, "docs/prd/backlog.json", json.dumps({"items": [{
            "id": "it1", "title": "t", "release": "r1", "request": {"words": "w", "date": "d"}, "decisions": [],
            "acceptance": [], "tasks": tasks}]}))
        write(self.root, "docs/factory/threat-model.md", "tm\n")
        write(self.root, "docs/DECISIONS.md", "| U1 | d | x | y |\n")
        write(self.root, ".gitignore", ".factory/\n")
        write(self.root, "app.ps1", "Write-Host 'base'\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def tearDown(self):
        shutil.rmtree(self.tmp, onerror=_rm)

    def branch(self, name="it1-r1"):
        git(self.root, "switch", "-q", "-c", name)

    def change_app(self, text="Write-Host 'ok'\n", commit=True):
        write(self.root, "app.ps1", text)
        if commit:
            git(self.root, "commit", "-q", "-am", "change")
