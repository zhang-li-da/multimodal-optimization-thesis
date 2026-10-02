def priority(f):
    return -f["distance"] + 0.4 * f["regret"] + 0.2 * f["return_distance"] * (1 + f["progress"]) + 0.08 * f["cluster_density"]