#!/usr/bin/env python
"""check.py: the repo's proof command.

  python tools/check.py            PowerShell parse of every tracked .ps1, py_compile of every tracked .py
  python tools/check.py --factory  + the factory self-tests (tests/factory)
  python tools/check.py --chrome   + the vendored chrome-mcp suite (npm --prefix chrome-mcp test, ~15 s)
  python tools/check.py --help-render  + every script carries a comment-based help block with .SYNOPSIS
  python tools/check.py --get-help     + Get-Help renders a synopsis for every script (baseline FAILS:
                                   a '#requires' line before the help block hides it from Get-Help)
  python tools/check.py --all      all of the above
Exit 0 only when every selected check passes. Never runs a Hermes script.
"""
from __future__ import annotations

import argparse
import py_compile
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factory_common as fc  # noqa: E402

PARSE = ("$bad=0; foreach($f in $args){$e=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile("
         "$f,[ref]$null,[ref]$e); if($e.Count){$bad++; Write-Output \"PARSE $f : $($e[0].Message)\"}}; exit $bad")
GET_HELP = ("$bad=0; foreach($f in $args){$h=Get-Help $f -ErrorAction SilentlyContinue; "
            "if(-not $h.Synopsis -or $h.Synopsis -like '*.ps1*'){$bad++; Write-Output \"GET-HELP $f : no synopsis\"}}; exit $bad")
HELP_RX = __import__("re").compile(r"<#.*?\.SYNOPSIS\s*\n\s*\S.*?#>", __import__("re").DOTALL)


def pwsh(script, files, root):
    exe = "pwsh" if subprocess.run(["where" if sys.platform == "win32" else "which", "pwsh"],
                                   capture_output=True).returncode == 0 else "powershell"
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", "& {%s}" % script, *files], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="check.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for f in ("--factory", "--chrome", "--help-render", "--get-help", "--all"):
        ap.add_argument(f, action="store_true")
    a = ap.parse_args(argv)
    root = fc.repo_root()
    tracked = fc.git("ls-files", cwd=root).splitlines()
    ps1 = [str(Path(root) / f) for f in tracked if f.endswith(".ps1") and not f.startswith("chrome-mcp/")]
    py = [f for f in tracked if f.endswith(".py")]
    results = []
    code, out = pwsh(PARSE, ps1, root) if ps1 else (0, "")
    results.append(("ps-parse (%d files)" % len(ps1), code, out))
    bad = []
    for f in py:
        try:
            py_compile.compile(str(Path(root) / f), doraise=True, cfile=None)
        except py_compile.PyCompileError as e:
            bad.append("%s: %s" % (f, e.msg.strip()[:200]))
    results.append(("py-compile (%d files)" % len(py), 1 if bad else 0, "\n".join(bad)))
    if a.help_render or a.all:
        missing = [f for f in ps1 if not HELP_RX.search(Path(f).read_text(encoding="utf-8", errors="replace"))]
        results.append(("help-block (%d scripts)" % len(ps1), 1 if missing else 0, "\n".join(missing)))
    if a.get_help:
        code, out = pwsh(GET_HELP, ps1, root)
        results.append(("get-help (%d scripts)" % len(ps1), code, out))
    if a.factory or a.all:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests/factory", "-t", ".", "-q"], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        results.append(("factory self-tests", r.returncode, (r.stdout + r.stderr).strip()[-1500:]))
    if a.chrome or a.all:
        r = subprocess.run("npm --prefix chrome-mcp test", shell=True, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith(("# pass", "# fail", "# tests"))]
        results.append(("chrome-mcp suite", r.returncode, " ".join(tail) or (r.stdout + r.stderr)[-800:]))
    failed = 0
    for name, code, out in results:
        print("%-28s %s" % (name, "ok" if code == 0 else "FAIL"))
        if code != 0:
            failed += 1
            print("    " + out.replace("\n", "\n    "))
        elif name.startswith("chrome") and out:
            print("    " + out)
    print("check: %s" % ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
