def priority(f):
    d = f['distance']
    rd = f['return_distance']
    rgt = f['regret']
    return -d + 0.25 * rd + 0.2 * rgt + 0.1 * rgt * d