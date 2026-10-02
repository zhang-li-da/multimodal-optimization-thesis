def priority(f):
    d = f['distance']
    rd = f['return_distance']
    pr = f['progress']
    rg = f['regret']
    nr = f['nearest_remaining']
    w1 = 0.15 + 0.3 * pr
    w2 = 0.1 + 0.15 * pr + 0.05 * pr * pr
    return -d + w1 * rd + w2 * rg - 0.12 * pr * nr