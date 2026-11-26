#!/usr/bin/env python
"""status.py: the only writer of live task rows (bootstrap v2.6, section 2.5).

Live state: one JSON file per item under <main worktree>/.factory/state/ (git-ignored, shared by
every worktree through the git common dir; local to this machine, history not kept).

Commands:
  set <item> <row> --status S [--eta MIN] [--evidence TEXT] [--model-class C] [--round --cause C]
                   [--bar-incomplete TEXT]
  add-row <item> --task TEXT --owner AGENT --model-class C --owned GLOB[,GLOB] --proof CMD
                 [--depends N,N] [--verdict author|verifier] [--live TEXT] [--interface-change]
  item-status <item> <decided|in work|built|shipped>
  ready [item]          rows whose dependencies are done, with the proposed Bar line and level
  measures [--release R]
  --console             all items and rows
A verdict is never written here: dispatch.py verify writes verdict records.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_common as fc  # noqa: E402


def item_file(item: str) -> Path:
    return fc.state_dir() / ("%s.json" % item)


def load_item_state(item: str) -> dict:
    p = item_file(item)
    if p.exists():
        return fc.load_json(p)
    return {"item": item, "status": "decided", "rows": {}, "updated": fc.now()}


def validate_state(st: dict) -> None:
    if st.get("status") not in fc.ITEM_STATUSES:
        raise fc.Refused("item status %r not in %s" % (st.get("status"), fc.ITEM_STATUSES))
    for n, r in st["rows"].items():
        if r.get("status") not in fc.STATUSES:
            raise fc.Refused("row %s: status %r not in %s" % (n, r.get("status"), fc.STATUSES))
        if len(r.get("evidence", "")) > 300:
            raise fc.Refused("row %s: evidence longer than 300 characters" % n)
        if r.get("model_class") and r["model_class"] not in fc.MODEL_CLASSES:
            raise fc.Refused("row %s: model class %r not in %s" % (n, r["model_class"], fc.MODEL_CLASSES))
        for c in r.get("causes", []):
            if c not in fc.CAUSES:
                raise fc.Refused("row %s: cause %r not in %s" % (n, c, fc.CAUSES))
        if r.get("eta") is not None and (not isinstance(r["eta"], int) or r["eta"] <= 0):
            raise fc.Refused("row %s: eta must be a positive number of minutes" % n)


def mutate(item: str, fn) -> dict:
    """Lock, re-read inside the lock, apply, validate, save."""
    with fc.Lock(fc.state_dir() / ("%s.lock" % item)):
        st = load_item_state(item)
        fn(st)
        validate_state(st)
        st["updated"] = fc.now()
        fc.save_json(item_file(item), st)
        return st


def new_row() -> dict:
    return {"status": "todo", "updated": fc.now(), "eta": None, "actual": None, "evidence": "",
            "rounds": 0, "model_class": None, "causes": [], "started": None}


def cmd_set(a) -> None:
    root = fc.repo_root()
    it = fc.find_item(fc.backlog(root), a.item)
    fc.find_task(it, a.row)
    if a.status not in fc.STATUSES:
        raise fc.Refused("status %r not in %s" % (a.status, fc.STATUSES))
    if a.status == "done":
        ok, reason, _ = fc.standing(root, a.item, a.row)
        if not ok:
            raise fc.Refused("done needs a standing verdict from the role its bar names: %s" % reason)
    if a.round and not a.cause:
        raise fc.Refused("--round needs --cause (the cause class of the second round)")

    def fn(st):
        r = st["rows"].setdefault(str(a.row), new_row())
        prev = r["status"]
        r["status"] = a.status
        r["updated"] = fc.now()
        if a.status == "doing" and not r.get("started"):
            r["started"] = fc.now()
        if a.status == "done" and r.get("started"):
            mins = (fc.parse_time(fc.now()) - fc.parse_time(r["started"])).total_seconds() / 60
            r["actual"] = round(mins)
        if a.eta is not None:
            r["eta"] = a.eta
        if a.evidence is not None:
            r["evidence"] = a.evidence
        if a.model_class:
            r["model_class"] = a.model_class
        if a.round:
            r["rounds"] = r.get("rounds", 0) + 1
            r.setdefault("causes", []).append(a.cause)
        elif prev == "todo" and a.status == "doing" and r.get("rounds", 0) == 0:
            r["rounds"] = 1
        if st["status"] == "decided" and a.status in ("doing", "done"):
            st["status"] = "in work"
    st = mutate(a.item, fn)
    if a.bar_incomplete:
        fc.append_event({"kind": "bar-incomplete", "item": a.item, "row": str(a.row), "text": a.bar_incomplete})
    if a.round:
        fc.append_event({"kind": "second-round", "item": a.item, "row": str(a.row), "cause": a.cause})
    print("row %s/%s: %s" % (a.item, a.row, st["rows"][str(a.row)]["status"]))


def cmd_add_row(a) -> None:
    root = fc.repo_root()
    bl_path = Path(root) / "docs" / "prd" / "backlog.json"
    if a.model_class not in fc.MODEL_CLASSES:
        raise fc.Refused("model class %r not in %s" % (a.model_class, fc.MODEL_CLASSES))
    if a.verdict not in fc.ROLES:
        raise fc.Refused("verdict role %r not in %s" % (a.verdict, fc.ROLES))
    with fc.Lock(fc.state_dir() / "backlog.lock"):
        bl = fc.load_json(bl_path)
        it = fc.find_item(bl, a.item)
        n = max([int(t["n"]) for t in it.get("tasks", [])] + [0]) + 1
        deps = [int(x) for x in a.depends.split(",")] if a.depends else []
        for d in deps:
            fc.find_task(it, d)
        it.setdefault("tasks", []).append({
            "n": n, "task": a.task, "owner": a.owner, "model_class": a.model_class,
            "owned_files": [g.strip() for g in a.owned.split(",") if g.strip()], "depends_on": deps,
            "proof": a.proof, "interface_change": bool(a.interface_change),
            "bar": {"proof": a.proof, "live": a.live, "verdict": a.verdict}})
        schema = fc.load_json(Path(root) / "docs" / "prd" / "backlog.schema.json")
        errs = fc.validate(bl, schema)
        if errs:
            raise fc.Refused("backlog fails its schema: " + "; ".join(errs[:5]))
        fc.save_json(bl_path, bl)
    mutate(a.item, lambda st: st["rows"].setdefault(str(n), new_row()))
    print("added row %s/%d" % (a.item, n))


def cmd_item_status(a) -> None:
    root = fc.repo_root()
    it = fc.find_item(fc.backlog(root), a.item)
    if a.status not in fc.ITEM_STATUSES:
        raise fc.Refused("item status %r not in %s" % (a.status, fc.ITEM_STATUSES))

    def fn(st):
        if a.status in ("built", "shipped"):
            for t in it.get("tasks", []):
                r = st["rows"].get(str(t["n"]))
                if not r or r["status"] != "done":
                    raise fc.Refused("%s needs every row done; row %s is %s"
                                     % (a.status, t["n"], r["status"] if r else "todo"))
                ok, reason, _ = fc.standing(root, a.item, t["n"])
                if not ok:
                    raise fc.Refused("row %s has no standing verdict: %s" % (t["n"], reason))
        st["status"] = a.status
    mutate(a.item, fn)
    print("item %s: %s" % (a.item, a.status))


def bar_line(root, it, t) -> str:
    owned = t.get("owned_files", [])
    pm_path = Path(root) / "docs" / "path-map.yaml"
    feats = []
    if pm_path.exists():
        for e in fc.load_json(pm_path).get("entries", []):
            if any(fc.matches(g.replace("**", "x"), owned) or fc.matches(o.replace("**", "x"), e["paths"])
                   for g in e["paths"] for o in owned):
                if e.get("feature"):
                    feats.append(e["feature"])
    risky = [o for o in owned if fc.matches(o.replace("**", "x"), fc.risk_globs(root))]
    need = fc.required_role(root, t, [o.replace("**", "x") for o in owned])
    live = ", ".join(sorted(set(feats))) if feats else (t.get("bar", {}).get("live") or "none + reason needed")
    return ("Bar: %s | live: %s | verdict: %s%s" % (t["proof"], live, need,
            "   (risk paths: %s)" % ", ".join(risky) if risky else ""))


def cmd_ready(a) -> None:
    root = fc.repo_root()
    bl = fc.backlog(root)
    lv = fc.level_for_repo(root)
    print("tier %s | working level %s off risk paths, %s on risk paths (ceiling %s, earned %s)"
          % (lv["tier"], lv["working_off_risk"], lv["working_on_risk"], lv["ceiling"], lv["earned"]))
    for it in bl.get("items", []):
        if a.item and it["id"] != a.item:
            continue
        st = load_item_state(it["id"])
        for t in it.get("tasks", []):
            r = st["rows"].get(str(t["n"]), new_row())
            if r["status"] != "todo":
                continue
            deps_ok = all(st["rows"].get(str(d), {}).get("status") == "done" for d in t.get("depends_on", []))
            if not deps_ok:
                continue
            print("%s/%s  %s  [%s, %s]" % (it["id"], t["n"], t["task"], t["owner"], t["model_class"]))
            print("    " + bar_line(root, it, t))


def cmd_console(_a) -> None:
    root = fc.repo_root()
    for it in fc.backlog(root).get("items", []):
        st = load_item_state(it["id"])
        print("%s  [%s]  %s" % (it["id"], st["status"], it.get("title", "")))
        for t in it.get("tasks", []):
            r = st["rows"].get(str(t["n"]), new_row())
            ok, reason, _ = fc.standing(root, it["id"], t["n"])
            print("   %-3s %-8s eta=%-4s actual=%-4s rounds=%s verdict=%s  %s"
                  % (t["n"], r["status"], r.get("eta"), r.get("actual"), r.get("rounds"), reason,
                     r.get("evidence", "")[:60]))


def cmd_measures(a) -> None:
    root = fc.repo_root()
    bl = fc.backlog(root)
    events = fc.read_events()
    items = [it for it in bl.get("items", []) if not a.release or it.get("release") == a.release]
    rows = []
    for it in items:
        st = load_item_state(it["id"])
        for t in it.get("tasks", []):
            rows.append((it, t, st["rows"].get(str(t["n"]), new_row())))
    done = [x for x in rows if x[2]["status"] == "done"]
    ftr = [x for x in done if x[2].get("rounds", 0) <= 1]
    causes: dict = {}
    for _, _, r in rows:
        for c in r.get("causes", []):
            causes[c] = causes.get(c, 0) + 1
    ids = {it["id"] for it in items}
    recs = [r for r in fc.load_records(root) if r.get("item") in ids]
    by_role: dict = {}
    for r in recs:
        by_role.setdefault(r.get("by"), []).append(r.get("verdict"))
    ev = [e for e in events if e.get("item") in ids]
    count = lambda k: sum(1 for e in ev if e.get("kind") == k)  # noqa: E731
    lv = fc.level_for_repo(root)
    prof = fc.profile(root)
    print("release: %s" % (a.release or "all"))
    print("packets: %d (done %d)" % (len(rows), len(done)))
    print("first-time-right: %s" % ("%d/%d = %.0f%%" % (len(ftr), len(done), 100 * len(ftr) / len(done))
                                   if done else "n/a"))
    print("second rounds by cause: %s" % (causes or "none"))
    print("escalations: %d" % count("escalation"))
    for role, vs in sorted(by_role.items()):
        fails = sum(1 for v in vs if v == "FAIL")
        print("verdicts by %s: %d (FAIL %d = %.0f%%)" % (role, len(vs), fails, 100 * fails / len(vs)))
    print("re-verifications: %d  causes: %s" % (count("reverify"),
          sorted({e.get("cause", "?") for e in ev if e.get("kind") == "reverify"}) or "none"))
    print("merge-result failures: %d" % count("merge-result-fail"))
    print("gate failures: %d" % count("gate-fail"))
    print("escaped defects (bar-gap): %d" % causes.get("bar-gap", 0))
    bi = [e for e in ev if e.get("kind") == "bar-incomplete"]
    print("'bar looks incomplete' lines: %d (judged right: %d)" % (len(bi), count("bar-incomplete-right")))
    print("working level: %s off risk / %s on risk" % (lv["working_off_risk"], lv["working_on_risk"]))
    need = prof.get("a2_evidence", {"min_packets": 10, "first_time_right": 0.8})
    if lv["earned"] == "A0":
        print("next level (A1): verify skill proven + Phase 3 gate accepted by the user")
    elif lv["earned"] == "A1":
        share = len(ftr) / len(done) if done else 0
        print("next level (A2): packets %d/%d, first-time-right %.0f%%/%.0f%%, escaped defects must be 0,"
              " blocking gate %s" % (len(done), need["min_packets"], 100 * share, 100 * need["first_time_right"],
                                     "on" if prof.get("enforcement", {}).get("main_protected") else "OFF"))
    trig = prof.get("extension_triggers", {})
    open_rows = sum(1 for x in rows if x[2]["status"] != "done")
    print("extension triggers: X1 planner %s (open rows %d > %s)" % (
        "MET" if open_rows > trig.get("x1_open_rows", 8) else "not met", open_rows, trig.get("x1_open_rows", 8)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="status.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--console", action="store_true", help="print all items and rows")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("set")
    s.add_argument("item")
    s.add_argument("row")
    s.add_argument("--status", required=True)
    s.add_argument("--eta", type=int)
    s.add_argument("--evidence")
    s.add_argument("--model-class")
    s.add_argument("--round", action="store_true")
    s.add_argument("--cause")
    s.add_argument("--bar-incomplete")
    s = sub.add_parser("add-row")
    s.add_argument("item")
    for k in ("--task", "--owner", "--model-class", "--owned", "--proof"):
        s.add_argument(k, required=True)
    s.add_argument("--depends")
    s.add_argument("--verdict", default="author")
    s.add_argument("--live", default="none")
    s.add_argument("--interface-change", action="store_true")
    s = sub.add_parser("item-status")
    s.add_argument("item")
    s.add_argument("status")
    s = sub.add_parser("ready")
    s.add_argument("item", nargs="?")
    s = sub.add_parser("measures")
    s.add_argument("--release")
    sub.add_parser("verdict", help="refused: verdicts are written only by dispatch.py verify")
    a = ap.parse_args(argv)
    try:
        if a.console:
            cmd_console(a)
        elif a.cmd == "set":
            cmd_set(a)
        elif a.cmd == "add-row":
            cmd_add_row(a)
        elif a.cmd == "item-status":
            cmd_item_status(a)
        elif a.cmd == "ready":
            cmd_ready(a)
        elif a.cmd == "measures":
            cmd_measures(a)
        elif a.cmd == "verdict":
            raise fc.Refused("verdicts are written only by tools/dispatch.py verify")
        else:
            ap.print_help()
    except fc.Refused as e:
        fc.refuse(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
