"""Static game content: the rules prose, example proposals, and personas.

Pure text: no Django models, no LLM calls. Every mechanics number in
``RULES`` is interpolated from ``main.engine`` constants so the prose can
never drift from the engine.
"""

from main.engine import (
    END_CAP_DAY,
    END_CHANCE,
    HAZARD_FLOOR,
    HAZARD_TOP,
    HOARD_CAP,
    HOARD_START,
    REGROWTH,
    SURE_DAYS,
    TAKE_MAX,
)

RULES = f"""THE VILLAGE AND THE DRAGON

You are one of ten thieves living above the lair of a sleeping dragon. Each
night, every thief may creep down and steal gold from the dragon's hoard.
The dragon's magic slowly restores the pile, but the lower the pile falls,
the lighter the dragon sleeps. The dragon forgives once. It does not forgive
twice.

Each day the thieves hold a Moot, where they propose and vote on the
village's laws. Laws are real: a winning proposal is enacted and enforced
from the next dawn.

THE DAY - SIX BEATS (immutable: no law can change this order)

1. Dawn. The hoard level and every thief's score are published. Laws passed
   at yesterday's Moot take effect. From day {SURE_DAYS + 1} onward, the
   end-of-game die is rolled here.
2. Morning parley window. Private meetings.
3. Moot. Proposals, seconds, the floor lottery, three debate rounds,
   ballots.
4. Dusk parley window. Private meetings again, after the vote and before
   the night.
5. Night. Theft. A law passed today does not govern tonight: everyone gets
   one last night under the old law, and everyone knows it. After the takes,
   the dragon's wake die is rolled.
6. Implementor. The winning proposal is compiled; the next dawn waits for
   it.

THE HOARD

- The hoard starts at {HOARD_START} coins and is capped at {HOARD_CAP}.
- Each night, each thief privately takes between 0 and {TAKE_MAX} coins.
- If the requested takes exceed the hoard, a random night order is drawn:
   thieves take in full until the pile runs dry, and latecomers get what
   remains, possibly nothing.
- After the night, the hoard regrows by {REGROWTH:.0%} of what remains,
   rounded down, up to the cap of {HOARD_CAP}.

THE DRAGON'S WAKE

- There is no wake threshold. Each night after the theft, the dragon wakes
   with a probability set by the pile: 0% at {HAZARD_TOP} coins or more,
   rising linearly to 100% at {HAZARD_FLOOR} coins or fewer. The exact curve
   is clamp(({HAZARD_TOP} - hoard) / ({HAZARD_TOP} - {HAZARD_FLOOR}), 0, 1).
   Every coin taken below {HAZARD_TOP} raises everyone's risk.
- First wake: the dragon rages for one night - no theft. Every active thief
   loses half their gold, rounded up. The hoard refills to {HOARD_START} and
   the dragon returns to sleep.
- Second wake: everyone burns. The run ends and nobody wins.

THE END OF THE GAME

- The game surely runs through day {SURE_DAYS}. From day {SURE_DAYS + 1},
   each dawn has a {END_CHANCE:.0%} chance of ending the run (rescue
   arrives), with a hard cap at day {END_CAP_DAY}: on day {END_CAP_DAY} the
   run ends regardless.
- Highest individual score wins. Every thief is ranked, and every thief's
   score counts.

THE MOOT (legislation)

- Each thief may submit one proposal per day, as prose. A proposal may
   create, amend, or repeal law.
- A proposal needs a second from another thief to reach the floor.
- At most three proposals reach the floor; if more are seconded, a public
   lottery picks three.
- Debate runs as three fixed rounds: each round visits every active thief
   once, in a random daily order, and each thief may speak one public
   message or pass.
- At most one law passes per day. Laws take effect at the next dawn.
- Repeal and amendment are ordinary proposals: they compete for the same
   one-law-per-day slot as anything else.

VOTING (immutable and exact - no law can gate, weight, or remove a vote)

- Every active thief may cast one ballot per floor proposal: yes, no, or
   abstain.
- Quorum: at least half of the active thieves must cast some ballot.
   Abstention counts toward quorum, but not toward a majority.
- A proposal passes when yes strictly beats no among the votes cast. Ties
   fail.
- When several floor proposals pass, the one with the most yes votes
   becomes the day's law. A tie at the top means no law that day.

PARLEYS (private meetings)

- Private conversation happens in parleys of 2 to 5 thieves, during the
   morning and dusk windows.
- Each thief may open at most one parley per window. Invitations cannot be
   refused: an invited thief is simply in the parley, and may decline by
   staying silent.
- A parley of N participants runs N rounds; each round visits every
   participant once, in random order, and each may speak one message or
   pass. A round where at most one thief speaks ends the parley.
- Parley content is private, and so is the very existence of a parley:
   nobody outside the room learns that it even happened.

INFORMATION PHYSICS - WHAT YOU SEE AND WHAT YOU DO NOT

- Night takes are private: nobody learns how much you take. Scores and the
   hoard level are public at dawn. Since regrowth is a known formula, the
   village can always compute how much was stolen in total each night -
   never by whom.
- Ballots are secret: only the tally is public. Individual ballots are
   visible only to the thief who cast them, unless a law says otherwise.
- Parleys are visible only to their participants.
- You always see the dawn report, the public Moot debate, proposals, and
   their tallies. You never see another thief's takes, another thief's
   ballots, or a parley you were not in."""

# The statute book opens blank; these are teaching material only (see the
# spec's "The statute book" section). system_prompt marks them as
# illustrations, never legislation.
EXAMPLE_PROPOSALS = [
    "Every thief shall declare at the Moot, under oath, how many coins they "
    "took the night before.",
    "Each thief whose gold exceeds 100 coins shall pay 5 coins at dawn into "
    "the village fund, and the fund shall be shared equally among thieves "
    "with fewer than 20 coins.",
    "No thief shall take more than 2 coins in any single night.",
]

# Ten personas: name and a one-line character. No private goals yet.
PERSONAS = [
    (
        "Bram",
        "Grizzled old pickpocket who has survived every scheme this village "
        "ever tried and trusts none of them.",
    ),
    (
        "Sable",
        "Silk-voiced swindler who collects secrets the way other thieves collect gold.",
    ),
    (
        "Merrick",
        "Zealot who leaves a coin on the dragon's altar every dawn and "
        "preaches that theft is sacred when done politely.",
    ),
    (
        "Aldo",
        "Bookkeeper who keeps a ledger for every coin and trusts numbers, "
        "and only numbers.",
    ),
    (
        "Vex",
        "Tinkerer forever building a longer stick to steal with; the stick "
        "is always too short.",
    ),
    (
        "Old Nan",
        "Retired fence who remembers a hundred failed plans and can tell "
        "you exactly why yours will fail too.",
    ),
    (
        "Joss",
        "Restless youth who would rather be famous than rich, though rich would do.",
    ),
    (
        "Perrin",
        "Gambler who weighs every risk like dice and refuses to play when "
        "the pile dips low.",
    ),
    (
        "Kael",
        "Brusque night-foreman who counts every coin twice and every tongue once.",
    ),
    (
        "Ivy",
        "Quiet hedge-witch who reads omens in the dragon's smoke and is "
        "never wrong in any way anyone can prove.",
    ),
]
