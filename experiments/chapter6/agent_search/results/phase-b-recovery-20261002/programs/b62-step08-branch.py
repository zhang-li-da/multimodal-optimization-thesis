def priority(f):
    d = f["distance"]
    rg = f["regret"]
    rd = f["return_distance"]
    pr = f["progress"]
    return -d + 0.3 * rg + 0.25 * rd * pr