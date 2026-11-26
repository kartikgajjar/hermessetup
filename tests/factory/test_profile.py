import json
import unittest

from tests.factory.helpers import RepoCase, run, write, REAL, TOOLS  # noqa: F401
import sys
sys.path.insert(0, str(TOOLS))
import factory_common as fc  # noqa: E402


def facts(**kw):
    base = {"lifetime": "kept", "exposure": "internal", "data": "internal metadata", "transactions": "none",
            "availability": "best effort", "regulation": "none"}
    base.update(kw)
    return base


class TierTest(unittest.TestCase):
    def test_each_tier(self):
        self.assertEqual(fc.derive_tier(facts(lifetime="throwaway")), "T0")
        self.assertEqual(fc.derive_tier(facts()), "T1")
        self.assertEqual(fc.derive_tier(facts(transactions="internal writes")), "T2")
        self.assertEqual(fc.derive_tier(facts(exposure="public")), "T3")
        self.assertEqual(fc.derive_tier(facts(regulation="GDPR")), "T3")

    def test_throwaway_with_personal_data_is_kept(self):
        self.assertEqual(fc.derive_tier(facts(lifetime="throwaway", data="personal")), "T2")

    def test_this_repo_is_t2(self):
        p = json.loads((REAL / "config/factory-profile.yaml").read_text())
        self.assertEqual(fc.derive_tier(p["facts"]), "T2")


class LevelTest(unittest.TestCase):
    def test_lowest_of_three(self):
        lv = fc.working_level("T2", "A3", "A0", True)
        self.assertEqual((lv["working_off_risk"], lv["working_on_risk"]), ("A0", "A0"))
        lv = fc.working_level("T2", "A3", "A3", True)
        self.assertEqual((lv["working_off_risk"], lv["working_on_risk"]), ("A2", "A1"))
        lv = fc.working_level("T1", "A1", "A3", True)
        self.assertEqual(lv["working_off_risk"], "A1")
        lv = fc.working_level("T3", "A3", "A3", True)
        self.assertEqual(lv["working_off_risk"], "A1")

    def test_no_blocking_gate_caps_at_a1_except_t0(self):
        self.assertEqual(fc.working_level("T2", "A3", "A3", False)["working_off_risk"], "A1")
        self.assertEqual(fc.working_level("T0", "A3", "A3", False)["working_off_risk"], "A3")

    def test_cli(self):
        r = run("profile.py", "level", "--tier", "T2", "--ceiling", "A3", "--earned", "A1", "--blocking", "yes",
                cwd=REAL)
        self.assertIn("working A1 off risk / A1 on risk", r.stdout)


class SettingsTest(RepoCase):
    def test_minus_c_forms_and_drift(self):
        r = run("profile.py", "settings", cwd=self.root)
        s = json.loads(r.stdout)
        self.assertIn("Bash(git -C * push *)", s["permissions"]["ask"])
        self.assertIn("Bash(git push *)", s["permissions"]["ask"])
        self.assertIn("Bash(git -C * commit *)", s["permissions"]["ask"])  # A0: commit is the user's step
        self.assertTrue(any("-C" in d and "--force" in d for d in s["permissions"]["deny"]))
        self.assertEqual(run("profile.py", "settings", "--check", cwd=self.root).returncode, 1)
        self.assertEqual(run("profile.py", "settings", "--write", cwd=self.root).returncode, 0)
        self.assertEqual(run("profile.py", "settings", "--check", cwd=self.root).returncode, 0)

    def test_a1_lets_agents_commit(self):
        p = json.loads((self.root / "config/factory-profile.yaml").read_text())
        p["autonomy"]["earned"] = "A1"
        write(self.root, "config/factory-profile.yaml", json.dumps(p))
        s = json.loads(run("profile.py", "settings", cwd=self.root).stdout)
        self.assertIn("Bash(git commit *)", s["permissions"]["allow"])
        self.assertIn("Bash(git push *)", s["permissions"]["ask"])
