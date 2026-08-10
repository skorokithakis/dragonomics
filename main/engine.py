"""Pure engine math for the Dragon's Hoard commons game.

This module is plain Python: no Django imports, no database access, so it can
be imported and tested standalone.

Night order of operations (theft, then wake roll, then regrowth):

1. ``resolve_takes`` — thieves take coins from the hoard.
2. ``wake_probability`` — the wake roll.
3. On a wake night the hoard refills to ``HOARD_START`` and regrowth is
   skipped; otherwise ``regrow`` applies to what remains.

``tally_moot`` is the Moot's pure math: quorum, pass/fail, and the day's law.
"""

import math
import random

HOARD_START = 250
HOARD_CAP = 300
REGROWTH = 0.12
TAKE_MAX = 5
HAZARD_TOP = 120
HAZARD_FLOOR = 60
SURE_DAYS = 25
END_CHANCE = 0.20
END_CAP_DAY = 40


def wake_probability(hoard: int) -> float:
    """Probability the dragon wakes tonight.

    Zero at ``HAZARD_TOP`` coins or more, rising linearly to 1 at
    ``HAZARD_FLOOR`` or fewer: clamp((HAZARD_TOP - hoard) / (HAZARD_TOP - HAZARD_FLOOR), 0, 1).
    """
    return min(1.0, max(0.0, (HAZARD_TOP - hoard) / (HAZARD_TOP - HAZARD_FLOOR)))


def regrow(hoard: int) -> int:
    """Regrow the hoard overnight: floor(hoard * (1 + REGROWTH)), capped at HOARD_CAP."""
    return min(math.floor(hoard * (1 + REGROWTH)), HOARD_CAP)


def resolve_takes(requests: list[int], hoard: int, rng: random.Random) -> list[int]:
    """Resolve the night's thefts, returning the coins each thief gets.

    ``requests`` is the amount each thief asked for, ``hoard`` the coins
    available. While the pile lasts every request is honored in full; on
    overdraw a random night order is drawn from ``rng`` and latecomers get
    what remains, possibly nothing. Coins are conserved: the returned amounts
    sum to min(sum(requests), hoard).
    """
    if sum(requests) <= hoard:
        return list(requests)
    order = list(range(len(requests)))
    rng.shuffle(order)
    out = [0] * len(requests)
    remaining = hoard
    for i in order:
        out[i] = min(requests[i], remaining)
        remaining -= out[i]
    return out


def wake_loss(gold: int) -> int:
    """Gold a thief loses on a wake: half, rounded up."""
    return math.ceil(gold / 2)


def tally_moot(ballots: list, active_count: int) -> dict:
    """Tally a Moot's ballots: pure math, no database access.

    ``ballots`` is an iterable of ``(thief_id, proposal_id, choice)``
    triples, choice one of "yes", "no", "abstain"; ``active_count`` is the
    number of active thieves. Returns a dict:

    - ``quorum``: true when at least half of the active thieves (rounded
      up) cast some ballot; abstention counts as casting.
    - ``tallies``: per proposal id, ``{"yes": y, "no": n, "abstain": a}``.
    - ``winner``: the proposal id that becomes the day's law, or ``None``.

    A proposal passes when yes strictly beats no. When several proposals
    pass, the one with the most yes votes wins; a tie at the top means no
    law that day. Without quorum nothing passes.
    """
    tallies: dict = {}
    casters: set = set()
    for thief_id, proposal_id, choice in ballots:
        tally = tallies.setdefault(proposal_id, {"yes": 0, "no": 0, "abstain": 0})
        if choice in tally:
            tally[choice] += 1
        casters.add(thief_id)
    quorum = len(casters) >= math.ceil(active_count / 2)
    passing = [pid for pid, tally in tallies.items() if tally["yes"] > tally["no"]]
    winner = None
    if quorum and passing:
        best = max(tallies[pid]["yes"] for pid in passing)
        top = [pid for pid in passing if tallies[pid]["yes"] == best]
        if len(top) == 1:
            winner = top[0]
    return {"quorum": quorum, "tallies": tallies, "winner": winner}
