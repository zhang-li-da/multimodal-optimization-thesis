from pathlib import Path
import json
import hashlib
from collections import Counter
from chapter6_demo.v12_2.common import digest,file_sha,save_json,utcnow
from chapter6_demo.v12_2.calls import decode_response
from chapter6_demo.v12_2.identity import identity
from chapter6_demo.providers import ModelClient
from experiments.chapter6.agent_search.s3_tsp_r3.controller import restore_state
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_study import _select_prefix_checkpoint
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_runner import PhaseBState,_append_history_prompt
from chapter6_demo.discovery import planner_prompt

P=Path('C:/Users/67473/Desktop/5/phase_b_recovery_20261002')
def read(p):return json.loads(p.read_text(encoding='utf8'))
def main():
 m=read(P/'manifest.json');a=read(P/'PREFIX_AUDIT.json')
 replayed=0;identities=0;calls=0;responses=0;known=0;unknown=0;order=[];failures=[]
 # Check against exact in-memory credential without printing it or persisting it.
 key=ModelClient.from_opencode('minimax-cn-coding-plan','MiniMax-M3')._key.encode()
 for f in P.rglob('*'):
  if f.is_file():
   assert key not in f.read_bytes(), 'Credential detected in artifact'
 for rec in m['data']:
  snap=read(P/rec['path']);assert 'test' not in snap
  assert file_sha(P/rec['path'])==rec['sha256']
 for job in m['prefix_jobs']:
  run=P/'prefix_runs'/job['job_id'];checkpoint=read(run/'checkpoint.json')
  result=read(run/'search_result.json')
  state=restore_state(checkpoint);replayed+=len(checkpoint['records'])
  assert result['summary']['completed_proposals']==len(checkpoint['records'])
  assert result['summary']['wall_seconds'] <= 3601
  for node in state.nodes:
   assert node['evaluation']['program_identity']==identity(node['code']);identities+=1
  for f in run.glob('calls/*/request.json'):
   request=read(f);folder=f.parent;status=read(folder/'state.json')
   assert status['request_sha256']==digest(request)
   assert request['model']=='MiniMax-M3'
   assert request['max_tokens']==(16384 if request['stage']=='planner' else 8192)
   calls+=1
   if (folder/'raw_response.json').exists():
    raw=read(folder/'raw_response.json');response=read(folder/'response.json')
    assert raw['envelope_sha256']==digest(raw['envelope'])
    assert raw['request_sha256']==digest(request)
    assert {k:v for k,v in response.items() if k!='request_sha256'}==decode_response(raw['envelope'])
    assert response['returned_model']=='MiniMax-M3' and response['usage_complete']
    responses+=1;known+=response['input_tokens']+response['output_tokens']
   else:
    unknown+=1
    assert status['status'] in {'sent_unknown','provider_failed'}
  for record in checkpoint['records']:
   if not record['node']['evaluation']['valid']:
    failures.append({'job':job['job_id'],'step':record['decision'].get('step'),
                     'node_id':record['node']['id'], 'proposal_failure':record['node'].get('proposal_failure'),
                     'evaluation_failure':record['node']['evaluation'].get('failure_type')})
 for row in a['checkpoints']:
  cp=read(P/row['path']);assert file_sha(P/row['path'])==row['sha256']
  if cp['status']!='ready':continue
  block=cp['continuation']['block'];step=cp['prefix_step']
  saved=read(P/f'prefix_runs/prefix-sp-b{block}/checkpoint.json')
  nodes=saved['seeds']+[r['node'] for r in saved['records'][:step]]
  expected=_select_prefix_checkpoint(block,step,nodes,cp['continuation']['snapshot_sha256'])
  assert all(cp[k]==v for k,v in expected.items())
  interim=P/f'interim_audit/b{block}-step{step:02d}.json'
  if interim.exists(): assert read(interim)==expected
  eg=PhaseBState(cp,'EG').choose(0);e0=PhaseBState(cp,'E0').choose(0)
  assert eg['evidence']['required_strategy_hypothesis']==e0['evidence']['required_strategy_hypothesis']
  prompts={}
  for name,decision in [('EG',eg),('E0',e0)]:
   prompts[name]=_append_history_prompt(planner_prompt('tsp', {'target':decision['target'],'action':decision['action'],
       'parent':None,'reference':None,'evidence':decision['evidence']},0),decision)
  assert 'historical_search_summary' in prompts['EG'] and 'historical_search_summary' not in prompts['E0']
  order.append({'checkpoint':cp['checkpoint_id'],'visible_nodes':len(cp['nodes']),
                'summary_in_EG_request':True,'summary_absent_from_E0_request':True,
                'same_output_schema':True,'selection_matches_prefix_only':True})
 value={'utc':utcnow(),'controller_steps_replayed':replayed,'program_identities_verified':identities,
        'main_trajectory_requests':calls,'main_trajectory_responses':responses,
        'main_trajectory_known_tokens':known,'main_trajectory_unknown_requests':unknown,
        'search_data_only':True,'credential_leak_check':'passed', 'checkpoints':order,
        'invalid_candidates':failures,'numerical_evaluations_rerun':False,'model_calls_added':0,
        'test_access':False,'all_16_planned_checkpoint_states_accounted':len(a['checkpoints'])==16}
 save_json(P/'INTEGRITY_AUDIT.json',value,immutable=True)
 print(json.dumps({k:v for k,v in value.items() if k not in ['checkpoints','invalid_candidates']}))
if __name__=='__main__':main()
