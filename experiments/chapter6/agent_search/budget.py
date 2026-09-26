"""Prospective budget admission and no-silent-overrun accounting.

Not used by S1 r1. UTF-8-byte input reservation plus margin is explicitly
an operational approximation when a provider tokenizer upper bound is absent.
Request caps are hard; token caps are claimed hard only with certified bounds.
"""
from __future__ import annotations
from dataclasses import dataclass,field
import math


@dataclass
class Budget:
    token_limit:int
    request_limit:int
    wall_limit:float
    certified_input_bound:bool=False
    known_tokens:int=0
    requests:int=0
    elapsed:float=0.
    pending:dict|None=None
    halted:str|None=None
    records:list=field(default_factory=list)

    def __post_init__(self):
        if self.token_limit<=0 or self.request_limit<=0 or self.wall_limit<=0:raise ValueError('Positive limits required')

    def can_reserve(self,input_bound,output_cap,elapsed):
        if self.pending is not None:raise ValueError('A request is already pending')
        if input_bound<0 or output_cap<=0 or elapsed<self.elapsed:raise ValueError('Invalid reservation or clock rollback')
        if self.halted:return False,self.halted
        if self.requests>=self.request_limit:return False,'request_limit'
        if elapsed>=self.wall_limit:return False,'wall_limit'
        if self.known_tokens+input_bound+output_cap>self.token_limit:return False,'token_reservation'
        return True,None

    def reserve(self,request_id,input_bound,output_cap,elapsed):
        if any(r['request_id']==request_id for r in self.records):raise ValueError('Do not resend an already charged request')
        allowed,reason=self.can_reserve(input_bound,output_cap,elapsed)
        if not allowed:return {'admitted':False,'reason':reason}
        self.pending={'request_id':request_id,'input_reservation':input_bound,'output_cap':output_cap,
            'started_elapsed':elapsed}
        self.requests+=1;self.elapsed=elapsed
        return {'admitted':True,'remaining_wall_seconds':self.wall_limit-elapsed,
            'token_bound_claim':'hard_if_provider_bound_true' if self.certified_input_bound else 'operational_not_certified'}

    def complete(self,input_tokens,output_tokens,elapsed,*,unknown=False):
        if self.pending is None:raise ValueError('No charged pending request')
        if elapsed<self.elapsed:raise ValueError('Clock rollback')
        record={**self.pending,'finished_elapsed':elapsed}
        if unknown or type(input_tokens) is not int or type(output_tokens) is not int or min(input_tokens,output_tokens)<0:
            self.halted='unknown_usage_or_dispatch';record.update(input_tokens=None,output_tokens=None,usage_complete=False)
        else:
            self.known_tokens+=input_tokens+output_tokens
            record.update(input_tokens=input_tokens,output_tokens=output_tokens,usage_complete=True)
            if input_tokens>self.pending['input_reservation'] or output_tokens>self.pending['output_cap']:
                self.halted='provider_exceeded_reservation'
            elif self.known_tokens>self.token_limit:self.halted='token_limit_exceeded'
        self.elapsed=elapsed;record['halted']=self.halted
        self.records.append(record);self.pending=None
        return record

    def summary(self):
        unknown=any(not r['usage_complete'] for r in self.records) or self.pending is not None
        return {'known_tokens':self.known_tokens,'total_tokens':None if unknown else self.known_tokens,
            'requests':self.requests,'elapsed':self.elapsed,'halted':self.halted,
            'unknown_usage':unknown,'token_budget_certified':self.certified_input_bound,
            'remaining_requests':max(0,self.request_limit-self.requests),
            'pending':self.pending}


def byte_reservation(system,prompt,max_tokens,margin=1024):
    return {'input_bound':len(system.encode('utf-8'))+len(prompt.encode('utf-8'))+margin,
            'output_cap':max_tokens,'certified':False,
            'reason':'provider tokenizer bound unavailable; report observed overruns and stop'}
