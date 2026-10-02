def priority(f):
    w_return = 0.25 + 0.10 * f['progress']
    w_regret = 0.05 + 0.10 * f['progress']
    return -f['distance'] + w_return * f['return_distance'] + w_regret * f['regret'] + 0.03 * f['cluster_density']