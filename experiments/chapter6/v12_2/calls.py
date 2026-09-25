"""Durable, single-attempt model calls. Construction alone never calls a model."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .common import digest, read_json, save_json, utcnow


class IndeterminateCall(RuntimeError):
    """A sent request has no durable response; no automatic retry is safe."""


class ProviderFailure(RuntimeError):
    pass


def no_hook(*args):
    pass


def response_envelope(body, *, seconds=0.0, request_id="", protocol="openai"):
    return {"body_base64": base64.b64encode(body).decode("ascii"),
            "seconds": seconds, "request_id": request_id, "protocol": protocol}


def decode_response(envelope):
    try:
        data = json.loads(base64.b64decode(envelope["body_base64"], validate=True))
        usage = data.get("usage") or {}
        if envelope["protocol"] == "anthropic":
            text = "\n".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
            input_tokens = usage.get("input_tokens")
            if input_tokens is not None:
                input_tokens += (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
            output_tokens = usage.get("output_tokens")
        else:
            choices = data.get("choices", [])
            text = choices[0].get("message", {}).get("content") if choices else ""
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        tokens = [x if type(x) is int and x >= 0 else None for x in (input_tokens, output_tokens)]
        return {"text": text if isinstance(text, str) else "",
                "returned_model": data.get("model"), "usage_raw": usage,
                "input_tokens": tokens[0], "output_tokens": tokens[1],
                "usage_complete": None not in tokens, "seconds": envelope["seconds"],
                "request_id": envelope["request_id"] or str(data.get("id", ""))}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ProviderFailure("Stored provider envelope could not be parsed; it will not be reposted.") from exc


class HTTPTransport:
    """Read authorized OpenCode credentials only at an explicit live start."""
    def __init__(self, provider, model, timeout):
        from chapter6_demo.providers import ModelClient
        self.client = ModelClient.from_opencode(provider, model, timeout=timeout)

    def send(self, request, persist):
        client = self.client
        headers = {"Content-Type": "application/json"}
        payload = {"model": request["model"], "max_tokens": request["max_tokens"],
                   "temperature": request["temperature"]}
        if client.protocol == "anthropic":
            endpoint = client.base_url + "/messages"
            headers.update({"x-api-key": client._key, "anthropic-version": "2023-06-01"})
            payload.update(system=request["system"], messages=[{"role": "user", "content": request["prompt"]}])
        else:
            endpoint = client.base_url + "/chat/completions"
            headers["Authorization"] = "Bearer " + client._key
            payload["messages"] = [{"role": "system", "content": request["system"]},
                                   {"role": "user", "content": request["prompt"]}]
            if request["model"].lower().startswith("qwen"):
                payload["enable_thinking"] = False
        start = time.perf_counter()
        try:
            with urlopen(Request(endpoint, json.dumps(payload).encode(), headers, method="POST"),
                         timeout=client.timeout) as response:
                body = response.read()
                request_id = response.headers.get("x-request-id", "")
        except HTTPError as exc:
            # Do not persist error bodies or headers which may echo secrets.
            raise ProviderFailure(f"HTTP {exc.code}; no retry; token usage unknown") from None
        # Serialize the response before parsing any model JSON or usage.
        # Endpoint metadata is redacted (no userinfo, query or auth headers).
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(endpoint)
        authority = parts.hostname or ""
        if parts.port:
            authority += f":{parts.port}"
        envelope = response_envelope(body, seconds=time.perf_counter() - start,
                                     request_id=request_id, protocol=client.protocol)
        envelope["endpoint"] = urlunsplit((parts.scheme, authority, parts.path, "", ""))
        persist(envelope)


class DurableCalls:
    def __init__(self, directory, config, transport, hook=no_hook):
        self.directory, self.config = Path(directory), config
        self.transport, self.hook = transport, hook

    def complete(self, step, stage, system, prompt, max_tokens):
        folder = self.directory / "calls" / f"{step:03d}-{stage}"
        request = {"run_config_sha256": digest(self.config), "step": step, "stage": stage,
                   "provider": self.config["provider"], "model": self.config["model"],
                   "system": system, "prompt": prompt, "max_tokens": max_tokens,
                   "temperature": self.config["parameters"]["temperature"]}
        request_sha = digest(request)
        save_json(folder / "request.json", request, immutable=True)
        raw_path, state_path = folder / "raw_response.json", folder / "state.json"
        if not raw_path.exists():
            if state_path.exists():
                state = read_json(state_path)
                if state["request_sha256"] != request_sha:
                    raise ValueError("Call state/request fingerprint mismatch.")
                if state["status"] != "prepared":
                    raise IndeterminateCall("Request has no durable response; refusing an unaccounted retry.")
            else:
                save_json(state_path, {"status": "prepared", "request_sha256": request_sha})
            self.hook("request_prepared", step, stage)
            save_json(state_path, {"status": "sent_unknown", "request_sha256": request_sha,
                                   "sent_utc": utcnow()})
            self.hook("request_sent_marker", step, stage)
            def persist(envelope):
                save_json(raw_path, {"request_sha256": request_sha, "envelope": envelope,
                                     "envelope_sha256": digest(envelope), "received_utc": utcnow()}, immutable=True)
                self.hook("response_persisted", step, stage)
            try:
                self.transport.send(request, persist)
            except ProviderFailure as exc:
                save_json(state_path, {"status": "provider_failed", "request_sha256": request_sha,
                                       "error_type": type(exc).__name__, "usage": None})
                raise
            except Exception as exc:
                # Any transport or disk exception after dispatch may be billed.
                # Raw response, if already durable, is recovered on the next run.
                raise IndeterminateCall("Call interrupted after dispatch; response status is uncertain.") from exc
        if not raw_path.exists():
            raise IndeterminateCall("Transport returned without persisting a response.")
        raw = read_json(raw_path)
        if raw["request_sha256"] != request_sha or raw["envelope_sha256"] != digest(raw["envelope"]):
            raise ValueError("Stored response fingerprint mismatch.")
        response = decode_response(raw["envelope"])
        save_json(folder / "response.json", {"request_sha256": request_sha, **response}, immutable=True)
        save_json(state_path, {"status": "response_persisted", "request_sha256": request_sha})
        return response

    def usage(self):
        rows = []
        for folder in sorted((self.directory / "calls").glob("*")):
            state_file = folder / "state.json"
            if not state_file.exists() or read_json(state_file)["status"] == "prepared":
                continue
            request, state = read_json(folder / "request.json"), read_json(state_file)
            response = read_json(folder / "response.json") if (folder / "response.json").exists() else {}
            rows.append({"step": request["step"], "stage": request["stage"],
                         "requested_model": request["model"], "status": state["status"],
                         **{key: response.get(key) for key in ("returned_model", "input_tokens",
                             "output_tokens", "usage_complete", "seconds", "request_id")}})
        complete = all(row.get("usage_complete") for row in rows)
        known = sum((row.get("input_tokens") or 0) + (row.get("output_tokens") or 0) for row in rows)
        return {"calls": rows, "call_attempts": len(rows), "usage_complete": complete,
                "known_tokens": known, "total_tokens": known if complete else None}
