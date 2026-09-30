"""Build a standalone, network-free viewer of all S3 r3 real search traces."""
import argparse
import csv
import json
from pathlib import Path

from chapter6_demo.v12_2.common import read_json


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>S3 r3 · 第六章真实搜索回放</title>
<style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#182c43;background:#f4f6f9}body{max-width:1280px;margin:auto;padding:28px}h1{font-size:25px;margin:8px 0}h2{font-size:18px;margin:16px 0 10px}.note{line-height:1.65;color:#45596b}.tag{color:#775d18;background:#fff5d7;border-radius:6px;padding:6px 10px;display:inline-block;font-size:13px}.card{background:white;border:1px solid #dce4ec;border-radius:10px;padding:18px;margin:14px 0}.controls{display:flex;gap:14px;flex-wrap:wrap;align-items:center}select,button{font:inherit;padding:7px;border:1px solid #b9c6d1;border-radius:5px;background:#fff}input{flex:1;min-width:160px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{padding:12px;background:#f4f7fb;border-radius:6px}.metric b{display:block;font-size:20px;margin-top:6px}.metric span{font-size:12px;color:#5a6b7c}svg{width:100%;height:225px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px;text-align:left;border-bottom:1px solid #e2e8f0}th{color:#53657a}pre{margin:0;max-height:300px;overflow:auto;font-size:13px;line-height:1.6;white-space:pre-wrap;background:#16273b;color:#e9f2f7;padding:14px;border-radius:6px}.small{font-size:12px;color:#53657a;line-height:1.6}.flow{color:#14637d;font-weight:600;margin-bottom:10px}.empty{color:#8291a3}a{color:#196f99}footer{line-height:1.8;color:#52667c;margin:24px 0;font-size:13px}@media(max-width:850px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}body{padding:14px}}
</style>
<div class="tag">MiniMax M3 · S3 r3 · 全部 48 次真实搜索历史</div>
<h1>探索方向、授予机会、实际开发</h1>
<p class="note">此页面回放冻结实验日志，浏览过程不调用模型。每组 8 个数据块、每次 32 提案；最终输出由 validation 选定。<strong>本轮三个主要比较均未达到预注册筛查标准，不能据单条轨迹宣称方法有效。</strong></p>
<div class="card"><h2>完整结果</h2><div id="aggregate"></div><p class="small">Gap 是 TSP14 相对精确最优值的平均差距，越低越好。平均值不能替代区块配对不确定性；详细对比见随附技术报告。</p></div>
<div class="card"><div class="controls"><label>搜索记录 <select id="run"></select></label><button id="prev">上一步</button><input id="step" type="range" min="0" max="31" value="0"><button id="next">下一步</button><strong id="index"></strong></div><p class="small" id="identity"></p><div class="metrics" id="metrics"></div></div>
<div class="grid"><div class="card"><h2>Validation 搜索轨迹</h2><svg id="chart" viewBox="0 0 560 220" role="img" aria-label="验证集历史最好质量"></svg><p class="small">横轴：已完成提案数；纵轴：历史最好 validation gap（%）。曲线仅显示已经发生的搜索。</p></div><div class="card"><h2>当前动作</h2><div class="flow" id="flow"></div><table id="event"></table><p class="small" id="counter"></p></div></div>
<div class="grid"><div class="card"><h2>开发池 B：动作前</h2><div id="before"></div></div><div class="card"><h2>开发池 B：动作后</h2><div id="after"></div></div></div>
<div class="card"><h2>生成的真实程序</h2><pre id="code"></pre></div>
<footer>搜索源码：51e5d5e；冻结协议：62cf707；数据块 32–39。P 表示最低开发机会保障，U 表示使用相同开发池但不强制优先。谱系/方向 ID 是有限 probe 下的操作性标识，不是经证实的真实模式。<br>本页包含全部记录，可切换查看失败提案与无进展步骤。r1/r2 工程失败单独归档，未混入此结果。离线回放不等于新的搜索实验；“同状态关闭优先”只重算下一动作，没有生成替代子代。</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
'use strict';const data=JSON.parse(document.getElementById('data').textContent),el=id=>document.getElementById(id);
const pct=x=>x==null?'无有效值':(100*x).toFixed(3)+'%',num=x=>x==null?'—':String(x);
function table(headers,rows){const t=document.createElement('table'),tr=document.createElement('tr');headers.forEach(h=>{const q=document.createElement('th');q.textContent=h;tr.append(q)});t.append(tr);rows.forEach(row=>{const tr=document.createElement('tr');row.forEach(v=>{const q=document.createElement('td');q.textContent=num(v);tr.append(q)});t.append(tr)});return t}
el('aggregate').append(table(['策略','搜索数','Test gap','平均 tokens','有效提案'],Object.entries(data.summary.group).map(([a,r])=>[a,r.tested_jobs,pct(r.mean_test_gap),Math.round(r.mean_tokens).toLocaleString(),r.valid_proposals+'/'+r.proposal_slots])));
data.runs.sort((a,b)=>a.job.data_block-b.job.data_block||a.job.arm_id.localeCompare(b.job.arm_id));
data.runs.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.job.job_id;el('run').append(o)});
function pool(id,entries){const d=el(id);d.replaceChildren();if(!entries.length){d.textContent='空';d.classList.add('empty');return}d.classList.remove('empty');d.append(table(['方向 / 账本','父程序','剩余资格','保护额度','gap'],entries.map(e=>[e.direction_id+' / '+e.lineage_id,e.node_id,e.remaining,e.protection_remaining,pct(e.loss)])))}
function svg(type,attrs,text){const n=document.createElementNS('http://www.w3.org/2000/svg',type);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!=null)n.textContent=text;return n}
function draw(r,k){const chart=el('chart');chart.replaceChildren();const values=[Math.min(...r.seeds.map(x=>x.loss)),...r.steps.map(s=>s.best_after)];const lo=Math.max(0,Math.min(...values)*100-.3),hi=Math.max(...values)*100+.3,x=i=>48+i/32*490,y=v=>180-(v*100-lo)/(hi-lo)*145;for(let i=0;i<=3;i++){const v=lo+(hi-lo)*i/3,yy=y(v/100);chart.append(svg('line',{x1:48,y1:yy,x2:538,y2:yy,stroke:'#e3e9ef'}),svg('text',{x:2,y:yy+4,'font-size':11,fill:'#596d81'},v.toFixed(1)+'%'))}chart.append(svg('polyline',{points:values.map((v,i)=>x(i)+','+y(v)).join(' '),fill:'none',stroke:'#206a84','stroke-width':2}),svg('circle',{cx:x(k+1),cy:y(values[k+1]),r:5,fill:'#e27148'}));[0,8,16,24,32].forEach(i=>chart.append(svg('text',{x:x(i)-5,y:204,'font-size':11,fill:'#596d81'},String(i))))}
function render(){const r=data.runs[+el('run').value],k=+el('step').value,s=r.steps[k];el('index').textContent=(k+1)+' / 32';el('identity').textContent=r.job.job_id+' · '+r.job.model+' · 最终程序 #'+r.best_id+' · 所有质量均来自归档';el('metrics').replaceChildren();[['当前候选 validation',pct(s.loss)],['当前最好 validation',pct(s.best_after)],['最终选中程序 test',r.summary.test_gap_percent.toFixed(3)+'%'],['累计实际 tokens',s.tokens.toLocaleString()]].forEach(([name,value])=>{const d=document.createElement('div');d.className='metric';const a=document.createElement('span'),b=document.createElement('b');a.textContent=name;b.textContent=value;d.append(a,b);el('metrics').append(d)});el('flow').textContent=(s.action==='explore'?'探索新方向':'开发父程序 #'+s.parent_id)+' → 候选 #'+s.node_id+' → '+(s.valid?'执行有效':'执行失败');el('event').replaceChildren(...table(['字段','记录'],[['保护 / 落后父代',s.protected+' / '+s.lagging],['方向',s.direction],['分类',s.classification],['相对父代改善',s.local_gain==null?'无父代':(100*s.local_gain).toFixed(4)+' 个百分点'],['本次授予开发资格',s.grant],['普通开发概率',s.p_develop.toFixed(4)]]).children);const cf=data.same.find(d=>d.job_id===r.job.job_id&&d.step===k);el('counter').textContent=cf?'同状态关闭优先保障：下一动作 '+cf.disabled_action+'，父程序 '+num(cf.disabled_parent)+'。仅动作重算；替代后续收益未知。':'此组无同状态反事实读出。';pool('before',s.pool_before);pool('after',s.pool_after);el('code').textContent=s.code||'无可执行程序；原始失败记录见归档。';draw(r,k)}
el('run').addEventListener('change',render);el('step').addEventListener('input',render);el('prev').onclick=()=>{el('step').value=Math.max(0,+el('step').value-1);render()};el('next').onclick=()=>{el('step').value=Math.min(31,+el('step').value+1);render()};render();
</script></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--results',type=Path,required=True)
    args=parser.parse_args();path=args.results
    data=read_json(path/'REPLAY_DATA.json');data['summary']=read_json(path/'S3_ANALYSIS.json')
    with (path/'SAME_STATE_NEXT_DECISION.csv').open(encoding='utf-8-sig',newline='') as f:
        data['same']=[{**r,'step':int(r['step'])} for r in csv.DictReader(f)]
    payload=json.dumps(data,ensure_ascii=False).replace('<','\u003c').replace('>','\u003e').replace('&','\u0026')
    output=path/'demo/index.html';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(TEMPLATE.replace('__DATA__',payload),encoding='utf-8',newline='\n')
    print(json.dumps({'runs':len(data['runs']),'path':str(output),'bytes':output.stat().st_size}))


if __name__=='__main__':main()
