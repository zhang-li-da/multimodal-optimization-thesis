def priority(f):
    d = f["distance"]
    r = f["regret"]
    rd = f["return_distance"]
    p = f["progress"]
    coef = 0.1 + 0.2 * p
    return -d + 0.5 * r + coef * rd
