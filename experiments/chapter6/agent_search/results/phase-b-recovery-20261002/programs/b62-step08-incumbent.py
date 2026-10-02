def priority(f):
    d = f["distance"]
    rg = f["regret"]
    nr = f["nearest_remaining"]
    rd = f["return_distance"]
    pr = f["progress"]
    return -d + 0.3 * rg - 0.1 * nr + 0.25 * rd * pr