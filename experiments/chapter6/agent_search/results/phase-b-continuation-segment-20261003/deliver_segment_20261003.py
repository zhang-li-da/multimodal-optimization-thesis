"""Offline audit and delivery of registered phase-B segments, without Test."""
import argparse
import copy
import hashlib
import json
import zipfile
from pathlib import Path
from collections import Counter
from chapter6_demo.v12_2.common import read_json, save_json, digest, file_sha
from chapter6_demo.v12_2.calls import decode_response
from experiments.chapter6.agent_search.minimal_mechanism import continuation_resume as base
from experiments.chapter6.agent_search.minimal_mechanism import continuation_segment as segment
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_runner import PhaseBState, history_summary


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def audit(out):
    m = read_json(out / 'manifest.json')
    assert digest({k: v for k, v in m.items() if k != 'manifest_sha256'}) == m['manifest_sha256']
    segment.verify_segment_inputs(out, m)
    for f in m['files']:
        assert file_sha(out / f['path']) == f['sha256']
        if f['path'].startswith('data/'):
            assert set(read_json(out / f['path'])) == {'block', 'profile', 'probe', 'validation'}
    rows, calls, selections = [], [], []
    for job in m['jobs']:
        run = out / 'runs' / job['job_id']
        term = read_json(run / 'terminal_status.json') if (run / 'terminal_status.json').exists() else {'status': 'not_started'}
        inherited = bool(term.get('parent_segment_read_only'))
        if inherited:
            run = out / 'parent_snapshot/runs' / job['job_id']
        cp = read_json(out / 'checkpoints' / (job['checkpoint_id'] + '.json'))
        result = read_json(run / 'search_result.json') if (run / 'search_result.json').exists() else {}
        saved = read_json(run / 'checkpoint.json') if (run / 'checkpoint.json').exists() else {}
        records = saved.get('records', [])
        local_calls = []
        for p in sorted(run.glob('calls/*/request.json')):
            req, state = read_json(p), read_json(p.with_name('state.json'))
            assert state['request_sha256'] == digest(req)
            assert req['provider'] == 'minimax-cn-coding-plan' and req['model'] == 'MiniMax-M3'
            resp = read_json(p.with_name('response.json')) if p.with_name('response.json').exists() else {}
            raw = read_json(p.with_name('raw_response.json')) if p.with_name('raw_response.json').exists() else {}
            if raw:
                assert raw['request_sha256'] == digest(req)
                assert raw['envelope_sha256'] == digest(raw['envelope'])
                decoded = decode_response(raw['envelope'])
                if resp:
                    assert resp == dict(decoded, request_sha256=digest(req))
                else:
                    resp = decoded
            if resp:
                assert resp['returned_model'] == 'MiniMax-M3'
            if state['status'] == 'prepared':
                continue
            row = dict(base.call_cost(req, resp), job_id=job['job_id'], inherited=inherited,
                       path=p.relative_to(out).as_posix(), state=state['status'],
                       raw_present=bool(raw), response_present=bool(resp))
            calls.append(row); local_calls.append(row)
        sel = read_json(run / 'selection_candidates.json') if (run / 'selection_candidates.json').exists() else {}
        if records:
            assert saved['config']['checkpoint_sha256'] == digest(cp)
            replay = PhaseBState(cp, job['strategy'], 8)
            for i, record in enumerate(records):
                decision = replay.choose(i)
                for key in ('step', 'strategy', 'action', 'parent', 'reference', 'target', 'allocation', 'evidence'):
                    assert decision[key] == record['decision'][key], (job['job_id'], i, key)
                assert replay.observe(copy.deepcopy(record['node']), record['costs']) == record['event']
                candidate = read_json(run / 'slots' / f'{i:03d}' / 'candidate.json')
                assert candidate['code'] == record['node']['code']
            # Older parent tasks persisted a result state digest that differs
            # from the last checkpoint serialization; replay below is the
            # authoritative decision/event check and this discrepancy is
            # reported explicitly in the row.
            for h in (4, 8):
                value = sel[str(h)]
                if len(records) < h:
                    assert value['status'] == 'missing_prefix'
                    continue
                best = min((n for n in cp['nodes'] + [r['node'] for r in records[:h]] if n['evaluation']['valid']),
                           key=lambda n: (n['evaluation']['loss'], n['id']))
                assert value['best_id'] == best['id'] and value['code'] == best['code']
                assert value['validation_loss'] == best['evaluation']['loss']
                selections.append({'job_id': job['job_id'], 'horizon': h, 'inherited': inherited,
                                   'code_sha256': sha(best['code'].encode()), 'validation_loss': value['validation_loss']})
        costs = base.sum_cost(local_calls)
        assert costs['requests'] <= 16 and costs['known_tokens'] <= 100000
        if result:
            assert result['summary']['completed_proposals'] == len(records)
            assert result['summary']['known_tokens'] == costs['known_tokens']
            assert result['selection_candidates_sha256'] == file_sha(run / 'selection_candidates.json')
        if term['status'] == 'continuation_complete':
            assert len(records) == 8 and len(sel) == 2
        if local_calls:
            assert (run / 'attempt_claim.json').exists()
        failures = Counter((r['node'].get('proposal_failure') or {}).get('kind') for r in records if r['node'].get('proposal_failure'))
        rows.append(dict(job_id=job['job_id'], strategy=job['strategy'], checkpoint_id=job['checkpoint_id'],
                         block=job['data_block'], repetition=job['repetition'], inherited=inherited,
                         status=term['status'], proposals=len(records), valid=sum(r['node']['evaluation']['valid'] for r in records),
                         failures=dict(failures), global_improvements=sum(r['event']['global_improvement'] for r in records),
                         project_progress=sum(r['event']['project_progress'] for r in records),
                         wall_seconds=term.get('wall_seconds'),
                         final_state_matches_last_checkpoint=(result.get('state_sha256')==digest(saved['state'])) if result and saved else None,
                         start_loss=(cp.get('incumbent') or {}).get('loss'),
                         selections={k: {a: b for a,b in v.items() if a != 'code'} for k,v in sel.items()}, **costs))
    lookup = {(r['checkpoint_id'], r['repetition'], r['strategy']): r for r in rows}
    pairs = []
    for r in rows:
        for a,b in [('B','I'),('EG','E0'),('E0','I'),('EG','I')]:
            if r['strategy'] != a:
                continue
            other = lookup[(r['checkpoint_id'], r['repetition'], b)]
            for h in ('4','8'):
                x,y = r['selections'].get(h,{}), other['selections'].get(h,{})
                if x.get('status') == y.get('status') == 'frozen_on_validation':
                    pairs.append({'comparison': a+'-'+b,'checkpoint_id':r['checkpoint_id'],'block':r['block'],
                                  'repetition':r['repetition'],'horizon':int(h),
                                  'validation_difference_pp':100*(x['validation_loss']-y['validation_loss'])})
    eg = []
    for f in sorted((out / 'checkpoints').glob('*.json')):
        cp = read_json(f)
        if cp['status'] != 'ready':
            continue
        summary = history_summary(cp['nodes'], cp['incumbent']['loss'])
        eg.append({'checkpoint_id':cp['checkpoint_id'],'source_nodes':len(cp['nodes']),
                   'included_node_ids':[n['node_id'] for n in summary['recorded_program_summaries']],
                   'included_intents':sum(bool(n['intent'].strip()) for n in summary['recorded_program_summaries']),
                   'included_hypotheses':sum(bool(n['strategy_hypothesis']) for n in summary['recorded_program_summaries']),
                   'failures_included':len(summary['observed_failures']), 'summary_sha256':digest(summary)})
    costs = base.ledger(out,m)
    current = [c for c in calls if not c['inherited']]
    assert base.sum_cost(calls)['requests'] + 83 == costs['requests']
    return {'schema':'chapter6-segment-delivery-audit-v1','manifest_sha256':m['manifest_sha256'],
            'source_commit':read_json(out/'EXECUTION_BINDING.json')['source_commit'],
            'new_cost':base.sum_cost(current),'registered_cost':base.sum_cost(calls),'cumulative_cost':costs,
            'new_proposals':sum(r['proposals'] for r in rows if not r['inherited']),
            'new_valid':sum(r['valid'] for r in rows if not r['inherited']),
            'registered_proposals':sum(r['proposals'] for r in rows),'registered_valid':sum(r['valid'] for r in rows),
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'new_status_counts':dict(Counter(r['status'] for r in rows if r['requests'] and not r['inherited'])),
            'rows':rows,'calls':calls,'selections':selections,'pairs':pairs,'eg_history':eg,
            'data_checkpoint_hashes_verified':len(m['files']), 'decision_event_replay_count':sum(r['proposals'] for r in rows),
            'test_access':False,'audit_new_model_calls':0,'numeric_reevaluations':0}


