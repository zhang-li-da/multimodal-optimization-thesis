"""Read authorized OpenCode credentials in memory and record redacted usage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .inspect_environment import jsonc_load

CHINESE_PROVIDERS = {
    "alibaba-token-plan-cn", "minimax-cn-coding-plan", "kimi-for-coding",
    "kimi-for-coding-cn", "zhipuai-coding-plan", "deepseek",
}


class ModelError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    request_id: str

    def usage(self):
        return {k: v for k,v in self.__dict__.items() if k != "text"}


class ModelClient:
    def __init__(self, provider, model, base_url, key, protocol="openai", timeout=120):
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._key = key
        self.protocol = protocol
        self.timeout = timeout

    @classmethod
    def from_opencode(cls, provider, model, timeout=120):
        if provider not in CHINESE_PROVIDERS:
            raise ModelError("Choose an authorized Chinese coding-plan provider.")
        user = Path.home()
        auth = jsonc_load(user / ".local/share/opencode/auth.json")
        config_file = user / ".config/opencode/opencode.jsonc"
        cfg = jsonc_load(config_file) if config_file.exists() else {}
        catalog_file = Path(__file__).resolve().parents[1] / "_analysis/models_dev.json"
        if not catalog_file.exists():
            catalog_file = user / ".cache/opencode/models.json"
        catalog = jsonc_load(catalog_file)
        entry = {**catalog.get(provider, {}), **cfg.get("provider", {}).get(provider, {})}
        options = entry.get("options", {})
        key = options.get("apiKey") or auth.get(provider, {}).get("key")
        if key and key.startswith("{env:") and key.endswith("}"):
            key = os.getenv(key[5:-1])
        base_url = options.get("baseURL") or entry.get("api")
        if not key or not base_url:
            raise ModelError("Provider credentials or endpoint missing from OpenCode metadata.")
        if model not in entry.get("models", {}):
            raise ModelError("Requested model is absent from the provider catalog.")
        protocol = "anthropic" if "anthropic" in entry.get("npm", "") else "openai"
        return cls(provider, model, base_url, key, protocol, timeout)

    def complete(self, system, prompt, max_tokens=2400, temperature=0.7):
        start = time.perf_counter()
        headers = {"Content-Type": "application/json"}
        if self.protocol == "anthropic":
            endpoint = self.base_url + "/messages"
            headers.update({"x-api-key": self._key, "anthropic-version": "2023-06-01"})
            payload = {"model": self.model, "max_tokens": max_tokens,
                "temperature": temperature, "system": system,
                "messages": [{"role":"user", "content":prompt}]}
        else:
            endpoint = self.base_url + "/chat/completions"
            headers["Authorization"] = "Bearer " + self._key
            payload = {"model": self.model, "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role":"system","content":system},
                             {"role":"user","content":prompt}]}
            if self.model.lower().startswith("qwen"):
                payload["enable_thinking"] = False
        request = Request(endpoint, json.dumps(payload).encode(), headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
                request_id = response.headers.get("x-request-id", "")
        except HTTPError as exc:
            # Never echo response bodies or request headers: some gateways
            # repeat credentials. Status + bounded error code suffice.
            try:
                error = json.loads(exc.read()).get("error", {})
                code = error.get("code", "http_error") if isinstance(error, dict) else "http_error"
            except Exception:
                code = "http_error"
            code = re.sub(r"[^a-zA-Z0-9_.-]", "", str(code))[:64]
            raise ModelError(f"HTTP {exc.code}; provider={self.provider}; code={code}") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ModelError(f"{type(exc).__name__}; provider={self.provider}") from None
        usage = data.get("usage", {})
        if self.protocol == "anthropic":
            output = "\n".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
            input_tokens, output_tokens = usage.get("input_tokens",0), usage.get("output_tokens",0)
        else:
            choices = data.get("choices", [])
            if not choices:
                raise ModelError("API returned no choices.")
            output = choices[0].get("message", {}).get("content") or ""
            input_tokens, output_tokens = usage.get("prompt_tokens",0), usage.get("completion_tokens",0)
        if not isinstance(output, str) or not output.strip():
            raise ModelError("API returned no usable text within output budget.")
        return Completion(output, data.get("model", self.model), int(input_tokens), int(output_tokens),
                          time.perf_counter()-start, request_id or str(data.get("id", "")))


def parse_json(text):
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if clean.startswith("~~~") or clean.startswith(chr(96)*3):
        clean = "\n".join(clean.splitlines()[1:-1])
    decoder = json.JSONDecoder()
    for index,ch in enumerate(clean):
        if ch in "[{":
            try:
                return decoder.raw_decode(clean[index:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON value in model response.")


def probe(provider, model, output):
    client = ModelClient.from_opencode(provider, model, timeout=55)
    result = {"provider": provider, "requested_model": model}
    try:
        response = client.complete(
            "You are a coding assistant. Return a JSON object only.",
            'Implement a Python function priority(f) returning -f["gap"]. '
            'Return {"code":"..."} with actual newline escapes in valid JSON.',
            max_tokens=800, temperature=0.3)
        data = parse_json(response.text)
        result.update(ok=True, code=data.get("code"), usage=response.usage())
    except (ModelError, ValueError, KeyError) as exc:
        result.update(ok=False, error=str(exc))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "code"}, ensure_ascii=True))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="alibaba-token-plan-cn")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--output", default="chapter6_demo/runs/provider_probe.json")
    args = parser.parse_args()
    probe(args.provider, args.model, args.output)
