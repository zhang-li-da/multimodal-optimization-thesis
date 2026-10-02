def priority(f):
    d = f['distance']
    rd = f['return_distance']
    nr = f['nearest_remaining']
    rgt = f['regret']
    prg = f['progress']
    cd = f['cluster_density']
    return -d + 0.25 * rd + (0.5 - 0.4 * prg) * rgt + 0.1 * rgt * d + (0.4 - 0.3 * prg) * nr - 0.15 * cd