def priority(f):
    w = 0.25 + 0.1 * f['progress']
    return -f['distance'] + w * f['return_distance'] + 0.05 * f['regret']