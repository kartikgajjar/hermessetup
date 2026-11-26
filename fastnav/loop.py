"""fastnav: a "System 1" browser loop -- numbered elements in, judged choices out.

    python fastnav\\loop.py --url https://example.com --goal "open the pricing page"
    python fastnav\\loop.py --url https://example.com --goal "open the blog" --goal "open the newest post"

Per step: chrome-mcp `snapshot` -> numbered element list -> judge answers two
choice questions (action, element) -> if both confidences >= --threshold, plain
code performs the action; otherwise the goal stops as `escalate` so a reasoning
agent (Claude Code / Hermes) can take over from the printed report.

The judge never writes text. The few places that need generation -- the text to
type, the value to select, and checking a claimed `done` against the page --
go to the local llama-server's chat endpoint (`Writer`), never to the judge.
A `done` is never trusted on the judge's word alone.

Guards from the known failure modes of this design:
  * consent banners: a keyword pre-filter + yes/no question, then a forced
    click on the accept button before any goal step;
  * iframes / file inputs: snapshot only sees the top frame and the judge can't
    upload, so their presence is noted in the state and in the report;
  * short memory: each goal sees only its last few actions, so chain short
    goals (one per page or form) instead of one long one.

Each run writes one JSON line per step to fastnav\\runs\\<timestamp>.jsonl
(latency, both confidences, action taken) for comparing judges.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge import Judge, JevJudge, LocalJudge, Question, _post_json, noul  # noqa: E402
from mcp_client import McpError, McpStdioClient  # noqa: E402

HERE = Path(__file__).resolve().parent
CHROME_MCP_CLI = HERE.parent / "chrome-mcp" / "dist" / "src" / "cli.js"

ACTIONS = {
    "click": "click the chosen element (link, button, tab, checkbox)",
    "type": "type text into the chosen input box",
    "select": "choose a value in the chosen dropdown",
    "scroll_down": "what is needed is probably further down the page",
    "scroll_up": "what is needed is probably further up the page",
    "wait": "the page is still loading",
    "done": "the goal is already achieved on this page",
    "blocked": "the goal cannot be achieved from here (login wall, error page, captcha, missing content)",
}
CONSENT_WORDS = re.compile(r"\b(accept( all)?|agree|allow( all)?|i understand|got it)\b", re.I)
CONSENT_CONTEXT = re.compile(r"cookie|consent|privacy|gdpr|tracking", re.I)
HISTORY = 5
MAX_ELEMENTS = 150


class Writer:
    """Generation the judge can't do, via llama-server's OpenAI-compatible chat endpoint."""

    def __init__(self, base_url: str):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"

    def _chat(self, prompt: str, max_tokens: int) -> str:
        res = _post_json(self.url, {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        })
        return res["choices"][0]["message"]["content"].strip()

    def text_for(self, goal: str, element: str) -> str:
        return self._chat(
            f"Goal: {goal}\nThe browser agent is about to type into: {element}\n"
            "Reply with exactly the text to type, nothing else. Use only facts from the goal.",
            60,
        ).strip('"')

    def verify(self, goal: str, url: str, title: str, page_text: str) -> tuple[bool, str]:
        # URL + title matter: without them the 8B model called medium.com/about
        # "the homepage" from its body text alone (2026-10-20).
        reply = self._chat(
            f"Goal: {goal}\n\nCurrent page URL: {url}\nTitle: {title}\n"
            f"Page content (markdown, truncated):\n{page_text}\n\n"
            "Is the goal actually achieved on this page? Error pages, login walls, "
            "and pages that merely link to the target do NOT count. "
            "Answer YES or NO, then a one-line reason.",
            80,
        )
        return reply.upper().startswith("YES"), reply


class Browser:
    def __init__(self, domains: list[str]):
        flags = []
        for d in domains:
            flags += ["--allow-domain", d, "--allow-domain", f"*.{d}"]
        self.mcp = McpStdioClient([
            "node", str(CHROME_MCP_CLI), *flags,
            "--enable-mutations", "--persist-token", "--no-auto-update", "--no-telemetry",
        ])
        self.tab: str | None = None

    def open(self, url: str) -> None:
        self.tab = self.mcp.call("tab_new", {"url": url})["tabId"]
        time.sleep(1.5)  # first snapshot after tab-open can be empty (CLAUDE.md)

    def snapshot(self) -> dict:
        return self.mcp.call("snapshot", {"tabId": self.tab, "interactiveOnly": True, "max": MAX_ELEMENTS})

    def notes(self) -> list[str]:
        out = []
        try:
            frames = self.mcp.call("frames_list", {"tabId": self.tab})
            if frames.get("count", 1) > 1:
                out.append(f"page has {frames['count'] - 1} iframe(s) whose elements are NOT listed")
        except McpError:
            pass
        try:
            html = self.mcp.call("get_html", {"tabId": self.tab, "selector": "input[type=file]", "maxBytes": 200})
            if isinstance(html, dict) and html.get("html"):
                out.append("page has a file upload input (agent cannot upload)")
        except McpError:
            pass
        return out

    def act(self, action: str, ref: str | None, text: str | None = None, enter: bool = False) -> None:
        base = {"tabId": self.tab}
        if action == "click":
            self.mcp.call("click", {**base, "ref": ref})
        elif action == "type":
            self.mcp.call("type", {**base, "ref": ref, "text": text, "clear": True, "pressEnter": enter, "trusted": True})
        elif action == "select":
            self.mcp.call("select_option", {**base, "ref": ref, "values": [text]})
        elif action in ("scroll_down", "scroll_up"):
            self.mcp.call("scroll", {**base, "deltaY": 800 if action == "scroll_down" else -800})
        elif action == "wait":
            time.sleep(1.0)

    def page_text(self, max_bytes: int = 6000) -> str:
        res = self.mcp.call("read_as_markdown", {"tabId": self.tab, "maxBytes": max_bytes})
        return res.get("markdown", json.dumps(res)) if isinstance(res, dict) else str(res)


def describe(node: dict) -> str:
    name = (node.get("name") or "").replace("\n", " ").strip()[:80]
    extra = f" value={node['value'][:40]!r}" if node.get("value") else ""
    return f'{node.get("role", node.get("tag"))} "{name}"{extra}'


def build_state(goal: str, snap: dict, history: list[str], notes: list[str]) -> tuple[str, list[dict]]:
    nodes = [n for n in snap.get("nodes", []) if not n.get("disabled")][:MAX_ELEMENTS]
    lines = [f"{i}. {describe(n)}" for i, n in enumerate(nodes, 1)]
    state = (
        f"Goal: {goal}\nPage: {snap.get('title', '')} ({snap.get('url', '')})\n"
        + ("Notes: " + "; ".join(notes) + "\n" if notes else "")
        + "Recent actions:\n" + ("\n".join(f"- {h}" for h in history[-HISTORY:]) or "- none") + "\n"
        + "Interactive elements:\n" + "\n".join(lines)
    )
    return state, nodes


def consent_target(judge: Judge, state: str, nodes: list[dict]) -> dict | None:
    cands = [n for n in nodes if CONSENT_WORDS.search(n.get("name") or "")]
    if not cands or not CONSENT_CONTEXT.search(state):
        return None
    ans = judge.choose(state, [noul("consent", "Is a cookie or consent banner shown that must be accepted before using the page?")])
    choice, conf = ans["consent"]
    return cands[0] if choice == "yes" and conf >= 0.7 else None


def run_goal(goal: str, browser: Browser, judge: Judge, writer: Writer, args, log) -> dict:
    history: list[str] = []
    rejected_done = 0
    last_sig = None
    for step in range(1, args.max_steps + 1):
        t0 = time.perf_counter()
        snap = browser.snapshot()
        notes = browser.notes()
        state, nodes = build_state(goal, snap, history, notes)

        banner = consent_target(judge, state, nodes)
        if banner:
            browser.act("click", banner["ref"])
            history.append(f"accepted consent banner via {describe(banner)}")
            log({"goal": goal, "step": step, "action": "consent", "ms": int((time.perf_counter() - t0) * 1000)})
            time.sleep(args.settle)
            continue

        # Two questions, asked in sequence: the element question names the chosen
        # action. Asked blind ("which element for that action? 0 = none") the
        # local judge hedged toward 0 -- on medium.com the right link scored 0.43
        # blind vs 0.93 when told the action is a click (2026-10-20).
        tj = time.perf_counter()
        action, a_conf = judge.choose(state, [
            Question("action", "What should the browser agent do next to make progress on the goal?", ACTIONS)
        ])["action"]
        needs_el = action in ("click", "type", "select")
        el, e_conf, node = "0", 1.0, None
        if needs_el and nodes:
            el, e_conf = judge.choose(state, [Question(
                "element",
                f"The agent will {action.upper()} an element to make progress on the goal. Which numbered element?",
                {str(i): None for i in range(1, len(nodes) + 1)},
            )])["element"]
            node = nodes[int(el) - 1]
        judge_ms = int((time.perf_counter() - tj) * 1000)
        rec = {"goal": goal, "step": step, "url": snap.get("url"), "action": action, "action_conf": round(a_conf, 3),
               "element": el, "element_desc": describe(node) if node else None, "element_conf": round(e_conf, 3),
               "judge_ms": judge_ms, "notes": notes}

        low = a_conf < args.threshold or (needs_el and (node is None or e_conf < args.threshold))
        if action == "blocked" or low:
            rec["ms"] = int((time.perf_counter() - t0) * 1000)
            log(rec)
            return {"goal": goal, "status": "escalate",
                    "reason": "judge says blocked" if action == "blocked" else "confidence below threshold",
                    "steps": step, "last": rec, "history": history, "state": state}

        # Repeat guard: the same action on the same element without the URL
        # changing means the judge didn't notice it had arrived (seen live on
        # medium.com/about: "About Medium" clicked 5x). Treat it as a done claim.
        sig = (action, describe(node) if node else None, snap.get("url"))
        repeated = sig == last_sig
        last_sig = sig
        if repeated and action != "done":
            history.append(f"repeated {action} with no page change")
            action = "done"
            rec["repeat_guard"] = True

        if action == "done":
            ok, why = writer.verify(goal, snap.get("url", ""), snap.get("title", ""), browser.page_text())
            rec["verified"] = ok
            rec["ms"] = int((time.perf_counter() - t0) * 1000)
            log(rec)
            if ok:
                return {"goal": goal, "status": "done", "steps": step, "url": snap.get("url"), "verifier": why, "history": history}
            rejected_done += 1
            history.append(f"claimed done but verifier said: {why}")
            if rejected_done >= 2:
                return {"goal": goal, "status": "escalate", "reason": "done rejected twice by verifier",
                        "steps": step, "history": history, "state": state}
            continue

        text = None
        if action in ("type", "select"):
            text = writer.text_for(goal, describe(node))
            rec["text"] = text
        if args.dry_run:
            rec["dry_run"] = True
            rec["ms"] = int((time.perf_counter() - t0) * 1000)
            log(rec)
            return {"goal": goal, "status": "dry_run", "would": rec}
        enter = action == "type" and re.search(r"search", describe(node), re.I) is not None
        browser.act(action, node["ref"] if node else None, text, enter)
        history.append(f"{action} {describe(node) if node else ''}{f' text={text!r}' if text else ''}".strip())
        rec["ms"] = int((time.perf_counter() - t0) * 1000)
        log(rec)
        time.sleep(args.settle)
    return {"goal": goal, "status": "escalate", "reason": f"hit --max-steps {args.max_steps}", "history": history}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True)
    p.add_argument("--goal", action="append", required=True, help="repeat to chain short goals")
    p.add_argument("--judge", choices=["local", "jev"], default="local")
    p.add_argument("--llama", default="http://127.0.0.1:8080", help="llama-server base URL")
    p.add_argument("--allow-domain", action="append", help="default: the --url host without www.")
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--max-steps", type=int, default=12)
    p.add_argument("--settle", type=float, default=0.6, help="seconds to let the page settle after an action")
    p.add_argument("--dry-run", action="store_true", help="judge the first step of each goal, act on nothing")
    args = p.parse_args()

    host = urllib.parse.urlparse(args.url).hostname or ""
    domains = args.allow_domain or [host.removeprefix("www.")]
    judge: Judge = LocalJudge(args.llama) if args.judge == "local" else JevJudge()
    writer = Writer(args.llama)

    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    log_path = runs / f"{datetime.now():%Y%m%d-%H%M%S}-{args.judge}.jsonl"
    log_file = log_path.open("a", encoding="utf-8")

    def log(rec: dict) -> None:
        log_file.write(json.dumps(rec) + "\n")
        log_file.flush()
        print(f"  step {rec['step']}: {rec['action']} "
              f"{rec.get('element_desc') or ''} "
              f"(a={rec.get('action_conf', '-')}, e={rec.get('element_conf', '-')}, {rec.get('ms', '?')} ms)",
              file=sys.stderr)

    browser = Browser(domains)
    results = []
    try:
        browser.open(args.url)
        for goal in args.goal:
            print(f"goal: {goal}", file=sys.stderr)
            r = run_goal(goal, browser, judge, writer, args, log)
            results.append(r)
            if r["status"] != "done":
                break  # chained goals depend on the previous one
    finally:
        browser.mcp.close()
        log_file.close()

    print(json.dumps({"log": str(log_path), "results": results}, indent=2))
    return 0 if results and all(r["status"] == "done" for r in results) and len(results) == len(args.goal) else 2


if __name__ == "__main__":
    sys.exit(main())
