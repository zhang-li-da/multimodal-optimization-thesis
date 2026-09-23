# Decision examples

Examples are selected by sorted job ID and earliest qualifying node. They illustrate mechanism events and are not selected by held-out performance.
Full parent/candidate code, validation evidence and archived test evaluation (if available) are in `decision_examples.json`.

## binpack/invalid_program

Run `minimax-binpack-niche-b0-tokens30000`, node 6.

Global gain: None; parent gain: None; neighbor gain: None.
Collision: False; local credit eligible: False; useful gain recorded: False.

```python
def priority(f):
    item = f["item"]
    remaining = f["remaining"]
    gap = f["gap"]
    mean_gap = f["mean_gap"]
    min_gap = f["min_gap"]
    std_gap = f["std_gap"]
    fraction_fitting = f["fraction_fitting"]
    position = f["position"]
    fill = 1 - remaining
    safe_gap = abs(gap)
    diff_mid = safe_gap - 0.5 * item
    exp_term = (-(diff_mid * diff_mid) / (0.08 + 1e-9))
    exp_term = max(exp_term, -50)
    exp_term = min(exp_term, 50)
    e = __import__("math").exp(exp_term)
    score = -2.5 * gap - 0.15 * gap * gap + 0.6 * (min_gap - gap) - 0.3 * std_gap - 0.08 * fraction_fitting - 0.04 * position + 0.04 * fill - 0.5 * e
    return score

```

## binpack/parent_or_neighbor_gain_without_global_record

Run `minimax-binpack-niche-b0-slots8`, node 6.

Global gain: 0.0; parent gain: 0.011034835400777446; neighbor gain: 0.0.
Collision: True; local credit eligible: True; useful gain recorded: False.

```python
def priority(f):
    item = f["item"]
    gap = f["gap"]
    fill = f["fill"]
    mean_gap = f["mean_gap"]
    min_gap = f["min_gap"]
    std_gap = f["std_gap"]
    fraction_fitting = f["fraction_fitting"]
    position = f["position"]
    eps = 1e-9
    ratio = gap / (item + eps)
    exact = 1.0 if gap < eps else 0.0
    base = -ratio
    fit_pen = -0.05 * ratio * fraction_fitting
    frag = -0.05 * (gap - min_gap) / (item + eps)
    spread = -0.01 * std_gap / (item + eps)
    pos_pen = -0.02 * position
    fill_bonus = 0.05 * fill
    score = base + fit_pen + frag + spread + pos_pen + fill_bonus + exact
    return score
```

## binpack/protected_local_gain

Run `minimax-binpack-relational_qp-b4-slots8`, node 9.

Global gain: 0.0; parent gain: 0.010745677095314793; neighbor gain: 0.0.
Collision: True; local credit eligible: True; useful gain recorded: True.

```python
def priority(f):
    gap = f["gap"]
    mean_gap = f["mean_gap"]
    fill = f["fill"]
    dev = abs(gap - mean_gap)
    score = -gap - 0.30 * dev + 0.10 * fill
    return score

```

## binpack/unproductive_collision

Run `minimax-binpack-niche-b0-slots8`, node 7.

Global gain: 0.0; parent gain: None; neighbor gain: 0.0.
Collision: True; local credit eligible: False; useful gain recorded: False.

```python
def priority(f):
    item = f['item']
    remaining = f['remaining']
    gap = f['gap']
    fill = f['fill']
    mean_gap = f['mean_gap']
    min_gap = f['min_gap']
    std_gap = f['std_gap']
    fraction_fitting = f['fraction_fitting']
    position = f['position']
    eps = 1e-9
    exact = 1.0 if gap < eps else 0.0
    tightness = -gap / (item + eps)
    deviation = abs(gap - mean_gap) / (item + eps)
    starve = abs(gap - min_gap) / (item + eps)
    rel_gap = gap / (remaining + eps)
    score = tightness + 0.5 * exact - 0.3 * deviation - 0.1 * rel_gap - 0.05 * starve
    score = score + 0.2 * fraction_fitting - 0.1 * (1.0 - fill)
    return score

```

## tsp/global_gain_with_collision

Run `minimax-tsp-niche-b0-slots8`, node 4.

