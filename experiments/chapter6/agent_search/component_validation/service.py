"""Component-local provider diagnostics and paced durable calls.

This module intentionally sits outside the shared frozen v12.2 call layer.  A
new component-validation manifest records it through ``tooling_source`` while
the historical S3 and E2 manifests remain byte-for-byte verifiable.
"""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from chapter6_demo.v12_2.calls import (
    DurableCalls as _DurableCalls,
    ProviderFailure,
    response_envelope,
)
from chapter6_demo.v12_2.common import digest, read_json, save_json, utcnow
from chapter6_demo.v12_2.calls import HTTPTransport as _HTTPTransport


def _safe_text(value, limit=96):
    if value is None:
        return None
    text = re.sub(r"[^a-zA-Z0-9_.:/+-]", "", str(value))
    return text[:limit] or None


def error_category(status, code):
    value = str(code or "").lower()
    if value in {"1041", "connection_limit", "connectionlimit"}:
        return "connection_limit"
    if value in {"2056", "token_plan_limit", "quota_exhausted", "quota"}:
        return "quota_exhausted"
    if value in {"1002", "rate_limit", "ratelimit", "too_many_requests"} or status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "authentication"
    if status == 408:
        return "request_timeout"
    if status is not None and status >= 500:
        return "provider_server"
    return "provider_error"


def diagnose_http_error(exc):
    """Return bounded, redacted provider diagnostics without retaining body."""
    try:
        status = int(getattr(exc, "code", None))
    except (TypeError, ValueError):
        status = None
    headers = getattr(exc, "headers", None)

    def header(*names):
        for name in names:
            value = headers.get(name) if headers is not None else None
            if value:
                return _safe_text(value)
        return None

    code = None
    body_retry_after = None
    try:
        body = exc.read(65536)
        parsed = json.loads(body.decode("utf-8", "replace")) if body else {}
        error = parsed.get("error", {}) if isinstance(parsed, dict) else {}
        if isinstance(error, dict):
            code = error.get("code") or error.get("type")
            body_retry_after = error.get("retry_after")
        if code is None and isinstance(parsed, dict):
            code = parsed.get("code") or parsed.get("error_code")
            body_retry_after = body_retry_after or parsed.get("retry_after")
    except Exception:
        code = None
    code = _safe_text(code)
    return {
        "http_status": status,
        "business_code": code,
        "error_category": error_category(status, code),
        "trace_id": header("x-request-id", "request-id", "trace-id", "x-trace-id"),
        "retry_after": header("Retry-After", "retry-after") or _safe_text(body_retry_after),
    }


class DiagnosticHTTPTransport(_HTTPTransport):
    """HTTP transport with pacing and sanitized provider-failure metadata."""

    def __init__(self, provider, model, timeout, *, min_interval_seconds=0.0):
        super().__init__(provider, model, timeout)
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0

    def _pace(self):
        with self._rate_lock:
            wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def send(self, request, persist):
        self._pace()
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
            diagnostics = diagnose_http_error(exc)
            failure = ProviderFailure(
                "HTTP %s; category=%s; code=%s; no retry; token usage unknown" % (
                    diagnostics["http_status"], diagnostics["error_category"],
                    diagnostics["business_code"] or "unknown"))
            # The shared exception class is deliberately unchanged for frozen
            # archives; attach diagnostics as an instance-only attribute.
            failure.diagnostics = diagnostics
            raise failure from None
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(endpoint)
        authority = parts.hostname or ""
        if parts.port:
            authority += f":{parts.port}"
        envelope = response_envelope(body, seconds=time.perf_counter() - start,
                                     request_id=request_id, protocol=client.protocol)
        envelope["endpoint"] = urlunsplit((parts.scheme, authority, parts.path, "", ""))
        persist(envelope)


class DiagnosticDurableCalls(_DurableCalls):
    """Persist diagnostics after the shared durable call marks failure."""

    def complete(self, step, stage, system, prompt, max_tokens):
        try:
            return super().complete(step, stage, system, prompt, max_tokens)
        except ProviderFailure as exc:
            path = self.directory / "calls" / f"{step:03d}-{stage}" / "state.json"
            state = read_json(path) if path.exists() else {}
            diagnostics = getattr(exc, "diagnostics", None)
            if diagnostics:
                state.update({"error": str(exc), "diagnostics": diagnostics,
                              "diagnostics_recorded_utc": utcnow()})
                save_json(path, state)
            raise

    def usage(self):
        result = super().usage()
        for row in result.get("calls", []):
            path = self.directory / "calls" / f"{row['step']:03d}-{row['stage']}" / "state.json"
            if path.exists():
                row["diagnostics"] = read_json(path).get("diagnostics")
        return result


def pause_category(diagnostics):
    return (diagnostics or {}).get("error_category")


def pause_immediately(diagnostics):
    return pause_category(diagnostics) in {"quota_exhausted", "connection_limit", "authentication"}


class GlobalPauseGate:
    """Decide when a provider failure should stop new job dispatches."""

    def __init__(self, rate_limit_threshold=2):
        self.rate_limit_threshold = max(1, int(rate_limit_threshold))
        self.consecutive_rate_limits = 0

    def observe(self, diagnostics):
        category = pause_category(diagnostics)
        if category == "rate_limit":
            self.consecutive_rate_limits += 1
        else:
            self.consecutive_rate_limits = 0
        return {
            "pause": pause_immediately(diagnostics) or
                     self.consecutive_rate_limits >= self.rate_limit_threshold,
            "category": category,
            "consecutive_rate_limits": self.consecutive_rate_limits,
        }
