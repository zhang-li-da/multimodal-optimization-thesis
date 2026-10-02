def priority(f):
    cd = f['cluster_density']
    prog = f['progress']
    rw = 0.1 + 0.2 * (1 - cd) + 0.3 * prog
    regret_w = 0.25 + 0.15 * (1 - cd)
    lookahead_w = 0.10 * (1 - cd)
    return -f['distance'] + rw * f['return_distance'] + regret_w * f['regret'] + lookahead_w * f['nearest_remaining']