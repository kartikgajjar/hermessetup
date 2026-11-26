#!/usr/bin/env python
"""setup_clone.py: one-time setup for a fresh clone.

Installs tools/hooks/commit-msg as the git commit-msg hook (refuses to overwrite a different existing
hook), creates the git-ignored state folder, and checks the generated agent/skill copies and settings.
  python tools/setup_clone.py [--check]
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factory_common as fc  # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = fc.repo_root()
    hooks = Path(fc.git("rev-parse", "--path-format=absolute", "--git-path", "hooks", cwd=root))
    src, dst = HERE / "hooks" / "commit-msg", hooks / "commit-msg"
    ok = True
    if dst.exists() and not filecmp.cmp(src, dst, shallow=False):
        print("REFUSED: %s exists and differs; merge it by hand" % dst, file=sys.stderr)
        ok = False
    elif not dst.exists():
        if "--check" in argv:
            print("commit-msg hook: not installed")
            ok = False
        else:
            hooks.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            dst.chmod(0o755)
            print("installed %s" % dst)
    else:
        print("commit-msg hook: installed")
    fc.state_dir(root)
    for cmd in (["gen.py", "--check"], ["profile.py", "settings", "--check"]):
        r = subprocess.run([sys.executable, str(HERE / cmd[0]), *cmd[1:]], cwd=root)
        ok = ok and r.returncode == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
