"""Monte Carlo for the hazard-ramp dragon.

Wake check each night after theft: p = clamp((START - hoard) / (START - FLOOR), 0, 1).
First wake: rage night (no theft next night), hoard refills to 250, half gold each.
Second wake: run over, everyone burns.
"""
import random

def run(takes_total, start_curve, floor_curve, trials=3000, days=30,
        start=250, cap=300, regrow=0.12):
    outcomes = {"none": 0, "one": 0, "fatal": 0}
    first_wake_days = []
    for _ in range(trials):
        hoard = start
        wakes = 0
        rage = False
        for day in range(1, days + 1):
            if rage:
                rage = False
            else:
                t = min(takes_total, hoard)
                hoard -= t
            p = (start_curve - hoard) / (start_curve - floor_curve)
            p = max(0.0, min(1.0, p))
            if random.random() < p:
                wakes += 1
                if wakes == 1:
                    first_wake_days.append(day)
                    hoard = start
                    rage = True
                else:
                    break
            hoard = min(hoard * (1 + regrow), cap)
        if wakes == 0: outcomes["none"] += 1
        elif wakes == 1: outcomes["one"] += 1
        else: outcomes["fatal"] += 1
    n = trials
    med = sorted(first_wake_days)[len(first_wake_days)//2] if first_wake_days else None
    return (outcomes["none"]/n, outcomes["one"]/n, outcomes["fatal"]/n, med)

curves = [(120, 60), (110, 50), (130, 70), (120, 40)]
scenarios = [("all take 1", 10), ("all take 2", 20), ("8x1 + 2x5", 18),
             ("8x2 + 2x5", 26), ("all take 3", 30), ("all take 4", 40), ("all take 5", 50)]

random.seed(7)
for sc, fc in curves:
    print(f"\n=== curve: 0% at {sc}, 100% at {fc} ===")
    print(f"{'scenario':<12} {'no wake':>8} {'one wake':>9} {'fatal':>7} {'median 1st wake':>16}")
    for name, total in scenarios:
        none, one, fatal, med = run(total, sc, fc)
        print(f"{name:<12} {none:>7.0%} {one:>8.0%} {fatal:>6.0%} {str(med):>16}")
