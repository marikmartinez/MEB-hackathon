FULL_RATIO = 0.9
LEVELS = [(0.5, "low"), (0.75, "medium"), (FULL_RATIO, "high")]


def level_for(ratio):
    if ratio is None:
        return "unknown"
    for bound, name in LEVELS:
        if ratio < bound:
            return name
    return "full"
