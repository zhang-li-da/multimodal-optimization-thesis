from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path('C:/Users/67473/Desktop/5/_delivery/ch6-phase-b-review-20261001')
STUDY = Path('C:/Users/67473/Desktop/5/phase_b_recovery_20261002')
OUT = ROOT / 'experiments/chapter6/agent_search/results/phase-b-recovery-20261002'

def read(p):
    return json.loads(p.read_text(encoding='utf8'))

def sha(b):
    return hashlib.sha256(b).hexdigest()

def write(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes((json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+chr(10)).encode())

def main():
    a=read(STUDY/'PREFIX_AUDIT.json')
    m=read(STUDY/'manifest.json')
    OUT.mkdir(parents=True,exist_ok=True)
    jobs={r['block']:r for r in a['rows']}
    statuses=Counter(r['task_status'] for r in jobs.values())
    ready=sum(c['status']=='ready' for c in a['checkpoints'])
    branches=sum(c['branch_available'] for c in a['checkpoints'])
    cost=a['cost']
    new={k:cost[k]-m['prior_cost'][k] for k in cost}
    summaries=[]
    responses=[]
    diagnostics=[]
    for run in (STUDY/'prefix_runs').iterdir():
        for f in run.glob('calls/*/response.json'):
            response=read(f)
            responses.append({'job':run.name,'call':f.parent.name,**{k:response.get(k) for k in
                              ['returned_model','input_tokens','output_tokens','seconds','usage_complete']}})
        for f in run.glob('calls/*/diagnostics.json'):
            diagnostics.append({'job':run.name,'call':f.parent.name,**read(f)})
    write(OUT/'SERVICE_AUDIT.json',{'responses':responses,'diagnostics':diagnostics,
          'returned_models':dict(Counter(r['returned_model'] for r in responses)),
          'responses_exceeding_180_seconds':sum(r['seconds']>180 for r in responses),
          'longest_response_seconds':max(r['seconds'] for r in responses),
          'historical_error_cause_not_inferred':True})
    for row in a['rows']:
        cp=read(STUDY/'checkpoints'/f"b{row['block']}-step{row['checkpoint']:02d}.json")
        structure={}
        for role in ('incumbent','branch'):
            rec=cp.get(role)
            if not rec: continue
            node=next(n for n in cp['nodes'] if n['id']==rec['id'])
            structure[role]={'id':node['id'],'name':node.get('name'),'intent':node.get('intent'),
                             'tags':node.get('tags'), 'program_identity':node['evaluation'].get('program_identity')}
            p=OUT/'programs'/f"b{row['block']}-step{row['checkpoint']:02d}-{role}.py"
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(node['code'].encode())
        summaries.append({**row,'structure':structure})
    write(OUT/'CHECKPOINT_TABLE.json',summaries)
    write(OUT/'INTEGRITY_AUDIT.json',read(STUDY/'INTEGRITY_AUDIT.json'))
    write(OUT/'ANALYSIS.json',{**a,'task_status_counts':dict(statuses),'ready_checkpoints':ready,
                              'branch_available_checkpoints':branches,'new_cost':new,
                              'historical_cost':m['prior_cost'], 'search_proposals':sum(r['completed_proposals'] for r in jobs.values()),
                              'valid_search_programs':sum(r['valid_programs'] for r in jobs.values()),
                              'superseded_rerun_proposals_excluded':2})
    write(OUT/'RUN_MANIFEST.json',m)
    write(OUT/'CONTINUATION_REQUEST.json',a['continuation_request'])
    members=[]
    zip_path=OUT/'raw-prefix-records.zip'
    with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(STUDY.rglob('*')):
            if not p.is_file(): continue
            rel=p.relative_to(STUDY).as_posix()
            raw=p.read_bytes()
            z.writestr(rel,raw)
            members.append({'path':rel,'bytes':len(raw),'sha256':sha(raw)})
    write(OUT/'RAW_MEMBERS.json',{'archive':'raw-prefix-records.zip','archive_sha256':sha(zip_path.read_bytes()),
                                 'member_count':len(members),'members':members})
    lines=['# 阶段 B 公共前缀恢复与完整性审查','',
           '本轮执行真实公共前缀，尚未执行 I/B/E0/EG 续开发，也未开放 Test。数据不足或中断均保留；本报告不作组件有效性结论。','',
           f"执行源码：`{m['source_commit']}`；恢复 manifest：`{m['manifest_sha256']}`。",'',
           f"8 个区块任务终态：`{dict(statuses)}`。完整完成 5 个，基础设施中断 3 个，预算终止 0 个，未启动 0 个。研究轨迹累计完成 {sum(r['completed_proposals'] for r in jobs.values())} 个提案；"
           f"16 个计划检查点中 {ready} 个就绪，{branches} 个具有合格分支。",'',
           '恢复选择预先固定为最早批次。区块 65 沿用最早 16 个提案及中断记录，后来的 2 个重复尝试只计入基础设施成本，未替代原轨迹。其余区块按原顺序执行，未补抽。','',
           f"本次新增公共前缀提案 153 个；合并最早 16 个后为 169 个，其中有效 165 个、无效 4 个、截断 0 次。无效原因见 INTEGRITY_AUDIT.json。第 24 步缺失的区块为 65、66、67；全部 8 个区块第 8 步均可用。",'',
           '| 成本范围 | 请求 | 已知 tokens | 未知用量请求 | 锁定未知预留 |',
           '|---|---:|---:|---:|---:|',
           f"| 本次新增 | {new['requests']} | {new['known_tokens']} | {new['unknown_requests']} | {new['unknown_reservation']} |",
           f"| 含两次历史尝试与已记录服务验收 | {cost['requests']} | {cost['known_tokens']} | {cost['unknown_requests']} | {cost['unknown_reservation']} |",'',
           '未知预留用于防止突破预算，不能充当实际用量。全局上限 384 请求、2,000,000 tokens；没有额外新增服务验收。实际调用采用本地 OpenCode provider 配置的 MiniMax-M3，通过同一 HTTP 接口执行。','',
           '单请求超时改为至多 600 秒，并服从每区块剩余 3,600 秒 wall 上限；输出上限、提示、策略和选择规则不变。所有失败无重试，保存可脱敏的异常诊断。历史 180 秒中断缺少底层异常，不能事后认定其原因。','',
           f"研究轨迹中有 {sum(r['seconds']>180 for r in responses)} 个完整响应耗时超过 180 秒，最长 {max(r['seconds'] for r in responses):.2f} 秒，说明旧上限会截断部分本可完成的请求。新增服务错误明细见 SERVICE_AUDIT.json；HTTP 529 / overloaded_error 归为服务过载，不归为 HTTP 429 限流。",'',
           '| 区块 | 检查点 | 任务终态 | 完成提案 | 有效 | 截断 | incumbent loss | 分支 loss | loss 差 | probe 距离 |',
           '|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(v): return '缺失' if v is None else f'{v:.6f}'
    for r in summaries:
        lines.append(f"| {r['block']} | {r['checkpoint']} | {r['task_status']} | {r['completed_proposals']} | {r['valid_programs']} | {r['truncations']} | {fmt((r['incumbent'] or {}).get('loss'))} | {fmt((r['branch'] or {}).get('loss'))} | {fmt(r['branch_difference'].get('validation_loss_delta'))} | {fmt(r['branch_difference'].get('probe_behavior_distance'))} |")
    lines += ['', 'loss 为比例单位，乘 100 才是百分数。代码身份、策略意图、标签、逐次成本、失败和全部选择摘要见 CHECKPOINT_TABLE.json、ANALYSIS.json 及原始归档。行为距离只作操作性候选筛选，不等于真实局部最优盆地。','',
              'EG 历史输入审查：确定性摘要从当时可见节点中取 validation 最好的至多 5 个程序，并保留至多 5 个失败；质量汇总覆盖全部有效可见节点。摘要含意图、标签与质量，旧 SP 提示不要求显式 strategy_hypothesis，因此该字段可能全部为空。缺失字段不回填为新证据，不修改已冻结前缀。EG 摘要已接入 planner evidence；E0/EG 同用四字段假设 schema、相同模型及 priority 接口。后续若改善 EG 提示须重新版本冻结。','',
              '| 检查点 | 可见节点 | 摘要节点 | 非空意图 | 非空策略假设 | 失败记录 |',
              '|---|---|---|---:|---:|---:|']
    for h in a['histories']:
        lines.append(f"| {h['checkpoint']} | {h['visible_node_ids']} | {h['summary_node_ids']} | {h['intent_count']} | {h['hypothesis_count']} | {h['failure_count']} |")
    q=a['continuation_request']
    lines += ['',f"后续仍保留全部 128 个计划任务槽位，其中实际可执行 {q['executable_jobs']} 个；其余明确记为 preparation_incomplete 或 branch_unavailable。申请上限为 {q['max_proposals']} 提案、{q['max_requests']} 请求、{q['max_tokens']:,} tokens。每任务 8 提案，第 4/8 步冻结候选，每行动重复两次。",'',
              '条件分支比较只纳入已有合格分支的状态，并报告覆盖率。覆盖全部状态的策略必须保留缺失类别；若将不可用 B 定义为回退 I，必须事先冻结该策略并复用配对 I 结果，不能把它混称为分支处理。当前申请不静默执行任何回退。','',
              '续开发执行条件：13 个检查点具备相同前缀、合格分支、search 快照及 I/B/E0/EG 请求接线，能够形成 104 个实际任务。服务仍有间歇 529 过载，继续采用有界失败处理；显式假设字段缺失是 EG 解释限制。若保持现提示，应把干预解释为意图/标签/质量历史引导；若增加结构化假设提炼，则须在续开发前另作版本冻结。','',
              '续开发请求文件不构成批准文件。此前详细指令明确只授权公共前缀并排除续开发；本轮已提出范围确认，但尚未收到具体答复。因此未创建续开发批准文件，也未运行续开发。所有续开发终态及候选冻结审查完成前，Test 保持封闭。阶段 C 的收益尺度尚需修订，阶段 D 样本量尚待独立规划。公共前缀 validation 改善不能证明 H1–H3 或完整方法有效。','',
              '验证：本修订运行 36 项针对性离线测试，全部通过；不是重复报告旧阶段的 66/68 项计数。重放 169 步控制器、核验 193 个程序身份（含种子）、338 个原始响应与 usage，检查所有 16 个检查点终态，未重跑数值评价。原始 ZIP 的全部成员按 RAW_MEMBERS.json 核对；固定远端提交下载验证另附 REMOTE_VERIFICATION.json。结束时检查无遗留本轮模型进程。','']
    (OUT/'REPORT_ZH.md').write_bytes(chr(10).join(lines).encode())
    files={p.relative_to(OUT).as_posix():sha(p.read_bytes()) for p in OUT.rglob('*') if p.is_file() and p.name not in {'DELIVERY_HASHES.json','REMOTE_VERIFICATION.json'}}
    write(OUT/'DELIVERY_HASHES.json',{'files':files,'raw_member_count':len(members),'manifest_sha256':m['manifest_sha256']})
    print(json.dumps({'output':str(OUT),'archive_bytes':zip_path.stat().st_size,'members':len(members),'statuses':dict(statuses),'cost':cost}))

if __name__=='__main__': main()
