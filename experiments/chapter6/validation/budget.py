"""Conservative online admission using actual usage; no post-hoc budget cutoff."""
from __future__ import annotations


class BudgetStop(RuntimeError):
    pass


def input_upper_bound(system, prompt):
    # Text tokenizers cannot ordinarily exceed one token per UTF-8 byte;
    # reserve an additional 512 for message serialization. Verify each real
    # response against this bound and invalidate a run if a provider violates it.
    # This is a checked admission bound, not a claim about undocumented APIs.
    return len(system.encode("utf-8")) + len(prompt.encode("utf-8")) + 512


class TokenBudget:
    def __init__(self, cap, usage=None):
        self.cap = cap
        self.usage = list(usage or [])

    @property
    def used(self):
        return sum(u["input_tokens"] + u["output_tokens"] for u in self.usage)

    def request(self, client, system, prompt, max_tokens, stage, iteration):
        bound = input_upper_bound(system, prompt)
        if self.used + bound + max_tokens > self.cap:
            raise BudgetStop("Insufficient remaining token budget for the next complete call.")
        response = client.complete(system, prompt, max_tokens=max_tokens)
        record = {"stage": stage, "iteration": iteration, "input_admission_bound": bound,
                  "requested_output_cap": max_tokens, **response.usage()}
        self.usage.append(record)
        record["usage_contract_valid"] = (
            0 < record["input_tokens"] <= bound and
            0 <= record["output_tokens"] <= max_tokens and self.used <= self.cap
        )
        return response, record
