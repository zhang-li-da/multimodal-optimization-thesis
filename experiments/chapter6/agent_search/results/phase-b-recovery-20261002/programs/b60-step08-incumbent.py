def priority(f):
    d = f["distance"]
    r = f["regret"]
    rd = f["return_distance"]
    p = f["progress"]
    coef = 0.08 + 0.65 * p * p
    return -d + 0.4 * r + coef * rd