def main():
    p=argparse.ArgumentParser(); p.add_argument('--study',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); out=args.study; dest=args.output; dest.mkdir(parents=True,exist_ok=True)
    a=audit(out); save_json(dest/'ANALYSIS.json',a)
    for name in ['manifest.json','EXECUTION_BINDING.json','parent_manifest.json','parent_halt.json',
                 'parent_snapshot_manifest.json','halt.json','progress.json','SEGMENT_AUDIT.json']:
        if (out/name).exists():
            (dest/name).write_bytes((out/name).read_bytes())
    members=[]; archive=dest/'raw-segments.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(out.rglob('*')):
            if not path.is_file() or path.name=='.run.lock' or path.suffix=='.tmp':continue
            raw=path.read_bytes(); rel=path.relative_to(out).as_posix(); z.writestr(rel,raw)
            members.append({'path':rel,'bytes':len(raw),'sha256':sha(raw)})
        registry=Path(read_json(out/'manifest.json')['registry'])
        for path in sorted(registry.rglob('*.json')):
            raw=path.read_bytes(); rel='registry_snapshot/'+path.relative_to(registry).as_posix(); z.writestr(rel,raw)
            members.append({'path':rel,'bytes':len(raw),'sha256':sha(raw)})
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))==len(members)
        for r in members:assert sha(z.read(r['path']))==r['sha256']
    save_json(dest/'ARCHIVE_INDEX.json',{'archive':archive.name,'sha256':file_sha(archive),'bytes':archive.stat().st_size,'members':members})
    print(json.dumps({k:v for k,v in a.items() if k not in ['rows','calls','pairs','selections','eg_history']}))

if __name__=='__main__':main()
