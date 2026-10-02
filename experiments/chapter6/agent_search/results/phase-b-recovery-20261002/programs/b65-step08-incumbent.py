def priority(f):
    return -f["distance"] + 0.25 * f["return_distance"] * (1 + f["progress"]) + 0.1 * f["regret"]