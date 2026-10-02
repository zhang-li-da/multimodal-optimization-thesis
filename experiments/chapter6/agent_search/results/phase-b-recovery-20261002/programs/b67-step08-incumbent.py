def priority(f):
    d = f["distance"]
    rd = f["return_distance"]
    r = f["regret"]
    p = f["progress"]
    coef = 0.1 + 0.25 * p * p
    denom = 1.0 + 0.5 * d + 1e-9
    return -d + coef * rd + 0.3 * r / denom