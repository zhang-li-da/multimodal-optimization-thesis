def priority(f):
    d = f["distance"]
    rg = f["regret"]
    nr = f["nearest_remaining"]
    rd = f["return_distance"]
    pr = f["progress"]
    cd = f["cluster_density"]
    return -d * (1 - 0.15 * cd) + 0.3 * rg - 0.1 * nr + 0.25 * rd * pr