"""Read-only audit of nested continuation evidence, without model or Test access."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from chapter6_demo.v12_2.calls import decode_response
from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json, utcnow
from .continuation_resume import _run_call_audit, ledger, sum_cost
from .phase_b_runner import PhaseBState


def layers(root):
    """Yield actual evidence layers; copied status markers are not new attempts."""
    root = Path(root)
    while (root / 'manifest.json').exists():
        yield root
        root = root / 'parent_snapshot'


def evidence_run(root, job_id):
    for layer in layers(root):
        run = layer / 'runs' / job_id
        marker = run / 'terminal_status.json'
        if marker.exists() and read_json(marker).get('parent_segment_read_only'):
            continue
        if marker.exists() or (run / 'config.json').exists():
            return layer, run
    return Path(root), Path(root) / 'runs' / job_id


def verify_layers(root):
    checked = 0
    for layer in layers(root):
        manifest = read_json(layer / 'manifest.json')
        assert digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}) == manifest['manifest_sha256']
        for row in manifest['files']:
            path = (layer / row['path']).resolve()
            assert path.is_relative_to(layer.resolve())
            assert file_sha(path) == row['sha256']
            if row['path'].startswith('data/'):
                assert set(read_json(path)) == {'block', 'profile', 'probe', 'validation'}
            checked += 1
        if manifest.get('schema') != 'chapter6-registered-continuation-segment-v1':
            continue
        parent = layer / 'parent_snapshot'
        index = read_json(layer / 'parent_snapshot_manifest.json')
        assert digest({k: v for k, v in index.items() if k != 'snapshot_sha256'}) == manifest['parent_snapshot_sha256']
        names = [r['path'] for r in index['files']]
        assert len(names) == len(set(names))
        actual = {p.relative_to(parent).as_posix() for p in parent.rglob('*') if p.is_file()}
        # The published r2 archive intentionally excluded OS lock files.
        # Preserve this explicit exception; evidence files must match exactly.
        omitted_locks = {r['path'] for r in index['files']
                         if Path(r['path']).name == '.run.lock' and r['path'] not in actual
                         and (r['bytes'], r['sha256']) in {
                             (0, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
                             (1, '5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9')}}
        assert set(names) - omitted_locks == actual
        for row in index['files']:
            if row['path'] in omitted_locks:
                continue
            path = (parent / row['path']).resolve()
            assert path.is_relative_to(parent.resolve())
            assert path.stat().st_size == row['bytes'] and file_sha(path) == row['sha256']
        pm = read_json(parent / 'manifest.json')
        assert pm['manifest_sha256'] == manifest['parent_manifest_sha256']
        assert read_json(layer / 'parent_manifest.json') == pm
        assert read_json(parent / 'halt.json') == manifest['parent_halt'] == read_json(layer / 'parent_halt.json')
        assert ledger(parent, pm) == sum_cost(manifest['historical_attempts'])
    return checked


def audit(root):
    root = Path(root).resolve()
    checked = verify_layers(root)
    manifest = read_json(root / 'manifest.json')
    rows, calls, latencies = [], [], defaultdict(list)
    replayed = 0
    for job in manifest['jobs']:
        layer, run = evidence_run(root, job['job_id'])
        term = read_json(run / 'terminal_status.json') if (run / 'terminal_status.json').exists() else {}
        status = term.get('status', 'running' if (run / 'config.json').exists() else 'not_started')
        result = read_json(run / 'search_result.json') if (run / 'search_result.json').exists() else {}
        selections = read_json(run / 'selection_candidates.json') if (run / 'selection_candidates.json').exists() else {}
        cp = read_json(root / 'checkpoints' / (job['checkpoint_id'] + '.json'))
        saved = read_json(run / 'checkpoint.json') if (run / 'checkpoint.json').exists() else {}
        records = saved.get('records', [])
        costs = _run_call_audit(run)
        assert costs['parse_errors'] == 0
        # Only replay terminal records, whose source files cannot still change.
        changes = progress_cross_cell = followups = 0
        if term and records:
            assert saved['config']['checkpoint_sha256'] == digest(cp)
            replay = PhaseBState(cp, job['strategy'], 8)
            for i, record in enumerate(records):
                decision = replay.choose(i)
                for key in ('step', 'strategy', 'action', 'parent', 'reference', 'target', 'allocation', 'evidence'):
                    assert decision[key] == record['decision'][key], (job['job_id'], i, key)
                parent = replay.by_id.get(record['node'].get('parent_id'))
                event = replay.observe(copy.deepcopy(record['node']), record['costs'])
                assert event == record['event'], (job['job_id'], i, 'event')
                crossing = parent is not None and parent.get('behavior_cell_id') != event.get('behavior_cell_id')
                changes += bool(crossing)
                progress_cross_cell += bool(crossing and event['project_progress'])
                if event['project_progress'] and i + 1 < len(records):
                    followups += records[i + 1]['decision']['allocation']['project_best_id'] == record['node']['id']
            replayed += len(records)
            for horizon in (4, 8):
                selection = selections[str(horizon)]
                if len(records) < horizon:
                    assert selection['status'] == 'missing_prefix'
                    continue
                best = min((n for n in cp['nodes'] + [r['node'] for r in records[:horizon]] if n['evaluation']['valid']),
                           key=lambda n: (n['evaluation']['loss'], n['id']))
                assert (selection['best_id'], selection['code'], selection['validation_loss']) == (best['id'], best['code'], best['evaluation']['loss'])
        for request_path in sorted(run.glob('calls/*/request.json')):
            request, state = read_json(request_path), read_json(request_path.with_name('state.json'))
            assert state['request_sha256'] == digest(request)
            assert request['provider'] == 'minimax-cn-coding-plan' and request['model'] == 'MiniMax-M3'
            raw_path = request_path.with_name('raw_response.json')
            if raw_path.exists():
                raw = read_json(raw_path)
                assert raw['request_sha256'] == digest(request) and raw['envelope_sha256'] == digest(raw['envelope'])
                decoded = decode_response(raw['envelope'])
                assert decoded['returned_model'] == 'MiniMax-M3'
                if request_path.with_name('response.json').exists():
                    assert read_json(request_path.with_name('response.json')) == dict(decoded, request_sha256=digest(request))
            transport_path = request_path.with_name('transport.json')
            transport = read_json(transport_path) if transport_path.exists() else state.get('diagnostics') or {}
            if transport.get('http_status') == 200:
                latencies[request['stage']].append(transport['elapsed_seconds'])
            calls.append({'job_id': job['job_id'], 'layer': layer.relative_to(root).as_posix(),
                          'call': request_path.parent.name, 'state': state['status'],
                          'in_flight': not term and state['status'] == 'sent_unknown',
                          'transport': transport})
        if result:
            assert result['summary']['completed_proposals'] == len(records)
            assert result['summary']['known_tokens'] == costs['known_tokens']
            assert result['selection_candidates_sha256'] == file_sha(run / 'selection_candidates.json')
        if status == 'continuation_complete':
            assert len(records) == 8 and costs['unknown_requests'] == 0
        integer_key_match = reconciled_match = None
        pending_included = False
        if result and saved:
            # PhaseBState uses integer node IDs; JSON object keys become str.
            # Python's sort_keys orders those differently once IDs reach 10.
            restored = copy.deepcopy(saved['state'])
            restored['behavior_cell_id'] = {int(k): v for k, v in restored['behavior_cell_id'].items()}
            integer_key_match = result['state_sha256'] == digest(restored)
            pending = run / 'slots' / f'{len(records):03d}' / 'decision.json'
            if status != 'continuation_complete' and pending.exists():
                restored['decisions'].append(read_json(pending)['decision'])
                pending_included = True
            reconciled_match = result['state_sha256'] == digest(restored)
            assert reconciled_match, (job['job_id'], 'state hash after key/pending reconciliation')
        rows.append({'job_id': job['job_id'], 'strategy': job['strategy'], 'block': job['data_block'],
                     'checkpoint_id': job['checkpoint_id'], 'repetition': job['repetition'],
                     'evidence_layer': layer.relative_to(root).as_posix(), 'status': status,
                     'completed_proposals': len(records), 'valid_proposals': sum(r['node']['evaluation']['valid'] for r in records),
                     'global_improvements': sum(r['event']['global_improvement'] for r in records),
                     'project_progress': sum(r['event']['project_progress'] for r in records),
                     'cross_behavior_cell': changes, 'cross_cell_project_progress': progress_cross_cell,
                     'project_progress_followed_next_step': followups,
                     'selections': {k: {a: b for a, b in v.items() if a != 'code'} for k, v in selections.items()},
                     'serialized_state_hash_matches': result.get('state_sha256') == digest(saved['state']) if result and saved else None,
                     'integer_key_state_hash_matches': integer_key_match,
                     'pending_decision_included_for_hash': pending_included,
                     'reconciled_state_hash_matches': reconciled_match,
                     'wall_seconds': term.get('wall_seconds'), **costs})
    lookup = {(r['checkpoint_id'], r['repetition'], r['strategy']): r for r in rows}
    pairs = []
    for a, b in [('B', 'I'), ('EG', 'E0'), ('E0', 'I'), ('EG', 'I')]:
        for row in rows:
            if row['strategy'] != a:
                continue
            other = lookup[(row['checkpoint_id'], row['repetition'], b)]
            for horizon in ('4', '8'):
                x, y = row['selections'].get(horizon, {}), other['selections'].get(horizon, {})
                if x.get('status') == y.get('status') == 'frozen_on_validation':
                    pairs.append({'comparison': a + '-' + b, 'block': row['block'],
                                  'checkpoint_id': row['checkpoint_id'], 'repetition': row['repetition'],
                                  'horizon': int(horizon), 'validation_difference_pp': 100 * (x['validation_loss'] - y['validation_loss'])})
    counts = dict(Counter(r['status'] for r in rows))
    # Only the earliest layer's archive ledger lies outside the copied runs.
    oldest = list(layers(root))[-1]
    historical = read_json(oldest / 'manifest.json')['historical_attempts']
    archived_by_task = defaultdict(list)
    for entry in historical:
        archived_by_task[entry['job_id']].append(entry)
    for row in rows:
        extra = sum_cost(archived_by_task[row['job_id']])
        row['archived_cost'] = extra
        row['total_task_cost'] = {key: row[key] + extra[key] for key in extra}
    cumulative = ledger(root, manifest)
    assert sum_cost([r['total_task_cost'] for r in rows]) == cumulative
    in_flight = sum(c['in_flight'] for c in calls)
    return {'schema': 'chapter6-nested-segment-audit-v1', 'created_utc': utcnow(),
            'manifest_sha256': manifest['manifest_sha256'], 'status_counts': counts,
            'all_tasks_terminal': not any(r['status'] in {'not_started', 'running'} for r in rows),
            'halted': (root / 'halt.json').exists(), 'cost_including_history': cumulative,
            'in_flight_requests': in_flight,
            'terminal_unknown_requests': cumulative['unknown_requests'] - in_flight,
            'rows': rows, 'calls': calls, 'pairs': pairs,
            'verified_search_inputs_across_layers': checked, 'replayed_decisions': replayed,
            'latencies_seconds': {k: {'n': len(v), 'min': min(v), 'median': statistics.median(v), 'max': max(v)} for k, v in latencies.items()},
            'test_access': False, 'numeric_reevaluations': 0, 'new_model_calls': 0,
            'interpretation': 'Search validation only; incomplete pairs and unknown outcomes are not zero benefit.'}


def archive(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.is_relative_to(root):
        raise ValueError('delivery output must be outside the study')
    audit_result = audit(root)
    if not audit_result['halted'] and not audit_result['all_tasks_terminal']:
        raise ValueError('cannot archive an actively dispatching study')
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'ANALYSIS.json', audit_result)
    entries = []
    with zipfile.ZipFile(output / 'raw-segments.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.name == '.run.lock' or path.suffix == '.tmp':
                continue
            name = path.relative_to(root).as_posix()
            z.write(path, name)
            entries.append({'path': name, 'bytes': path.stat().st_size, 'sha256': file_sha(path)})
        registry = Path(read_json(root / 'manifest.json')['registry'])
        for path in sorted(registry.rglob('*.json')):
            name = 'registry_snapshot/' + path.relative_to(registry).as_posix()
            z.write(path, name)
            entries.append({'path': name, 'bytes': path.stat().st_size, 'sha256': file_sha(path)})
    save_json(output / 'ARCHIVE_INDEX.json', {'archive': 'raw-segments.zip',
              'sha256': file_sha(output / 'raw-segments.zip'), 'members': entries})
    with zipfile.ZipFile(output / 'raw-segments.zip') as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(set(z.namelist())) == len(entries)
        for row in entries:
            raw = z.read(row['path'])
            assert len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256']
    save_json(output / 'manifest.json', read_json(root / 'manifest.json'))
    binding = root / 'EXECUTION_BINDING.json'
    if binding.exists():
        save_json(output / binding.name, read_json(binding))
    (output / 'REPORT_ZH.md').write_text(report_zh(audit_result), encoding='utf-8')
    return audit_result


def report_zh(result):
    new = [r for r in result['rows'] if r['evidence_layer'] == '.' and r['requests']]
    costs = sum_cost(new)
    total = result['cost_including_history']
    lines = ['# 阶段 B 第三续接段结果', '',
             '本报告保留所有计划任务、历史失败和实际成本。模型为本机 OpenCode 配置的 MiniMax-M3；测试集未开放。', '',
             f"- Manifest：{result['manifest_sha256']}。",
             f"- 本段启动 {len(new)} 个任务，完成 {sum(r['status'] == 'continuation_complete' for r in new)} 条完整轨迹。",
             f"- 本段完成 {sum(r['completed_proposals'] for r in new)} 个提案，其中 {sum(r['valid_proposals'] for r in new)} 个有效。",
             f"- 本段 {costs['requests']:,} 次请求、{costs['known_tokens']:,} 已知 token、{costs['unknown_requests']} 个未知用量请求。",
             f"- 包含全部历史：{total['requests']:,} 次请求、{total['known_tokens']:,} 已知 token、{total['unknown_requests']} 个未知用量请求。未知用量另保守占账 {total['unknown_reservation']:,} token，不能解释为实测消耗。",
             f"- 状态计数：{json.dumps(result['status_counts'], ensure_ascii=False, sort_keys=True)}。",
             f"- 同检查点、同重复、同期限的完整配对数：{len(result['pairs'])}（4/8 步分别计数）。", '',
             '| 本段任务 | 状态 | 提案／有效 | 项目进步 | 全局 validation 改善 | 已知 token |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for r in new:
        lines.append(f"| {r['job_id']} | {r['status']} | {r['completed_proposals']}/{r['valid_proposals']} | {r['project_progress']} | {r['global_improvements']} | {r['known_tokens']:,} |")
    lines += ['',
              f"审计重放了 {result['replayed_decisions']} 个终态提案，核验各层共 {result['verified_search_inputs_across_layers']} 份搜索输入，检查请求、响应、冻结候选和逐任务成本。审计本身新增模型调用和数值重评均为 0。", '',
              '内存中的整数行为编号写入 JSON 后变为字符串键，可能导致排序和原始状态摘要不同。报告分别保留序列化摘要比较和恢复整数键后的比较，不改写旧记录。', '',
              '项目内改善与最终全局改善分别计数。固定续开发中跨行为类别保持项目连续性，不等于自然搜索中的续期有效；历史回放也不产生替代轨迹。', '',
              '未启动、前置检查点缺失、未知调用和未成熟轨迹都不能按零收益处理。当前报告只含搜索期 validation 过程，不能证明 B 优于 I、EG 优于 E0、保护或反馈有效。完整方法的自然搜索消融、独立确认和适用范围验证仍未完成。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', action='store_true')
    args = parser.parse_args()
    if args.archive:
        result = archive(args.study, args.output)
    else:
        result = audit(args.study)
        save_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k not in {'rows', 'calls', 'pairs'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
