"""Single-attempt OpenCode-configured transport with bounded reads and diagnostics."""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from chapter6_demo.v12_2.calls import HTTPTransport, ProviderFailure, response_envelope
from chapter6_demo.v12_2.common import save_json, utcnow
from ..component_validation.service import diagnose_http_error, _safe_text


class RecoveryTransport(HTTPTransport):
    """Uses the exact local OpenCode provider; never retries a request."""

    def __init__(self, provider, model, directory, timeout=600):
        super().__init__(provider, model, timeout)
        self.directory = directory
        self.maximum_timeout = float(timeout)
        self.deadline = None
        self.last_started = 0.0
        self.last_diagnostics = None

    def set_wall_deadline(self, deadline):
        self.deadline = deadline

    def remaining(self):
        if self.deadline is None:
            raise ValueError('An absolute task deadline is required')
        remaining = self.deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('task deadline reached')
        return min(self.maximum_timeout, remaining)

    def send(self, request, persist):
        wait = max(0.0, 1.0 - (time.perf_counter() - self.last_started))
        if wait:
            time.sleep(min(wait, self.remaining()))
        timeout = self.remaining()
        self.last_started = time.perf_counter()
        self.last_diagnostics = None
        client = self.client
        if (client.protocol != 'openai' or request['model'] != 'MiniMax-M3'
                or request['provider'] != 'minimax-cn-coding-plan'):
            raise ValueError('Recovery transport is frozen to OpenCode MiniMax-M3')
        endpoint = client.base_url + '/chat/completions'
        payload = {'model': request['model'], 'max_tokens': request['max_tokens'],
                   'temperature': request['temperature'], 'stream': False,
                   'messages': [{'role': 'system', 'content': request['system']},
                                {'role': 'user', 'content': request['prompt']}]}
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + client._key}
        folder = self.directory / 'calls' / f"{request['step']:03d}-{request['stage']}"
        start = time.perf_counter()
        chunks = []
        status = None
        request_id = ''
        try:
            with urlopen(Request(endpoint, json.dumps(payload).encode(), headers, method='POST'),
                         timeout=timeout) as response:
                status = response.status
                request_id = _safe_text(response.headers.get('x-request-id', '')) or ''
                while True:
                    remaining = self.remaining()
                    # Rebind each read to the absolute job deadline. A server
                    # sending small chunks cannot reset the common wall budget.
                    raw = getattr(getattr(response, 'fp', None), 'raw', None)
                    sock = getattr(raw, '_sock', None)
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if sum(map(len, chunks)) > 8 * 1024 * 1024:
                        raise ValueError('response exceeds frozen byte bound')
        except Exception as exc:
            if isinstance(exc, HTTPError):
                diagnostics = diagnose_http_error(exc)
            else:
                cause = getattr(exc, 'reason', None) or exc.__cause__
                diagnostics = {
                    'http_status': status, 'business_code': None,
                    'error_category': ('transport_timeout' if isinstance(exc, TimeoutError)
                                       or isinstance(cause, TimeoutError) else 'transport_error'),
                    'exception_type': type(exc).__name__,
                    'cause_type': type(cause).__name__ if cause else None,
                    'trace_id': request_id or None, 'retry_after': None,
                }
            diagnostics.update(elapsed_seconds=time.perf_counter() - start,
                               partial_bytes=sum(map(len, chunks)), utc=utcnow(),
                               automatic_retry=False, usage_unknown=True)
            self.last_diagnostics = diagnostics
            save_json(folder / 'diagnostics.json', diagnostics, immutable=True)
            if chunks:
                (folder / 'partial_response.bin').write_bytes(b''.join(chunks))
            if isinstance(exc, HTTPError):
                failure = ProviderFailure('HTTP failure; see sanitized diagnostics')
                failure.diagnostics = diagnostics
                raise failure from None
            raise
        body = b''.join(chunks)
        envelope = response_envelope(body, seconds=time.perf_counter() - start,
                                     request_id=request_id, protocol='openai')
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(endpoint)
        envelope['endpoint'] = urlunsplit((parts.scheme, parts.hostname, parts.path, '', ''))
        persist(envelope)
        save_json(folder / 'transport.json', {
            'http_status': status, 'elapsed_seconds': envelope['seconds'],
            'response_bytes': len(body), 'timeout_at_start': timeout,
            'transport': 'direct HTTP using local OpenCode provider configuration',
            'stream': False, 'automatic_retry': False,
        }, immutable=True)
