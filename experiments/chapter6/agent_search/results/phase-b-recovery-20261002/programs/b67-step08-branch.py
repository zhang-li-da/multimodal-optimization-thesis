def priority(f):
    return -f["distance"] + 0.2 * f["return_distance"] + 0.15 * f["regret"]
