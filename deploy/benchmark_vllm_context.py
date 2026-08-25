#!/usr/bin/env python3
"""Probe the effective chat-context boundary of an OpenAI-compatible server."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


SYSTEM_PROMPT = "Answer briefly and use only the supplied context."
FILLER = (
    "Remote attestation verifies a trusted execution environment by checking "
    "signed measurements, freshness evidence, and the expected trust policy. "
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8100/v1")
    parser.add_argument("--model", default="qwen3.6-27b")
    parser.add_argument("--targets", default="8192,12288,14336,15000,15200")
    parser.add_argument("--max-output-tokens", type=int, default=32)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--needle", action="store_true")
    return parser.parse_args()


def post_json(url: str, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def token_count(args: argparse.Namespace, text: str) -> int:
    result = post_json(
        f"{args.url.removesuffix('/v1')}/tokenize",
        {"model": args.model, "prompt": text},
        args.timeout,
    )
    if result.get("count") is not None:
        return int(result["count"])
    return len(result.get("tokens") or [])


def build_prompt(args: argparse.Namespace, target: int) -> tuple[str, int]:
    overhead = token_count(args, "")
    tokens_per_block = max(1, token_count(args, FILLER) - overhead)
    estimate = max(1, (target - overhead) // tokens_per_block)
    low, high = max(1, estimate - 4), estimate + 4
    while token_count(args, FILLER * high) <= target:
        low, high = high, high + 4
    while low < high:
        mid = (low + high + 1) // 2
        if token_count(args, FILLER * mid) <= target:
            low = mid
        else:
            high = mid - 1
    text = FILLER * low
    return text, token_count(args, text)


def needle_prompt(text: str) -> str:
    first = len(text) // 3
    second = first * 2
    return (
        f"{text[:first]}\nEvidence record A has code ALPHA-731.\n"
        f"{text[first:second]}\nEvidence record B has code BRAVO-284.\n"
        f"{text[second:]}\nEvidence record C has code CHARLIE-906.\n"
        "Return only the three evidence codes in A, B, C order."
    )


def completion_prompt(text: str) -> str:
    return f"{SYSTEM_PROMPT}\n\n{text}\n\nAnswer:"


def probe(args: argparse.Namespace, text: str) -> dict:
    if args.needle:
        payload = {
            "model": args.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": args.max_output_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        endpoint = "chat/completions"
    else:
        payload = {
            "model": args.model,
            "prompt": completion_prompt(text),
            "temperature": 0,
            "max_tokens": args.max_output_tokens,
        }
        endpoint = "completions"
    request = urllib.request.Request(
        f"{args.url.rstrip('/')}/{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            result = json.load(response)
        usage = result.get("usage") or {}
        choice = (result.get("choices") or [{}])[0]
        response_text = str(
            (choice.get("message") or {}).get("content")
            if args.needle
            else choice.get("text")
            or ""
        ).strip()
        return {
            "ok": True,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "prompt_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "finish_reason": choice.get("finish_reason"),
            "response": response_text[:200],
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "status": exc.code,
            "error": exc.read().decode("utf-8", errors="replace")[:500],
        }


def main() -> None:
    args = parse_args()
    for target in (int(value) for value in args.targets.split(",") if value.strip()):
        content_target = max(256, target - (160 if args.needle else 32))
        text, _estimated_tokens = build_prompt(args, content_target)
        if args.needle:
            text = needle_prompt(text)
        estimated_tokens = token_count(args, completion_prompt(text))
        result = probe(args, text)
        result.update(
            {
                "target_tokens": target,
                "estimated_tokens": estimated_tokens,
                "needle": args.needle,
            }
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
