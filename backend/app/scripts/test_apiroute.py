#!/usr/bin/env python3
"""Smoke-test the APIRoute OpenAI-compatible chat-completions endpoint.

Usage:
    DOUBAO_API_KEY='...' python3 backend/scripts/test_apiroute.py --stream
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx


BASE_URL = os.getenv("DOUBAO_BASE_URL", "https://apiroute.bodenai.net/v1").rstrip("/")
MODEL = os.getenv("DOUBAO_MODEL", "doubao-seed-2-0-pro-260215")


def main() -> int:
    api_key = os.getenv("APIROUTE_API_KEY") or os.getenv("DOUBAO_API_KEY")
    if not api_key:
        print("Missing APIROUTE_API_KEY. Set it in the environment before running this script.", file=sys.stderr)
        return 2

    stream = "--stream" in sys.argv
    payload: dict[str, object] = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "Reply exactly: APIRoute stream compatible."},
        ],
        "temperature": 0,
        "max_tokens": 32,
    }
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

    try:
        with httpx.Client(timeout=30) as client:
            if stream:
                return stream_request(client, api_key, payload)
            response = client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    if response.is_error:
        print(f"APIRoute returned HTTP {response.status_code}", file=sys.stderr)
        print(response.text[:1000], file=sys.stderr)
        return 1

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"Unexpected successful response: {exc}", file=sys.stderr)
        print(response.text[:1000], file=sys.stderr)
        return 1

    print(f"APIRoute connection succeeded (HTTP {response.status_code}).")
    print(f"Model: {body.get('model', MODEL)}")
    print(f"Reply: {content}")
    return 0


def stream_request(client: httpx.Client, api_key: str, payload: dict[str, object]) -> int:
    """验证服务端是否以 OpenAI SSE 格式持续推送并在结尾给出 usage。"""
    started = time.monotonic()
    first_chunk_at: float | None = None
    chunks = 0
    finished = False
    content = ""
    usage = None
    with client.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
            chunks += 1
            usage = chunk.get("usage") or usage
            choices = chunk.get("choices") or []
            if choices:
                content += choices[0].get("delta", {}).get("content", "") or ""

    if not finished:
        print("Streaming response ended without [DONE].", file=sys.stderr)
        return 1
    print("APIRoute streaming is compatible.")
    print(f"Model: {MODEL}")
    print(f"First data chunk: {(first_chunk_at - started) if first_chunk_at else 0:.2f}s")
    print(f"SSE chunks: {chunks}")
    print(f"Usage returned: {'yes' if usage else 'no'}")
    print(f"Reply: {content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
