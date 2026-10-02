def priority(f):
    d = f['distance']
    rd = f['return_distance']
    pr = f['progress']
    rg = f['regret']
    w = 0.15 + 0.3 * pr
    return -d + w * rd + 0.1 * rg