Global gain: 0.010462372065397262; parent gain: 0.03879166266583628; neighbor gain: 0.03879166266583628.
Collision: True; local credit eligible: True; useful gain recorded: True.

```python
def priority(f):
    d = f["distance"]
    rd = f["return_distance"]
    nr = f["nearest_remaining"]
    mr = f["mean_remaining"]
    rg = f["regret"]
    pg = f["progress"]
    eps = 1e-9
    p = -d + 0.35 * rd - 0.15 * nr - 0.10 * mr + 0.20 * rg - 0.20 * pg
    return p

```

## tsp/invalid_program

Run `minimax-tsp-niche-b1-slots8`, node 7.

Global gain: None; parent gain: None; neighbor gain: None.
Collision: False; local credit eligible: False; useful gain recorded: False.

```python
def priority(f):
    d = f['distance']
    rd = f['return_distance']
    nr = f['nearest_remaining']
    mr = f['mean_remaining']
    rg = f['regret']
    pr = f['progress']
    cd = f['cluster_density']
    sp = f['spread']
    base = -d + 0.10 * rd + 0.15 * rg + 0.15 * nr
    density_term = cd / (sp + 1e-9)
    density_term = density_term / (1.0 + density_term)
    base += 0.10 * density_term
    if pr < 0.5:
        base += 0.05 * (mr - nr)
    else:
        base += 0.02 * nr
    return base

```

## tsp/parent_or_neighbor_gain_without_global_record

Run `minimax-tsp-niche-b0-slots8`, node 6.

Global gain: -0.03359784185064672; parent gain: -0.02313546978524946; neighbor gain: 0.006981055368160488.
Collision: True; local credit eligible: True; useful gain recorded: False.

```python
def priority(f):
    d = f["distance"]
    rd = f["return_distance"]
    nr = f["nearest_remaining"]
    mr = f["mean_remaining"]
    rg = f["regret"]
    pg = f["progress"]
    cd = f["cluster_density"]
    sp = f["spread"]
    p = -d + 0.32 * rd + 0.10 * rg + 0.40 * cd * pg + 0.05 * cd * (1 - pg) - 0.10 * sp * (1 - cd) - 0.05 * nr - 0.05 * mr - 0.15 * pg
    return p

```

## tsp/protected_local_gain

Run `minimax-tsp-relational_qp-b1-tokens30000`, node 7.

Global gain: -0.023986809874049032; parent gain: None; neighbor gain: 0.008670223746814129.
Collision: True; local credit eligible: True; useful gain recorded: True.

```python
def priority(f):
    p = f["progress"]
    w_d = 1.0 + 0.6 * p
    w_ret = 0.20 + 0.15 * p
    w_reg = 0.15 * p
    w_cl = 0.10 * (1.0 - p)
    w_spread = 0.05 * (1.0 - p)
    score = -w_d * f["distance"] + w_ret * f["return_distance"] + w_reg * f["regret"] + w_cl * f["cluster_density"] - w_spread * f["spread"]
    return score

```

## tsp/unproductive_collision

Run `minimax-tsp-niche-b0-slots8`, node 5.

Global gain: -0.04057889721880721; parent gain: -0.04057889721880721; neighbor gain: -0.0017872345529709316.
Collision: True; local credit eligible: False; useful gain recorded: False.

```python
def priority(f):
    d = f["distance"]
    rd = f["return_distance"]
    nr = f["nearest_remaining"]
    mr = f["mean_remaining"]
    rg = f["regret"]
    pg = f["progress"]
    cd = f["cluster_density"]
    sp = f["spread"]
    eps = 1e-9
    safe_nr = nr / (nr + 1.0)
    safe_mr = mr / (mr + 1.0)
    safe_sp = sp / (sp + 1.0)
    p = -d + 0.30 * rd + 0.15 * rg + 0.15 * cd - 0.05 * nr - 0.05 * mr - 0.10 * sp - 0.10 * pg
    guard = 1.0 / (abs(cd) + 1.0)
    p = p * (1.0 - 0.05 * safe_sp) * (1.0 - 0.05 * safe_nr)
    if rg < eps:
        p = p - 0.10 * pg
    if cd > 0.5:
        p = p + 0.05 * (cd - 0.5)
    p = p - 0.02 * safe_mr * guard
    p = p + 0.01 * rg * (1.0 - safe_sp)
    return p

```
