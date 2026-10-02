import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from chapter6_demo.v12_2.calls import DurableCalls, IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import read_json, save_json
from experiments.chapter6.agent_search.minimal_mechanism import recovery_transport as module
from experiments.chapter6.agent_search.minimal_mechanism.recovery_study import ledger, cumulative_cost, dispatch


def transport(tmp_path, monkeypatch):
    monkeypatch.setattr(module.HTTPTransport, '__init__', lambda self, *a: setattr(
        self, 'client', SimpleNamespace(protocol='openai', base_url='https://example.invalid/v1', _key='secret')))
    value = module.RecoveryTransport('minimax-cn-coding-plan', 'MiniMax-M3', tmp_path)
    value.set_wall_deadline(module.time.perf_counter() + 3600)
    return value


def calls(tmp_path, transport):
    return DurableCalls(tmp_path, {'provider': 'minimax-cn-coding-plan', 'model': 'MiniMax-M3',
                                   'parameters': {'temperature': .7}}, transport)


def test_timeout_records_real_cause_and_never_reposts_unknown(tmp_path, monkeypatch):
    t = transport(tmp_path, monkeypatch)
    sent = []
    def timeout(*a, **kw):
        sent.append(kw['timeout'])
        raise TimeoutError('do not save arbitrary error messages or secrets')
    monkeypatch.setattr(module, 'urlopen', timeout)
    c = calls(tmp_path, t)
    for _ in range(2):
        with pytest.raises(IndeterminateCall):
            c.complete(0, 'planner', 'system', 'prompt', 16384)
    assert sent == [600]
    d = read_json(tmp_path / 'calls/000-planner/diagnostics.json')
    assert d['exception_type'] == 'TimeoutError'
    assert d['error_category'] == 'transport_timeout'
    assert 'secret' not in json.dumps(d)
    assert ledger(tmp_path)['unknown_requests'] == 1
    assert ledger(tmp_path)['unknown_reservation'] > 16384


def test_business_quota_diagnostic_retained(tmp_path, monkeypatch):
    t = transport(tmp_path, monkeypatch)
    def quota(*a, **kw):
        raise HTTPError('https://example.invalid', 429, 'rate', {'Retry-After': '600'},
                        io.BytesIO(b'{"error":{"code":"2056"}}'))
    monkeypatch.setattr(module, 'urlopen', quota)
    with pytest.raises(ProviderFailure):
        calls(tmp_path, t).complete(0, 'planner', 's', 'p', 16)
    assert t.last_diagnostics['error_category'] == 'quota_exhausted'
    assert t.last_diagnostics['retry_after'] == '600'


def test_timeout_is_bounded_by_remaining_job_time(tmp_path, monkeypatch):
    t = transport(tmp_path, monkeypatch)
    t.set_wall_deadline(module.time.perf_counter() + 9)
    assert 0 < t.remaining() <= 9
    t.set_wall_deadline(module.time.perf_counter() - 1)
    with pytest.raises(TimeoutError):
        t.remaining()


def test_cumulative_cost_survives_restart_and_excludes_duplicate_copy(tmp_path):
    prior = {'requests': 45, 'known_tokens': 160122, 'unknown_requests': 2, 'unknown_reservation': 41000}
    manifest = {'prior_cost': prior, 'new_dispatch_order': ['prefix-sp-b63']}
    run = tmp_path / 'prefix_runs/prefix-sp-b63/calls/000-planner'
    save_json(run / 'request.json', {'system': 'a', 'prompt': 'b', 'max_tokens': 10})
    save_json(run / 'state.json', {'status': 'sent_unknown'})
    first = cumulative_cost(tmp_path, manifest)
    second = cumulative_cost(tmp_path, json.loads(json.dumps(manifest)))
    assert first == second
    assert first['requests'] == 46 and first['known_tokens'] == 160122
    assert first['unknown_requests'] == 3 and first['unknown_reservation'] == 41524


def test_partial_job_is_never_reinitialized(tmp_path, monkeypatch):
    from experiments.chapter6.agent_search.minimal_mechanism import recovery_study as study
    manifest = {'prefix_jobs': [{'job_id': 'p'}], 'preserved_terminal_jobs': []}
    save_json(tmp_path / 'prefix_runs/p/config.json', {'old': 'config'})
    monkeypatch.setattr(study, 'verify', lambda p: manifest)
    with pytest.raises(ValueError, match='Interrupted job'):
        dispatch(tmp_path)
