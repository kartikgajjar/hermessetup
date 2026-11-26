"""Judges: answer fixed-option questions about a page state, with a confidence.

Two interchangeable backends behind `Judge.choose(state, questions)`:

  LocalJudge -- the production llama-server (Hermes-2-Pro-8B, ChatML) via
      POST /completion with n_predict=1 and n_probs. Each option gets a short
      label that is a single token in the Llama-3 vocab (letters for actions,
      0..999 for element numbers), so the one generated token IS the answer and
      its softmax probability, renormalised over the valid labels, is the
      confidence. temperature<0 = greedy, but llama-server still reports the
      plain softmax of the logits (tools/server/README.md, `n_probs`). The state
      goes first in the prompt and the question last, so the second question on
      the same page reuses the cached prompt prefix (cache_prompt).

  JevJudge -- TypeSafe's Jev (`POST <base>/v1/systemone`), all questions in one
      call. Request/response shape is from third-party gateway docs (Zuplo,
      2026-10), not an official TypeSafe reference: UNVERIFIED until a real call
      succeeds. Sends the page state to a third party -- don't point it at
      signed-in pages you wouldn't paste into a SaaS.

A question is (name, instructions, options) where options maps option key ->
description (or None). Answers are {name: (option_key, confidence)}.
"""
from __future__ import annotations

import json
import math
import os
import string
import urllib.request
from dataclasses import dataclass


@dataclass
class Question:
    name: str
    instructions: str
    options: dict[str, str | None]


Answers = dict[str, tuple[str, float]]


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Judge:
    def choose(self, state: str, questions: list[Question]) -> Answers:
        raise NotImplementedError


class LocalJudge(Judge):
    def __init__(self, base_url: str = "http://127.0.0.1:8080", n_probs: int = 40):
        self.base_url = base_url.rstrip("/")
        self.n_probs = n_probs

    @staticmethod
    def _labels(options: dict[str, str | None]) -> list[str]:
        keys = list(options)
        # Numeric keys (element numbers) are already single tokens; others get A, B, C...
        if all(k.isdigit() and int(k) < 1000 for k in keys):
            return keys
        if len(keys) > 26:
            raise ValueError("non-numeric choice with more than 26 options")
        return list(string.ascii_uppercase[: len(keys)])

    def _ask(self, state: str, q: Question) -> tuple[str, float]:
        keys = list(q.options)
        labels = self._labels(q.options)
        if labels == keys:
            # Element lists are already in the state; repeating them would double the prompt.
            listing = "Answer with the element number from the list above."
        else:
            listing = "\n".join(
                f"{lab}. {key}" + (f" -- {desc}" if desc else "")
                for lab, key, desc in zip(labels, keys, q.options.values())
            ) + "\nAnswer with the letter only."
        prompt = (
            "<|im_start|>system\nYou are a decision classifier for a browser agent. "
            "Reply with a single option label and nothing else.<|im_end|>\n"
            f"<|im_start|>user\n{state}\n\nQuestion: {q.instructions}\n{listing}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        res = _post_json(f"{self.base_url}/completion", {
            "prompt": prompt,
            "n_predict": 1,
            "n_probs": self.n_probs,
            "temperature": -1,
            "cache_prompt": True,
        })
        probs = res.get("completion_probabilities") or []
        top = (probs[0].get("top_logprobs") or probs[0].get("top_probs") or []) if probs else []
        mass: dict[str, float] = {}
        for t in top:
            tok = t.get("token", "").strip().rstrip(".")
            p = t["prob"] if "prob" in t else math.exp(t["logprob"])
            if tok in labels:
                mass[tok] = mass.get(tok, 0.0) + p
        if not mass:
            return keys[0], 0.0
        total = sum(mass.values())
        best = max(mass, key=mass.get)
        # Renormalise over valid labels, but discount when most probability went
        # to tokens that aren't options at all (the model didn't really answer).
        conf = (mass[best] / total) * min(1.0, total / 0.5)
        return keys[labels.index(best)], conf

    def choose(self, state: str, questions: list[Question]) -> Answers:
        return {q.name: self._ask(state, q) for q in questions}


class JevJudge(Judge):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str = "jev/jev-latest"):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY not set")
        self.base_url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
        self.model = model

    def choose(self, state: str, questions: list[Question]) -> Answers:
        body = {
            "model": self.model,
            "state": state,
            "questions": {
                q.name: {"type": "choice", "instructions": q.instructions, "criteria": q.options}
                for q in questions
            },
        }
        res = _post_json(f"{self.base_url}/v1/systemone", body,
                         headers={"Authorization": f"Bearer {self.api_key}"}, timeout=30)
        out: Answers = {}
        for q in questions:
            a = res["answers"][q.name]
            out[q.name] = (str(a["choice"]), float(a.get("confidence", 0.0)))
        return out


def noul(name: str, instructions: str) -> Question:
    """Yes/no question, expressed as a two-option choice so both backends handle it."""
    return Question(name, instructions, {"yes": None, "no": None})
