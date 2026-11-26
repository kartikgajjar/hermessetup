"""Shared helpers for the factory scripts (standard library only).

Config files under config/ and docs/path-map.yaml are written in JSON syntax (valid YAML 1.2),
so the standard library json module reads them; no YAML dependency.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATUSES = ("todo", "doing", "done", "blocked")
ITEM_STATUSES = ("decided", "in work", "built", "shipped")
MODEL_CLASSES = ("strong", "standard", "cheap")
VERDICTS = ("PASS", "PASS+NOTES", "FAIL")
ROLES = ("author", "verifier")
CAUSES = ("brief-gap", "bar-gap", "data-fact-missing", "scope-too-wide", "cross-cutting-design",
          "test-harness", "tooling", "environment", "lead-process", "agent-definition")

# Files never part of a packet's fingerprint: the spec, the verdict records, the live state.
EXCLUDED = ("docs/prd/**", ".factory/**")
# Support files: tests, docs, lint/config. Everything else that a packet changes is product.
SUPPORT = ("docs/**", "**/*.md", "*.md", "tests/**", "chrome-mcp/test/**", ".gitignore", ".editorconfig",
           "**/tsconfig*.json", ".gitattributes")


class Refused(Exception):
    pass


def git(*args: str, cwd=None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def repo_root(cwd=None) -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=cwd))


def main_root(cwd=None) -> Path:
    """The main worktree: parent of the git common dir, the same from every linked worktree."""
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd))
    return common.parent


def state_dir(cwd=None) -> Path:
    d = main_root(cwd) / ".factory" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_time(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s)


def load_json(p) -> dict:
    for attempt in range(50):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(p, data) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for attempt in range(50):  # Windows: a reader holding the file makes os.replace fail briefly
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    os.replace(tmp, p)


class Lock:
    """Exclusive lock file; a lock older than 'stale' seconds is taken over."""

    def __init__(self, path, timeout: float = 30.0, stale: float = 120.0):
        self.path = Path(path)
        self.timeout, self.stale = timeout, stale

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise Refused("lock busy: %s" % self.path)
                time.sleep(0.02)

    def __exit__(self, *exc):
        for attempt in range(50):
            try:
                self.path.unlink()
                return
            except FileNotFoundError:
                return
            except PermissionError:
                time.sleep(0.01 * (attempt + 1))


def append_event(event: dict, cwd=None) -> None:
    d = state_dir(cwd)
    event = dict(event, time=now())
    with Lock(d / "events.lock"):
        with open(d / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(cwd=None) -> list:
    p = state_dir(cwd) / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- globs ----------
_glob_cache: dict = {}


def glob_to_re(g: str):
    if g in _glob_cache:
        return _glob_cache[g]
    i, out = 0, ""
    while i < len(g):
        c = g[i]
        if g.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if g.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    rx = re.compile("^" + out + "$", re.IGNORECASE)
    _glob_cache[g] = rx
    return rx


def matches(path: str, globs) -> bool:
    path = path.replace("\\", "/")
    return any(glob_to_re(g).match(path) for g in globs)


# ---------- repo config ----------
def profile(root) -> dict:
    return load_json(Path(root) / "config" / "factory-profile.yaml")


def risk_globs(root) -> list:
    p = Path(root) / "config" / "risk-paths.yaml"
    return load_json(p).get("paths", []) if p.exists() else []


def high_risk_globs(root) -> list:
    p = Path(root) / "config" / "risk-paths.yaml"
    return load_json(p).get("high_risk", []) if p.exists() else []


def backlog(root) -> dict:
    return load_json(Path(root) / "docs" / "prd" / "backlog.json")


def find_item(bl: dict, item: str) -> dict:
    for it in bl.get("items", []):
        if it["id"] == item:
            return it
    raise Refused("no such item: %s" % item)


def find_task(it: dict, row) -> dict:
    for t in it.get("tasks", []):
        if str(t["n"]) == str(row):
            return t
    raise Refused("no such row: %s/%s" % (it["id"], row))


# ---------- tier / level ----------
def derive_tier(facts: dict) -> str:
    v = {k: (f["value"] if isinstance(f, dict) else f) for k, f in facts.items()}
    life, exp, data = v.get("lifetime"), v.get("exposure"), v.get("data")
    tx, avail, reg = v.get("transactions"), v.get("availability"), v.get("regulation", "none")
    if life == "throwaway" and data not in ("personal", "regulated"):
        return "T0"
    if exp == "public" or data == "regulated" or tx == "money, auth or customer-facing writes" \
            or avail == "always on" or (reg and reg != "none"):
        return "T3"
    if data == "personal" or tx == "internal writes" or exp == "partner" or avail == "business hours":
        return "T2"
    return "T1"


LEVELS = ("A0", "A1", "A2", "A3")
TIER_CAP = {"T0": ("A3", "A3"), "T1": ("A3", "A1"), "T2": ("A2", "A1"), "T3": ("A1", "A1")}


def lowest(*levels: str) -> str:
    return min(levels, key=LEVELS.index)


def working_level(tier: str, ceiling: str, earned: str, blocking_gate: bool) -> dict:
    off, on = TIER_CAP[tier]
    if tier != "T0" and not blocking_gate:
        off, on = lowest(off, "A1"), lowest(on, "A1")
    return {"ceiling": ceiling, "tier": tier, "cap_off_risk": off, "cap_on_risk": on, "earned": earned,
            "working_off_risk": lowest(ceiling, off, earned), "working_on_risk": lowest(ceiling, on, earned)}


def level_for_repo(root) -> dict:
    p = profile(root)
    return working_level(derive_tier(p["facts"]), p["autonomy"]["ceiling"], p["autonomy"]["earned"],
                         bool(p.get("enforcement", {}).get("main_protected")))


# ---------- fingerprints and verdicts ----------
def blob_id(root, path: str) -> str:
    full = Path(root) / path
    if not full.exists():
        return "deleted"
    return git("hash-object", "--", path, cwd=root)


def fingerprint(root, paths) -> str:
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        h.update(("%s\0%s\n" % (p, blob_id(root, p))).encode())
    return h.hexdigest()


def merge_base(root, base: str) -> str:
    return git("merge-base", base, "HEAD", cwd=root)


def changed_files(root, base: str, owned=()) -> list:
    """Files a packet changed against base: committed, uncommitted tracked, and untracked owned files."""
    mb = merge_base(root, base)
    files = set(filter(None, git("diff", "--name-only", mb, cwd=root).splitlines()))
    files |= set(filter(None, git("diff", "--name-only", "--cached", mb, cwd=root).splitlines()))
    if owned:
        for f in git("ls-files", "--others", "--exclude-standard", cwd=root).splitlines():
            if f and matches(f, owned):
                files.add(f)
    return sorted(f for f in files if not matches(f, EXCLUDED))


def split_files(files):
    product = [f for f in files if not matches(f, SUPPORT)]
    support = [f for f in files if matches(f, SUPPORT)]
    return product, support


def verdict_dir(root) -> Path:
    return Path(root) / "docs" / "prd" / "verdicts"


def load_records(root, item=None, row=None) -> list:
    d = verdict_dir(root)
    out = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            try:
                r = load_json(p)
            except ValueError:
                continue
            if item and r.get("item") != item:
                continue
            if row is not None and str(r.get("row")) != str(row):
                continue
            r["_path"] = str(p)
            out.append(r)
    out.sort(key=lambda r: r.get("time", ""))
    return out


def required_role(root, task: dict, product_files) -> str:
    tier = derive_tier(profile(root)["facts"])
    bar_role = (task.get("bar") or {}).get("verdict", "author")
    risky = any(matches(f, risk_globs(root)) for f in product_files)
    need = "author"
    if tier == "T3":
        need = "verifier"
    elif tier == "T2" and (risky or task.get("interface_change")):
        need = "verifier"
    elif tier == "T1" and risky:
        need = "verifier"
    return "verifier" if "verifier" in (need, bar_role) else "author"


def role_ok(by: str, need: str) -> bool:
    return need == "author" or by == "verifier"


def standing(root, item: str, row) -> tuple:
    """(ok, reason, record) for the latest qualifying verdict on (item, row) at the current tree."""
    it = find_item(backlog(root), item)
    task = find_task(it, row)
    recs = load_records(root, item, row)
    if not recs:
        return False, "no verdict record", None
    r = recs[-1]
    if r.get("verdict") not in ("PASS", "PASS+NOTES"):
        return False, "latest verdict is %s" % r.get("verdict"), r
    need = required_role(root, task, r.get("product_files", []))
    if not role_ok(r.get("by", ""), need):
        return False, "verdict by %s, bar needs %s" % (r.get("by"), need), r
    if fingerprint(root, r.get("product_files", [])) != r.get("product_fingerprint"):
        return False, "void: product fingerprint changed", r
    if fingerprint(root, r.get("support_files", [])) != r.get("support_fingerprint"):
        return True, "stands; support files changed: rerun proof (dispatch.py reproof)", r
    return True, "stands", r


# ---------- tiny JSON-schema validator (subset) ----------
def validate(obj, schema, path="$") -> list:
    errs = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
             "boolean": bool, "null": type(None)}
    if t:
        tl = t if isinstance(t, list) else [t]
        if not any(isinstance(obj, types[x]) and not (x in ("integer", "number") and isinstance(obj, bool))
                   for x in tl):
            return ["%s: expected %s" % (path, t)]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append("%s: %r not in %s" % (path, obj, schema["enum"]))
    if isinstance(obj, str):
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            errs.append("%s: longer than %d" % (path, schema["maxLength"]))
        if "minLength" in schema and len(obj) < schema["minLength"]:
            errs.append("%s: shorter than %d" % (path, schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append("%s: does not match %s" % (path, schema["pattern"]))
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append("%s: missing %s" % (path, k))
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                errs += validate(v, props[k], "%s.%s" % (path, k))
            elif schema.get("additionalProperties") is False:
                errs.append("%s: unexpected key %s" % (path, k))
    if isinstance(obj, list) and "items" in schema:
        for i, v in enumerate(obj):
            errs += validate(v, schema["items"], "%s[%d]" % (path, i))
    return errs


def refuse(msg: str, code: int = 1):
    print("REFUSED: " + msg, file=sys.stderr)
    sys.exit(code)
