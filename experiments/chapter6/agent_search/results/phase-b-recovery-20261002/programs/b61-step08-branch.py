def priority(f):
    d = f['distance']
    rd = f['return_distance']
    rgt = f['regret']
    return -d + 0.2 * rd + 0.3 * rgt + 0.2 * rgt * d
