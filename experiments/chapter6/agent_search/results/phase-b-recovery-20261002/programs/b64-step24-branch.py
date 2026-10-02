def priority(f):
    dist = f['distance']
    ret = f['return_distance']
    near = f['nearest_remaining']
    mean = f['mean_remaining']
    reg = f['regret']
    prog = f['progress']
    cd = f['cluster_density']
    spread = f['spread']
    sn_den = max(1 + spread, 1e-9)
    sn = spread / sn_den
    one_m_cd = 1 - cd
    one_m_prog = 1 - prog
    term1 = -dist * (1 + 0.15 * sn)
    term2 = (0.10 + 0.20 * one_m_cd + 0.30 * prog) * ret
    term3 = (0.20 + 0.20 * one_m_cd + 0.40 * sn + 0.20 * cd + 0.10 * one_m_cd * prog) * reg
    term4 = 0.10 * one_m_cd * near
    term5 = -0.12 * cd * mean
    term6 = 0.18 * cd * prog * one_m_prog * sn
    mean_safe = max(mean, 1e-9)
    term7 = 0.12 * cd * (1 - near / mean_safe) * one_m_prog
    return term1 + term2 + term3 + term4 + term5 + term6 + term7