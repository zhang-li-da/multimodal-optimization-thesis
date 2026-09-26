"""Pure five-policy controller prototype. NOT used by frozen S1 experiments.

Defines the proposed outer explore/develop interface for P-space. Evaluation,
LLM calls and task data are deliberately external. Only synthetic unit tests
currently exercise this version; no claim of real search benefit.
"""
from __future__ import annotations
import copy
import random

from chapter6_demo.agent_search.directions import describe
from chapter6_demo.v12_2.common import digest

POLICIES=('SP','WR','FB','TS','AD')


class DirectionController:
    def __init__(self,policy,seed=0,*,capacity=4,grant=2,maximum_attempts=8,
                 quality_tolerance=.035,gain_epsilon=1e-4,window=8,protection=True):
        if policy not in POLICIES: raise ValueError('Unknown strategy')
        self.policy=policy;self.rng=random.Random(seed)
        self.capacity=capacity;self.grant=grant;self.maximum_attempts=maximum_attempts
        self.quality_tolerance=quality_tolerance;self.gain_epsilon=gain_epsilon;self.window=window
        self.protection=protection
        self.nodes={};self.order=[];self.active={};self.ledgers={};self.aliases={}
        self.init_best=None;self.best_id=None;self.history=[];self.pending=None
        self.serial=0;self.started=False

    def _direction(self,node):
        # Coarse deterministic structure, never a claim of true semantic basin.
        return describe(node['code'])['direction_id']

    def _observational_key(self,node):
        ev=node['evaluation']
        return digest({'behavior':ev['behavior'],'validation':ev['per_instance_loss']})

    def _valid(self,node):
        return bool(node['evaluation']['valid'])

    def _root(self,direction):
        while direction in self.aliases:direction=self.aliases[direction]
        return direction

    def _representative(self,direction):
        ids=[i for i in self.order if self._valid(self.nodes[i]) and self._root(self._direction(self.nodes[i]))==direction]
        return min(ids,key=lambda i:(self.nodes[i]['evaluation']['loss'],i)) if ids else None

    def initialize(self,nodes):
        if self.started or self.nodes:raise ValueError('Initialization only once')
        if not nodes or not all(self._valid(n) for n in nodes):raise ValueError('Shared seeds must be valid')
        for n in nodes:
            if n['id'] in self.nodes:raise ValueError('Duplicate initial id')
            self.nodes[n['id']]=copy.deepcopy(n);self.order.append(n['id'])
        self.best_id=min(self.order,key=lambda i:(self.nodes[i]['evaluation']['loss'],i))
        self.init_best=self.nodes[self.best_id]['evaluation']['loss']
        # All arms receive the same seeds; multi-direction arms allocate finite
        # starting credit to competitive structural representatives.
        for i in sorted(self.order,key=lambda i:(self.nodes[i]['evaluation']['loss'],i)):
            node=self.nodes[i];d=self._direction(node)
            if d=='unknown' or node['evaluation']['loss']>self.init_best+self.quality_tolerance:continue
            key=self._observational_key(node)
            previous=next((k for k in self.order if k<i and self._valid(self.nodes[k]) and self._observational_key(self.nodes[k])==key),None)
            if previous is not None and self._direction(self.nodes[previous])!=d:
                root=self._root(self._direction(self.nodes[previous]))
                if root!='unknown':self.aliases[d]=root
            d=self._root(d)
            self.ledgers.setdefault(d,{'attempts':0,'granted':0,'no_progress_streak':0})
            if self.policy in ('FB','TS','AD') and d not in self.active and len(self.active)<self.capacity:
                self._admit(d,node,0.)
        self.started=True

    def _admit(self,d,node,gain):
        ledger=self.ledgers.setdefault(d,{'attempts':0,'granted':0,'no_progress_streak':0})
        if ledger['attempts']>=self.maximum_attempts:return False
        if d not in self.active and ledger['granted']>=self.maximum_attempts:return False
        if d not in self.active and len(self.active)>=self.capacity:
            # Never evict an unspent initial protection grant.
            replaceable=[(k,v) for k,v in self.active.items() if not v['initial_protection_remaining']]
            if not replaceable:return False
            victim=min(replaceable,key=lambda kv:(kv[1]['remaining']>0,kv[1]['gain']/(1+kv[1]['attempts']),kv[1]['created']))[0]
            self.active.pop(victim)
        if d not in self.active:
            extra=min(self.grant,self.maximum_attempts-ledger['granted'])
            remaining=ledger['granted']-ledger['attempts']+extra
            # Reactivation retains the same cumulative ledger, not a fresh cap.
            first=ledger['granted']==0
            self.active[d]={'representative':node['id'],'remaining':remaining,'gain':gain,
                'attempts':ledger['attempts'],'created':self.serial,
                'initial_protection_remaining':extra if self.protection and first else 0}
            self.serial+=1;ledger['granted']+=extra
        else:
            entry=self.active[d]
            if node['evaluation']['loss']<self.nodes[entry['representative']]['evaluation']['loss']:
                entry['representative']=node['id'];entry['gain']=gain
            # Total granted opportunities is capped and never reset by aliases.
            extra=min(self.grant,self.maximum_attempts-ledger['granted'])
            if extra>0:entry['remaining']+=extra;ledger['granted']+=extra
        return True

    def _eligible(self):
        available=[]
        for d,e in self.active.items():
            if e['remaining']<=0 or self.ledgers[d]['attempts']>=self.maximum_attempts:continue
            available.append((d,e))
        return available

    def progress_rates(self):
        values={}
        for action in ('E','D'):
            rows=[e for e in self.history if e['action']==action][-self.window:]
            values[action]={'attempts':len(rows),'successes':sum(r['success'] for r in rows),
                            'rate':(1+sum(r['success'] for r in rows))/(2+len(rows))}
        return values

    def choose(self,budget_fraction,*,request_fits=True):
        if not self.started:raise ValueError('Initialize common seeds first')
        if self.pending is not None:raise ValueError('One pending proposal at a time')
        if not 0<=budget_fraction<=1:raise ValueError('Budget fraction outside [0,1]')
        if not request_fits:return {'action':'STOP','reason':'budget_reservation_failed'}
        eligible=self._eligible();rates=self.progress_rates()
        if self.policy=='SP':p=1.
        elif self.policy=='WR':p=0.
        elif self.policy=='FB':p=.5
        elif self.policy=='TS':p=.2+.6*budget_fraction
        else:p=max(.2,min(.8,.5+.6*(rates['D']['rate']-rates['E']['rate'])))
        draw=self.rng.random();intended='D' if draw<p else 'E'
        # SP always modifies the incumbent and has no independent protection.
        if self.policy=='SP':
            d=self._root(self._direction(self.nodes[self.best_id]));parent=self.best_id;action='D';fallback=False
        elif intended=='D' and eligible:
            d,entry=max(eligible,key=lambda kv:(kv[1]['gain']/(1+kv[1]['attempts']),-kv[1]['created']))
            parent=entry['representative'];action='D';fallback=False
        else:
            d=None;parent=None;action='E';fallback=intended=='D'
        decision={'action':action,'intended_action':intended,'parent_id':parent,'direction_id':d,
            'p_develop':p,'rng_draw':draw,'fallback':fallback,'eligible_directions':[k for k,v in eligible],
            'rates':rates,'budget_fraction':budget_fraction,'baseline_loss':self.nodes[parent]['evaluation']['loss'] if parent is not None else self.init_best}
        self.pending=copy.deepcopy(decision)
        return copy.deepcopy(decision)

    def observe(self,node,*,provider_indeterminate=False):
        if self.pending is None:raise ValueError('Choose first')
        if node['id'] in self.nodes:raise ValueError('Duplicate observation')
        pending=self.pending
        if node.get('parent_id')!=pending['parent_id']:raise ValueError('Child parent differs from selection')
        if provider_indeterminate:
            # Caller must halt; do not manufacture a completed proposal or success.
            return {'halt':True,'reason':'unknown_provider_outcome','pending':copy.deepcopy(pending)}
        d=pending['direction_id'];action=pending['action']
        if action=='D' and self.policy!='SP':
            e=self.active[d];e['remaining']-=1;e['attempts']+=1
            e['initial_protection_remaining']=max(0,e['initial_protection_remaining']-1)
            self.ledgers[d]['attempts']+=1
        valid=self._valid(node);key=self._observational_key(node) if valid else None
        known=next((self.nodes[i] for i in self.order if valid and self._valid(self.nodes[i]) and self._observational_key(self.nodes[i])==key),None)
        competitive=valid and node['evaluation']['loss']<=self.init_best+self.quality_tolerance
        gain=pending['baseline_loss']-node['evaluation']['loss'] if valid else None
        success=valid and known is None and competitive and gain>self.gain_epsilon
        direction_before=self._root(self._direction(node)) if valid else 'unknown'
        representative_before=self._representative(direction_before) if valid else None
        prior_direction_loss=(self.nodes[representative_before]['evaluation']['loss']
                              if representative_before is not None else None)
        self.nodes[node['id']]=copy.deepcopy(node);self.order.append(node['id'])
        if valid and node['evaluation']['loss']<self.nodes[self.best_id]['evaluation']['loss']:
            self.best_id=node['id']
        actual=self._direction(node) if valid else 'unknown'
        if valid and known is not None and actual!=self._direction(known):
            root=self._root(self._direction(known))
            if actual!=root and root!='unknown' and actual not in self.ledgers:self.aliases[actual]=root
        actual=self._root(actual)
        admitted=False
        if self.policy in ('FB','TS','AD') and competitive and known is None and actual!='unknown':
            direction_gain=(prior_direction_loss-node['evaluation']['loss']
                            if prior_direction_loss is not None else None)
            first_grant=self.ledgers.get(actual,{}).get('granted',0)==0
            if first_grant or (direction_gain is not None and direction_gain>self.gain_epsilon):
                admitted=self._admit(actual,node,max(0.,direction_gain if direction_gain is not None else (gain or 0.)))
        if action=='D' and self.policy!='SP':
            self.ledgers[d]['no_progress_streak']=0 if success else self.ledgers[d]['no_progress_streak']+1
        event={'node_id':node['id'],'action':action,'valid':valid,'success':success,
            'gain':gain,'competitive':competitive,'known_reproduction':known is not None,
            'direction_id':actual,'allocated_direction_id':d,'admitted':admitted,
            'failure_type':node.get('failure_type'),'global_best_id':self.best_id,
            'decision':copy.deepcopy(pending)}
        self.history.append(event);self.pending=None
        return event

    def output(self):
        if self.pending is not None:raise ValueError('Cannot freeze with an unresolved proposal')
        return {'best_id':self.best_id,'selected_on':'validation','code':self.nodes[self.best_id]['code']}
