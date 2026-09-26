"""All-run S1 technical report renderer (offline, not a new protocol)."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import statistics
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import read_json,save_json,file_sha

LABELS={'niche_fixed_dev':'FIFO 固定开发','relational_branch':'R 组合排序'}
NL=chr(10)

def rows(path):
    with Path(path).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))

def num(row,key,default=None):
    v=row.get(key)
    return float(v) if v not in (None,'','None') else default

def yes(row,key):return row.get(key) in (True,'True','true')

def fmt(v,n=3):return '缺失' if v is None else f'{v:.{n}f}'

def table(head,values):
    def cell(v):return str(v).replace('|','/').replace(NL,' ')
    return NL.join(['| '+' | '.join(map(cell,head))+' |','| '+' | '.join('---' for _ in head)+' |']+
                   ['| '+' | '.join(map(cell,r))+' |' for r in values])

def build(study,audit,output):
    study,audit,output=Path(study),Path(audit),Path(output)
    m=read_json(study/'manifest.json');s=read_json(audit/'audit.json')
    assert m['manifest_sha256']==s['manifest_sha256']
    js=rows(audit/'run_table.csv');es=rows(audit/'decisions.csv');cs=rows(audit/'calls.csv')
    gs=rows(audit/'grants.csv');ds=rows(audit/'directions.csv')
    complete=sum(j['status']=='search_complete_test_not_run' for j in js)
    tested=sum(num(j,'test_gap') is not None for j in js)
    if complete!=tested:raise ValueError('Complete all frozen test readouts first')
    quality=[];mechanism=[];grants=[];scale=[];structures=[];outputs=[]
    for g in s['group_results']:
        c=g['controller'];label=LABELS[c];jobs=[j for j in js if j['controller']==c]
        seed=[num(j,'seed_test_gap') for j in jobs if num(j,'seed_test_gap') is not None]
        seed=statistics.mean(seed) if seed else None;mean=g['test_gap']['mean']
        quality.append([label,g['test_gap']['n'],fmt(None if mean is None else 100*mean),
            fmt(None if seed is None else 100*seed),fmt(None if seed is None or mean is None else 100*(seed-mean)),
            f"{g['known_tokens']:,}",fmt(sum(num(j,'api_seconds',0) for j in jobs)/60,2)])
        mechanism.append([label]+[g[k] for k in ('completed_proposals','valid_candidates','admissions',
            'development_attempts','multi_branch_slots','differentiated_family_slots',
            'full_vs_fifo_choice_differences','full_vs_gain_choice_differences',
            'parent_improving_dev_children','global_improving_dev_children')])
        gg=[r for r in gs if r['controller']==c]
        grants.append([label,len(gg),sum(num(r,'attempts_received',0)>0 for r in gg),
            sum(num(r,'attempts_received',0)>=num(r,'initial_grant',0) for r in gg),
            sum(num(r,'unused_at_eviction',0)>0 for r in gg),sum(num(r,'unused_at_horizon',0)>0 for r in gg),
            sum(not yes(r,'admission_improved_global') for r in gg),
            sum(not yes(r,'admission_improved_global') and yes(r,'final_best_parent_ancestor') for r in gg)])
        ee=[r for r in es if r['controller']==c and yes(r,'multi_branch')]
        ratio=[num(r,'family_q_span',0)/num(r,'weighted_gain_span') for r in ee if num(r,'weighted_gain_span',0)>0]
        scale.append([label,len(ee),sum(yes(r,'family_scale_exceeds_gain_scale') for r in ee),
            fmt(statistics.median(ratio) if ratio else None,2),sum(num(r,'family_fallback_count',0)>0 for r in ee)])
        for role in ('planner','coder'):
            cc=[r for r in cs if r['controller']==c and r['stage']==role and yes(r,'dispatched')]
            outputs.append([label,role,len(cc),sum(r['finish_reason']=='stop' for r in cc),
                sum(r['finish_reason']=='length' for r in cc),sum(yes(r,'s0_complete') for r in cc),
                sum(yes(r,'s0_schema_valid') for r in cc),sum(yes(r,'s0_valid') for r in cc)])
    for job in js:
        dd=[d for d in ds if d['job_id']==job['job_id']];valid=[d for d in dd if d['behavior_hash']]
        structures.append([job['job_id'],len(valid),len({d['direction_id'] for d in valid if d['direction_id']!='unknown'}),
            len({d['behavior_hash'] for d in valid}),len({d['lineage_id'] for d in valid}),sum(d['direction_id']=='unknown' for d in dd)])
    pair_table=[[p['block'],p['seed'],fmt(None if p['fifo_test_gap'] is None else 100*p['fifo_test_gap']),
        fmt(None if p['ranked_test_gap'] is None else 100*p['ranked_test_gap']),fmt(p['delta_pp'],4)] for p in s['pairs']]
    job_table=[[j['job_id'],j['status'],j['completed_proposals'],j['valid_candidates'],j['multi_branch_slots'],
        fmt(None if num(j,'test_gap') is None else 100*num(j,'test_gap')),j['known_tokens'],j['usage_complete']] for j in js]
    deltas=[p['delta_pp'] for p in s['pairs'] if p['delta_pp'] is not None]
    wins=sum(d< -1e-12 for d in deltas);ties=sum(abs(d)<=1e-12 for d in deltas);losses=len(deltas)-wins-ties
    gate=all(v for k,v in s['observability_gates'].items() if k!='diagnostic_only_not_quality_pass')
    ci=s['block_bootstrap_95_descriptive'];ci='未计算' if ci is None else f'[{ci[0]:+.4f}, {ci[1]:+.4f}]'
    effect=('平均方向有利于R，但不能据小样本认定稳定优势' if s['delta_mean_pp'] is not None and s['delta_mean_pp']<0
            else '本批平均方向不支持R优于FIFO')
    sections=[f'''# S1 MiniMax M3 自然分支竞争实验：完整技术报告

## 1. 核心判断与实际范围

计划 **12次真实搜索（两控制器×三新区块×两搜索种子），每次32提案**；完成{complete}/12，独立test读出{tested}/12。所有计划任务均列出，未用成功补位、筛选入池运行或删去负结果。模型是本机OpenCode中的 **minimax-cn-coding-plan / MiniMax-M3**。

- 自然竞争的三项诊断目标：**{'达到' if gate else '未全部达到'}**。它检验机制是否有实际选择机会，不是质量通过线。
- 以三块内两种子配对先均值、再区块等权汇总：**R−FIFO={fmt(s['delta_mean_pp'],4)}个百分点**，负值有利于R；六个种子配对为R {wins}胜、{ties}平、{losses}负。{effect}。
- 这是**同提案上限**，不是同token/墙钟实验；R仍为旧标签统计组合排序，不是新反馈探索—开发策略。
- 可以写第六章的问题、架构、输出校准、机制可观测性和失效分析；**S2五策略比较、保护/反馈消融、跨任务确认尚未执行**，不据此宣称博士章节有效性已经完成。最终可以只输出一个最佳方案，多算法集成不再是必需门槛。

## 2. 源码、冻结与数据

S1源码提交：{m['source_commit']}；冻结提交：f32f1b7。

manifest SHA-256：{m['manifest_sha256']}。

基于PR #4 / 860eb5f的分阶段设计。本批仍沿用V121SearchState，运行期间未修改控制器、提示、程序语言或评价器。离线复核检查所有依赖源码、协议、实例哈希，不只依靠启动器的局部校验。

TSP14，块11/12/13，每块12 probe、36 validation、60 test，真实坐标归档。同块由两策略/两种子共享。搜索seed为100×block+label，不是模型服务确定性seed。与历史块3–10做ID/精确坐标哈希去重，不宣称几何等价证明或排除预训练污染。

仅编辑受限priority(f)；公共构造和24次2-opt检查相同。14城市参考为原基准Held–Karp最优值；未将精确入口用于50/100/200城市。planner/coder上限16384/8192，temperature=0.7，单请求timeout=180秒。服务别名无不可变版本信息。

串行12任务，最多384提案/768请求；planner解析失败跳coder但扣提案，未知/服务错误不重试、不补位。S0 r3/S1先本地冻结后推送，受GitHub网络故障影响，不称调用前公开远端预注册。

输出按validation冻结；所有任务terminal后独立进程执行test。启动verify曾读取test原始字节核hash，所以只能声称**评分和数据流隔离**，不能声称OS权限隔离或从未打开test。生成程序无文件I/O，模型与搜索评价器只收到probe/validation。

## 3. 当前架构与公平对照

共同初始规则 → 共同普通niche调度 / 奇数步B开发 → 相同planner → MiniMax计划 → 相同coder → 受限程序执行 → A/B/M更新 → validation最佳单程序冻结 → 独立test。

- **A**：质量约束行为代表，为普通调度保留候选。
- **B**：容量3，每个入池节点两次机会；非观测重现且竞争性父代改进可入池。FIFO按创建顺序直到额度耗尽；R按 q(tag)+0.25×gain/(1+attempts) 排序。
- **M**：记录实际事件；q为Beta平滑标签改进频率，标签观测少于2时用共享统计，不是校准的未来成功概率。
- **W**：清空，不提供父代/参考。内层轨迹仍保存，但不代表外层语义方向。
- 两组仅B选择不同；相同历史下普通调度、随机数消耗和提示序列化相同。因真实模型生成/分支选择导致历史不同，后续动作自然可以不同。
- **旧B不是新方向保护器**：容量满仍可能淘汰未用完额度的节点；没有承诺每个节点一定被执行两次，也没有方向累计8次的限制。下面的额度账本明确统计这些情况。

## 4. 质量、成本与全部配对
''',table(['组','有test的运行数','平均test gap/%','种子单规则gap/%','较种子改善/百分点','已知tokens','API延迟合计/分钟'],quality),f'''
种子规则按validation选择，不按test选择；不可把初始规则称为发现的新算法。API延迟不是专用机器端到端walltime。tokens来自服务input+output usage，不等于coding plan货币收费。

### 全部六个配对
''',table(['区块','seed标签','FIFO gap/%','R gap/%','R−FIFO/百分点'],pair_table),f'''
三块均值差：{s['block_mean_deltas_pp']}个百分点。以区块为单位重采样20,000次的**描述性**95% bootstrap区间：{ci}个百分点，固定种子9260101。只有三个外层单位，精度有限；不作确认性显著结论。实例、候选、调用不是独立重复，六个种子也不是六个独立数据块。

### 全部计划任务（含失败/不完整）
''',table(['任务','搜索状态','提案','有效候选','多分支时隙','test gap/%','已知tokens','usage完整'],job_table),'''
search_complete_test_not_run是冻结搜索的原始状态名；test结果另存tests目录，不回写搜索状态，不表示漏跑测试。

## 5. 输出完整性与全部调用成本
''',table(['组','阶段','派发','stop','length','S0口径完整','schema合法','S0口径有效'],outputs),f'''
运行器沿用旧宽松JSON解析；S0口径完整/schema/coder四组人工特征检查为**事后诊断**，不追溯改候选保留或实际搜索。完成输出、schema、人工特征可执行、真实实例可执行是不同指标。

本批{s['total_calls']}次真实调用，已知tokens {s['total_known_tokens']:,}，全批usage完整：{s['all_usage_complete']}。校准的失败和事故成本另列：

| 批次 | 请求 | 已知tokens | 结论 |
| --- | ---: | ---: | --- |
| S0 r1 | 1 | 781 | 响应落盘后工程异常中止，未混入r2 |
| S0 r2 | 84 | 241,695 | planner独立验收14/18，未通过 |
| S0 r3 | 48 | 170,724 | planner/coder18/18，E2E6/6，通过工程门槛 |
| S0小计 | 133 | 413,200 | 三批分开保留 |
| S1 | {s['total_calls']} | {s['total_known_tokens']:,} | 自然竞争筛查 |
| 本轮S0+S1 | {133+s['total_calls']} | {413200+s['total_known_tokens']:,} | 不含更早v1.x |

## 6. 机制机会链、额度与评分尺度
''',table(['组','提案','有效','入池','续开发','多分支','族分数不同','同历史R/F不同','同历史R/G不同','开发改善父代','开发改善全局'],mechanism),f'''
R/F和R/G差异在每个保存的真实历史上比较选择，不生成反事实后代；只能证明机制改变动作的机会，不能用同一个观测子代推断另一选择的收益。全部{s['replayed_decisions']}步决策重放，普通步骤F/R差异{s['ordinary_step_FR_differences']}。

### 所有入池节点的额度兑现
''',table(['组','入池','开发过','额度用完','未用完即淘汰','结束仍有额度','入池未胜全局','后者且为最终最佳父系祖先'],grants),'''
audit/grants.csv逐节点保存第一次开发、有效/改进次数、淘汰与剩余额度。尾部仍有机会是观察窗口有限，不应一律当无效；容量淘汰尚有额度则是旧B保护不足。父系祖先忽略跨分支引用，属于事后关联，不替代保护开/关实验。

### 分数尺度检查
''',table(['组','多分支时隙','q跨度大于加权gain跨度','非零gain跨度的q/gain中位比','有共享回退的时隙'],scale),'''
比较同一候选集内max(q)−min(q)和加权gain的跨度。尺度主导不自动等于算法错误，要结合实际选择及收益；未根据本批结果修改0.25系数。

## 7. 方向、行为和谱系分开描述
''',table(['任务','有效程序含种子','粗结构签名数','probe行为哈希数','父系根数','unknown程序数'],structures),'''
结构签名由实际特征、算子、调用和条件数量决定，变量改名不建立新方向，不看test/未来成功。它是**后验粗粒度描述**，不参与S1选择；不同签名不是不同语义模式的证明，相同签名也非语义等价。未完成独立盲评，不能把B条目、标签、签名数当真实优化盆地数。

## 8. 完整复核与回放
''',f'''
- 请求/原始response envelope/解码文本/候选/slot逐项对应，planner与coder提示均按冻结逻辑重建，不只检查planner。
- 重放控制器、父代/参考、B选择、额度、事件、RNG，核验checkpoint与readout哈希。
- 重新执行{s['numerical_evaluations']}次程序级评价，包含所有seed/候选search评价及readout test；通过：{s['numerical_passed']}，最大绝对差：{s['max_abs_numerical_error']}。一次search评价含整套probe/validation，不把其中每实例计为独立搜索。
- loss浮点atol=1e-12, rtol=1e-10；离散路线、行为及身份精确一致；执行时间不作相等要求。本次在当前Windows环境重执行，不声称跨平台皆已通过。
- [配对与机制图](demo/figures/paired_quality_and_exposure.png)，[离线HTML回放](demo/index.html)，下载后本地打开。默认manifest第一条，保留全部12任务，不挑最好案例。HTML不执行候选程序、不调用API。
- 原始归档raw-study.zip及逐成员raw-study.index.json；审计和报告生成新增模型调用0。

## 9. 缺陷与下一步验收

1. 本批仅TSP14/P空间、一个模型、两个旧排序器，非五策略效果、模块算法发现或跨任务证明。
2. 缺总token/总walltime硬上限；180秒只是单请求timeout。按同32提案解释，不追溯补预算。
3. 启动verify未完整比较manifest.source，已离线補核；test字节hash访问和dispatcher恢复时间戳冲突需下版修复，不改写冻结代码。
4. 旧节点B不保证兑现两次机会或方向累计额度；新方向保护器仅离线草案，不能写作已验证机制。
5. q标签未校准且可能尺度主导；未加仅收益排序的真实归因组，不能宣称族信息独立有效。
6. validation经32步反复利用，独立test虽隔离但只有三块。跨版本结果不作因果比较，不看测试继续挑参数。
7. 五策略合成测试、TSP50/100/200的54次无API profiling和预算工具都是工程准备。未核验隔离执行环境/MLE数据及原数学15任务验证器，不能称MLEvolve/MLE-Bench已经复现。

后续按新协议完成：五策略在线接口、额度/预算/恢复/隔离 → 新开发块SP/WR/FB/TS/AD（主比较AD−FB，关键AD−TS）→ 保护×反馈消融 → 独立跨任务确认。若反馈没有增量，应保留简单方法并收缩创新主张，不自动扩量寻求正结果。

## 10. 获取、版本与离线复现

检出本批结果标签/PR #5分支；旧r1/r2/r3和v1.x标签不移动，不自动合并PR。用chapter6_demo.v12_3.package unpack展开ZIP到新副本，再用chapter6_demo.agent_search.review_s1 --numeric复核；无需配置模型凭据。精确命令见REPRODUCE.md，发布信息见PUBLICATION.json。

**不要为了看报告或复现审计运行search-all --live；它是真实计费入口，不是日志回放。**
''']
    output.mkdir(parents=True,exist_ok=True);path=output/'REPORT_ZH.md'
    path.write_text((NL+NL).join(sections),encoding='utf-8',newline=NL)
    metrics={'manifest_sha256':m['manifest_sha256'],'completed_searches':complete,'tested_searches':tested,
        'observability_diagnostic_passed':gate,'paired_R_wins_ties_losses':[wins,ties,losses],
        'total_S0_S1_calls':133+s['total_calls'],'total_S0_S1_known_tokens':413200+s['total_known_tokens'],
        'report_sha256':file_sha(path),'new_model_calls_by_report':0,'claims':'S0/S1 only; S2-S6 not executed'}
    save_json(output/'report_metrics.json',metrics,immutable=True)
    return metrics

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('study','audit','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args()
    with offline_only():print(build(a.study,a.audit,a.output))

if __name__=='__main__':main()

