def priority(f):
    cd = f["cluster_density"]
    prog = f["progress"]
    rw = 0.1 + 0.2 * (1 - cd) + 0.3 * prog
    return -f["distance"] + rw * f["return_distance"] + 0.25 * f["regret"]