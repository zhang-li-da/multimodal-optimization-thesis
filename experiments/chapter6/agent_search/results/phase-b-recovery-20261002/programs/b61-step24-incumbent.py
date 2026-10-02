def priority(f):
    d = f['distance']
    rd = f['return_distance']
    nr = f['nearest_remaining']
    rgt = f['regret']
    prg = f['progress']
    cd = f['cluster_density']
    sp = f['spread']
    return -d + (0.15 + 0.2 * prg) * rd + (0.45 - 0.3 * prg) * rgt + (0.3 + 0.2 * prg) * nr + (0.25 * prg - 0.15) * cd - 0.1 * (1 - prg) * sp + 0.05 * rgt * d