"""Quick sweep of the commons loop for The Dragon's Hoard.

Night: agents take; hoard decreases; wake check; regrowth on remainder, capped.
Strategies are fixed per scenario. Deterministic (no RNG needed for these).
"""

def run(start, regrow, cap_mult, threshold_frac, takes_fn, days=30):
    hoard = start
    cap = start * cap_mult
    threshold = start * threshold_frac
    wakes = []
    for day in range(1, days + 1):
        takes = takes_fn(day, hoard)
        total = sum(takes)
        # can't take more than the hoard holds
        total = min(total, hoard)
        hoard -= total
        if hoard < threshold:
            wakes.append(day)
            hoard = start  # reset after wake (recommended collapse rule)
        hoard = min(hoard * (1 + regrow), cap)
    return wakes, hoard

def scenario(n_coop, coop_take, n_defect, defect_take=5):
    def f(day, hoard):
        return [coop_take] * n_coop + [defect_take] * n_defect
    return f

configs = [
    # (label, start, regrow, cap_mult, threshold_frac)
    ("A: 100 / 15% / cap1.0 / thr0.3", 100, 0.15, 1.0, 0.3),
    ("B: 200 / 15% / cap1.2 / thr0.3", 200, 0.15, 1.2, 0.3),
    ("C: 250 / 12% / cap1.2 / thr0.4", 250, 0.12, 1.2, 0.4),
    ("D: 300 / 10% / cap1.2 / thr0.4", 300, 0.10, 1.2, 0.4),
    ("E: 250 / 15% / cap1.2 / thr0.4", 250, 0.15, 1.2, 0.4),
]

scenarios = [
    ("all take 1        (total 10)", scenario(10, 1, 0)),
    ("all take 2        (total 20)", scenario(10, 2, 0)),
    ("all take 3        (total 30)", scenario(10, 3, 0)),
    ("8 take 2, 2 take 5 (total 26)", scenario(8, 2, 2)),
    ("8 take 1, 2 take 5 (total 18)", scenario(8, 1, 2)),
    ("all take 5        (total 50)", scenario(10, 5, 0)),
]

for label, start, regrow, capm, thr in configs:
    print(f"\n=== {label} (threshold={start*thr:.0f}, cap={start*capm:.0f}) ===")
    # sustainable steady-state total take at full hoard
    sustain = start * capm * regrow / (1 + regrow)
    print(f"  sustainable total take/night at cap: {sustain:.1f}  ({sustain/10:.2f} per thief)")
    for sname, sfn in scenarios:
        wakes, final = run(start, regrow, capm, thr, sfn)
        w = f"wakes on days {wakes}" if wakes else "no wake"
        print(f"  {sname}: {w}; final hoard {final:.0f}")
