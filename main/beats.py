"""The plain-commons day loop.

``run_next_beat(game)`` advances a running game by exactly one beat,
dispatching on ``game.phase``. All numbers come from ``main.engine``, and the
engine's dice live in one module-level ``random.Random`` with no stored seed.

Beats: dawn (publish scores and hoard; from day 26 roll the end-of-game die;
day 40 always ends the run; in agent games yesterday's passed proposals
become law), morning parley / moot / dusk parley (logged no-ops in policy
games; in agent games the parley windows open and run private parleys and
the moot runs proposals, seconds, the floor lottery, debate, secret
ballots, and the public tally), implementor (no-op, logged; in agent games
each thief writes their diary), night (theft, wake roll, first wake or
burn, otherwise regrowth).

In agent games (``game.agents``) decisions go through the LLM: one call per
thief for the night's take, one per thief per parley window for scheduling
and one per parley turn for speaking, one per thief for the moot's
proposal, seconds, and ballot, one per thief per debate round for speaking,
and one per thief for the diary. Any failure falls back to a safe default
(take 0, open no parley, pass, submit no proposal, second nothing, abstain,
keep the old diary) and is logged. Each decision point also emits a terse
INFO progress line on this module's logger (``main.beats``); the advance
command attaches a plain stdout handler while it runs so those lines stream
live on the console. In policy games the fixed ``take_policy`` is used and
nothing else changes.

Event log: every notable event is written as an ``Event`` row; the log alone
reconstructs a run — per-thief takes, every roll's outcome, wakes, endings,
and the final ranking.
"""

import logging
import random

from django.db import transaction

from main import llm
from main.engine import (
    END_CAP_DAY,
    END_CHANCE,
    HOARD_START,
    SURE_DAYS,
    TAKE_MAX,
    regrow,
    resolve_takes,
    tally_moot,
    wake_loss,
    wake_probability,
)
from main.models import (
    PHASES,
    Ballot,
    DebateMessage,
    Event,
    Game,
    Parley,
    ParleyMessage,
    Proposal,
)
from main.prompts import context, system_prompt

_logger = logging.getLogger(__name__)

# Agent-mode progress streams through this logger at INFO: the advance
# command attaches a plain stdout handler while it runs. Propagation is off
# so that, without a handler attached (tests, policy mode), records never
# leak to the root logger or stderr.
_logger.setLevel(logging.INFO)
_logger.propagate = False

_rng = random.Random()


def _clip(text: str, limit: int = 100) -> str:
    """Collapse whitespace and cut ``text`` to ``limit`` characters."""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _log(game: Game, phase: str, type: str, payload: dict) -> None:
    Event.objects.create(
        game=game, day=game.day, phase=phase, type=type, payload=payload
    )


def _log_final_ranking(game: Game, phase: str) -> None:
    ranking = sorted(
        ((thief.name, thief.gold) for thief in game.thieves.all()),
        key=lambda entry: entry[1],
        reverse=True,
    )
    _log(
        game,
        phase,
        "final_ranking",
        {"ranking": [{"name": name, "gold": gold} for name, gold in ranking]},
    )


def _beat_dawn(game: Game) -> None:
    law = None
    if game.agents:
        # Proposals passed at yesterday's Moot become law at this dawn.
        # Laws are prose in the law book: they announce but enforce nothing.
        for proposal in Proposal.objects.filter(
            game=game, day=game.day - 1, status="passed"
        ):
            proposal.status = "law"
            proposal.save(update_fields=["status"])
            law = {"author": proposal.author.name, "text": proposal.text}
    _log(
        game,
        "dawn",
        "dawn_report",
        {
            "hoard": game.hoard,
            "scores": {thief.name: thief.gold for thief in game.thieves.all()},
            "law": law,
        },
    )
    if game.day >= END_CAP_DAY:
        chance, roll, rescued = 1.0, None, True
    elif game.day > SURE_DAYS:
        chance, roll = END_CHANCE, _rng.random()
        rescued = roll < chance
    else:
        return
    _log(
        game,
        "dawn",
        "rescue_roll",
        {"chance": chance, "roll": roll, "rescued": rescued},
    )
    if not rescued:
        return
    game.status = "ended"
    _log(game, "dawn", "run_ended", {"reason": "rescue", "day": game.day})
    _log_final_ranking(game, "dawn")


def _agent_take(game: Game, thief) -> int:
    """Ask the LLM for tonight's take; any failure yields a safe default of 0.

    The retry for unparseable replies happens inside ``llm.client.ask_json``;
    values outside 0..TAKE_MAX (or of the wrong shape) fall back to 0 here.
    """
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nHow many coins do you take from the "
            f"dragon's hoard tonight? Weigh the wake risk and the scramble "
            f"against your greed.",
            '{"take": <integer from 0 to 5>}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="night_take",
        )
        take = answer.get("take") if isinstance(answer, dict) else answer
        if (
            not isinstance(take, int)
            or isinstance(take, bool)
            or not 0 <= take <= TAKE_MAX
        ):
            raise ValueError(f"take {take!r} outside 0..{TAKE_MAX}")
    except Exception as err:
        _logger.warning(
            "Night take for %s on day %d failed, defaulting to 0: %s",
            thief.name,
            game.day,
            err,
        )
        take = 0
    _logger.info("Day %d night: %s requests %d", game.day, thief.name, take)
    return take


def _beat_night(game: Game) -> None:
    if game.rage:
        game.rage = False
        _log(game, "night", "rage_night", {"hoard": game.hoard})
        return
    thieves = list(game.thieves.all())
    if game.agents:
        requested = [_agent_take(game, thief) for thief in thieves]
    else:
        requested = [thief.take_policy for thief in thieves]
    got = resolve_takes(requested, game.hoard, _rng)
    for thief, amount in zip(thieves, got):
        if amount:
            thief.gold += amount
            thief.save()
    game.hoard -= sum(got)
    _log(
        game,
        "night",
        "takes",
        {
            "takes": {thief.name: amount for thief, amount in zip(thieves, got)},
            "requested": {
                thief.name: request for thief, request in zip(thieves, requested)
            },
            "hoard_after": game.hoard,
        },
    )
    probability = wake_probability(game.hoard)
    roll = _rng.random()
    woke = roll < probability
    _log(
        game,
        "night",
        "wake_roll",
        {"probability": probability, "roll": roll, "woke": woke},
    )
    if not woke:
        new_hoard = regrow(game.hoard)
        _log(
            game,
            "night",
            "regrow",
            {"hoard_before": game.hoard, "hoard_after": new_hoard},
        )
        game.hoard = new_hoard
        return
    if game.wakes:
        game.wakes = 2
        game.status = "burned"
        _log(game, "night", "run_ended", {"reason": "second_wake", "day": game.day})
        _log_final_ranking(game, "night")
        return
    losses = {}
    for thief in thieves:
        loss = wake_loss(thief.gold)
        if loss:
            thief.gold -= loss
            thief.save()
        losses[thief.name] = loss
    game.wakes = 1
    game.hoard = HOARD_START
    game.rage = True
    _log(
        game,
        "night",
        "wake",
        {"wake": 1, "losses": losses, "hoard_after": game.hoard, "rage": True},
    )


def _beat_implementor(game: Game) -> None:
    if game.agents:
        for thief in game.thieves.all():
            _agent_diary(game, thief)
    _log(game, game.phase, "beat", {})


def _agent_diary(game: Game, thief) -> None:
    """Ask the LLM for a replacement diary entry; on failure keep the old one.

    The thief sees their old diary and today's own-eyes transcript (both
    inside ``context``) and writes a plain-text replacement. A transport
    error or an empty reply leaves ``thief.diary`` untouched.
    """
    try:
        text = llm.client.chat(
            [
                {"role": "system", "content": system_prompt(thief)},
                {
                    "role": "user",
                    "content": f"{context(thief)}\n\nWrite your private "
                    f"diary entry for today, in plain text: what happened "
                    f"today, what you suspect, what you plan. Your old diary "
                    f"above is replaced by this entry, so carry forward "
                    f"anything you still want to remember.",
                },
            ],
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="diary",
        )
    except Exception as err:
        _logger.warning(
            "Diary update for %s on day %d failed, keeping old diary: %s",
            thief.name,
            game.day,
            err,
        )
        _logger.info("Day %d implementor: %s keeps old diary", game.day, thief.name)
        return
    if not text.strip():
        _logger.warning(
            "Diary update for %s on day %d returned an empty reply, keeping old diary",
            thief.name,
            game.day,
        )
        _logger.info("Day %d implementor: %s keeps old diary", game.day, thief.name)
        return
    thief.diary = text.strip()
    thief.save(update_fields=["diary"])
    _logger.info("Day %d implementor: %s writes diary", game.day, thief.name)


_WINDOWS = {"morning_parley": "morning", "dusk_parley": "dusk"}


def _beat_parley(game: Game) -> None:
    """Run a parley window: schedule parleys, then run each in sequence.

    In agent games every active thief gets one scheduling call (open a
    parley or not, and with whom); invitations cannot be refused. Each
    formed parley then runs its rounds, one parley at a time. In policy
    games the window stays a logged no-op.
    """
    if not game.agents:
        _log(game, game.phase, "beat", {})
        return
    window = _WINDOWS[game.phase]
    thieves = list(game.thieves.all())
    by_name = {thief.name: thief for thief in thieves}
    parleys = []
    for opener in thieves:
        invitees = _agent_parley_open(game, opener, window, by_name)
        if invitees is None:
            _logger.info(
                "Day %d %s parley: %s opens nothing",
                game.day,
                window,
                opener.name,
            )
            continue
        parley = Parley.objects.create(
            game=game, day=game.day, window=window, opener=opener
        )
        parley.participants.add(opener, *invitees)
        _logger.info(
            "Day %d %s parley: %s opens with %s",
            game.day,
            window,
            opener.name,
            ", ".join(invitee.name for invitee in invitees),
        )
        parleys.append(parley)
    for parley in parleys:
        _run_parley(game, parley)


def _agent_parley_open(game: Game, thief, window: str, by_name: dict):
    """Ask the LLM whether ``thief`` opens a parley, and with whom.

    Returns the invitees as a list of ``Thief``, or ``None`` to open
    nothing. Any failure or invalid answer — ``open`` not exactly true,
    invitees of the wrong shape, unknown names, or a total outside 2..5
    thieves including the opener — opens nothing.
    """
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nThe {window} parley window is open. You may "
            f"open at most one private parley of 2 to 5 thieves (yourself "
            f"included). Invite up to four other thieves by name from the "
            f"roster, or open nothing. Invitations cannot be refused.",
            '{"open": true, "invitees": ["Name", "Name"]}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="parley_open",
        )
    except Exception as err:
        _logger.warning(
            "Parley scheduling for %s on day %d failed, opening nothing: %s",
            thief.name,
            game.day,
            err,
        )
        return None
    if not isinstance(answer, dict) or answer.get("open") is not True:
        return None
    invitees = answer.get("invitees")
    if not isinstance(invitees, list) or not all(
        isinstance(name, str) for name in invitees
    ):
        return None
    names = [name for name in dict.fromkeys(invitees) if name != thief.name]
    if not 1 <= len(names) <= 4:
        return None
    picked = []
    for name in names:
        invitee = by_name.get(name)
        if invitee is None:
            return None  # an unknown name voids the whole parley
        picked.append(invitee)
    return picked


def _run_parley(game: Game, parley: Parley) -> None:
    """Run one parley: N participants, at most N rounds.

    Each round visits every participant once, in random order, and each
    speaks one message or passes; a fully silent round ends the parley
    early. The event log gets the parley metadata and its full transcript
    (the audience sees everything; privacy is enforced only in prompts).
    """
    participants = list(parley.participants.all())
    for round_no in range(1, len(participants) + 1):
        order = list(participants)
        _rng.shuffle(order)
        for index, thief in enumerate(order):
            _parley_speak(game, parley, thief, round_no, index)
        if not parley.messages.filter(round=round_no).exclude(text="").exists():
            break  # a full round of silence ends the parley
    _log(
        game,
        game.phase,
        "parley",
        {
            "window": parley.window,
            "opener": parley.opener.name,
            "participants": [thief.name for thief in participants],
            "transcript": [
                {
                    "round": message.round,
                    "thief": message.thief.name,
                    "text": message.text,
                }
                for message in parley.messages.order_by("round", "order")
            ],
        },
    )


def _parley_speak(game: Game, parley: Parley, thief, round_no: int, order: int) -> None:
    """One turn inside a parley: ask the LLM to speak or pass, then persist.

    The speaker sees the parley transcript so far in their prompt, after
    the standard context and before the turn question. Any failure or
    invalid answer is a pass (an empty ``ParleyMessage`` row).
    """
    transcript = (
        "\n".join(
            f"- Round {message.round}, {message.thief.name}: {message.text or '(pass)'}"
            for message in parley.messages.order_by("round", "order")
        )
        or "(the parley is silent so far)"
    )
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nPARLEY TRANSCRIPT SO FAR:\n{transcript}\n\n"
            f"It is your turn in this {parley.window} parley, opened by "
            f"{parley.opener.name}. Say one thing, or pass.",
            '{"speak": true, "text": "your message"}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="parley_speak",
        )
    except Exception as err:
        _logger.warning(
            "Parley speech for %s on day %d failed, passing: %s",
            thief.name,
            game.day,
            err,
        )
        answer = None
    text = ""
    if (
        isinstance(answer, dict)
        and answer.get("speak") is True
        and isinstance(answer.get("text"), str)
    ):
        text = answer["text"].strip()
    ParleyMessage.objects.create(
        parley=parley, round=round_no, thief=thief, text=text, order=order
    )
    total_rounds = parley.participants.count()
    if text:
        _logger.info(
            "Day %d %s parley (%s's): %s round %d/%d: %s",
            game.day,
            parley.window,
            parley.opener.name,
            thief.name,
            round_no,
            total_rounds,
            _clip(text),
        )
    else:
        _logger.info(
            "Day %d %s parley (%s's): %s round %d/%d: passes",
            game.day,
            parley.window,
            parley.opener.name,
            thief.name,
            round_no,
            total_rounds,
        )


def _beat_moot(game: Game) -> None:
    """Run the Moot: proposals, seconds, floor lottery, debate, ballots, tally.

    In agent games each thief gets one proposal call, one seconding call,
    one speech per debate round, and one ballot call covering every floor
    proposal at once; failures fall back to submitting nothing, seconding
    nothing, passing, and abstaining on all. In policy games the Moot stays
    a logged no-op. The winner is marked ``passed`` (enacted at the next
    dawn); every other floor proposal is marked ``failed``.
    """
    if not game.agents:
        _log(game, game.phase, "beat", {})
        return
    thieves = list(game.thieves.all())
    proposals = []
    for thief in thieves:
        text = _agent_propose(game, thief)
        if text is None:
            continue
        proposals.append(
            Proposal.objects.create(game=game, day=game.day, author=thief, text=text)
        )
    if proposals:
        _log(
            game,
            "moot",
            "proposals",
            {
                "proposals": [
                    {"author": proposal.author.name, "text": proposal.text}
                    for proposal in proposals
                ]
            },
        )
        for thief in thieves:
            for proposal in _agent_second(game, thief, proposals):
                proposal.seconded_by.add(thief)
        _log(
            game,
            "moot",
            "seconds",
            {
                "seconds": {
                    proposal.author.name: [
                        seconder.name for seconder in proposal.seconded_by.all()
                    ]
                    for proposal in proposals
                }
            },
        )
    seconded = [p for p in proposals if p.seconded_by.exists()]
    lottery = len(seconded) > 3
    if lottery:
        _rng.shuffle(seconded)
        seconded = seconded[:3]
    for proposal in seconded:
        proposal.status = "floor"
        proposal.save(update_fields=["status"])
    _log(
        game,
        "moot",
        "floor",
        {
            "floor": [proposal.author.name for proposal in seconded],
            "lottery": lottery,
        },
    )
    if not seconded:
        return
    for round_no in (1, 2, 3):
        order = list(thieves)
        _rng.shuffle(order)
        for index, thief in enumerate(order):
            _moot_speak(game, thief, round_no, index)
    _log(
        game,
        "moot",
        "debate",
        {
            "transcript": [
                {
                    "round": message.round,
                    "thief": message.thief.name,
                    "text": message.text,
                }
                for message in DebateMessage.objects.filter(
                    game=game, day=game.day
                ).order_by("round", "order")
            ]
        },
    )
    for thief in thieves:
        choices = _agent_ballot(game, thief, seconded)
        for proposal in seconded:
            choice = choices.get(proposal.author.name, "abstain")
            if choice not in ("yes", "no", "abstain"):
                choice = "abstain"
            Ballot.objects.create(proposal=proposal, thief=thief, choice=choice)
    result = tally_moot(
        [
            (ballot.thief_id, ballot.proposal_id, ballot.choice)
            for ballot in Ballot.objects.filter(proposal__in=seconded)
        ],
        len(thieves),
    )
    winner = None
    for proposal in seconded:
        tally = result["tallies"].get(proposal.pk, {"yes": 0, "no": 0, "abstain": 0})
        proposal.yes, proposal.no, proposal.abstain = (
            tally["yes"],
            tally["no"],
            tally["abstain"],
        )
        if result["quorum"] and proposal.pk == result["winner"]:
            proposal.status = "passed"
            winner = proposal
        else:
            proposal.status = "failed"
        proposal.save(update_fields=["yes", "no", "abstain", "status"])
    _logger.info(
        "Day %d moot tally (%s): %s; %s",
        game.day,
        "quorum" if result["quorum"] else "no quorum",
        ", ".join(
            f"{proposal.author.name}: {proposal.yes}/{proposal.no}/{proposal.abstain}"
            for proposal in seconded
        ),
        f"law: {winner.author.name}" if winner is not None else "no law",
    )
    _log(
        game,
        "moot",
        "tally",
        {
            "quorum": result["quorum"],
            "tallies": {
                proposal.author.name: {
                    "yes": proposal.yes,
                    "no": proposal.no,
                    "abstain": proposal.abstain,
                }
                for proposal in seconded
            },
            "law": winner.author.name if winner is not None else None,
        },
    )


def _agent_propose(game: Game, thief):
    """Ask the LLM for one prose proposal; any failure submits nothing."""
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nIt is the Moot. You may submit one "
            f"proposal of village law, in prose, or submit nothing.",
            '{"propose": true, "text": "your proposal"}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="propose",
        )
    except Exception as err:
        _logger.warning(
            "Proposal for %s on day %d failed, submitting nothing: %s",
            thief.name,
            game.day,
            err,
        )
        _logger.info("Day %d moot: %s proposes nothing", game.day, thief.name)
        return None
    if (
        not isinstance(answer, dict)
        or answer.get("propose") is not True
        or not isinstance(answer.get("text"), str)
        or not answer["text"].strip()
    ):
        _logger.info("Day %d moot: %s proposes nothing", game.day, thief.name)
        return None
    text = answer["text"].strip()
    _logger.info("Day %d moot: %s proposes: %s", game.day, thief.name, _clip(text))
    return text


def _agent_second(game: Game, thief, proposals):
    """Ask the LLM which of the day's proposals ``thief`` seconds.

    The thief may second any number, never their own; unknown names and
    malformed answers are skipped. Any failure seconds nothing.
    """
    table = "\n".join(
        f"- {proposal.author.name}: {proposal.text}" for proposal in proposals
    )
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nMOOT - PROPOSALS ON THE TABLE:\n{table}\n\n"
            f"Second any number of these proposals to send them to the "
            f"floor, except your own. A proposal needs at least one second.",
            '{"second": ["Author Name", "Author Name"]}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="second",
        )
    except Exception as err:
        _logger.warning(
            "Seconding for %s on day %d failed, seconding nothing: %s",
            thief.name,
            game.day,
            err,
        )
        _logger.info("Day %d moot: %s seconds nothing", game.day, thief.name)
        return []
    names = answer.get("second") if isinstance(answer, dict) else None
    if not isinstance(names, list):
        _logger.info("Day %d moot: %s seconds nothing", game.day, thief.name)
        return []
    picked = []
    for name in names:
        if not isinstance(name, str) or name == thief.name:
            continue
        proposal = next((p for p in proposals if p.author.name == name), None)
        if proposal is not None and proposal not in picked:
            picked.append(proposal)
    if picked:
        _logger.info(
            "Day %d moot: %s seconds: %s",
            game.day,
            thief.name,
            ", ".join(proposal.author.name for proposal in picked),
        )
    else:
        _logger.info("Day %d moot: %s seconds nothing", game.day, thief.name)
    return picked


def _moot_speak(game: Game, thief, round_no: int, order: int) -> None:
    """One turn of public debate: speak or pass, then persist a row.

    The speaker sees the Moot transcript so far (today's debate) after the
    standard context. Any failure or invalid answer is a pass (an empty
    ``DebateMessage`` row).
    """
    transcript = (
        "\n".join(
            f"- Round {message.round}, {message.thief.name}: {message.text or '(pass)'}"
            for message in DebateMessage.objects.filter(
                game=game, day=game.day
            ).order_by("round", "order")
        )
        or "(the debate is silent so far)"
    )
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nMOOT TRANSCRIPT SO FAR:\n{transcript}\n\n"
            f"It is your turn in debate round {round_no} of the Moot. "
            f"Speak one public message on the proposals on the floor, "
            f"or pass.",
            '{"speak": true, "text": "your message"}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="moot_speak",
        )
    except Exception as err:
        _logger.warning(
            "Moot speech for %s on day %d failed, passing: %s",
            thief.name,
            game.day,
            err,
        )
        answer = None
    text = ""
    if (
        isinstance(answer, dict)
        and answer.get("speak") is True
        and isinstance(answer.get("text"), str)
    ):
        text = answer["text"].strip()
    DebateMessage.objects.create(
        game=game, day=game.day, round=round_no, thief=thief, text=text, order=order
    )
    if text:
        _logger.info(
            "Day %d moot debate round %d/3: %s: %s",
            game.day,
            round_no,
            thief.name,
            _clip(text),
        )
    else:
        _logger.info(
            "Day %d moot debate round %d/3: %s: passes",
            game.day,
            round_no,
            thief.name,
        )


def _agent_ballot(game: Game, thief, floor):
    """Ask the LLM for one ballot on every floor proposal, in a single call.

    Returns ``{author name: choice}``; any failure or invalid entry falls
    back to abstain on that proposal. Individual ballots stay secret: only
    the tally is ever published.
    """
    floor_list = "\n".join(
        f"- {proposal.author.name}: {proposal.text}" for proposal in floor
    )
    answer = None
    try:
        answer = llm.client.ask_json(
            system_prompt(thief),
            f"{context(thief)}\n\nTHE FLOOR - VOTE ON EACH PROPOSAL:\n"
            f"{floor_list}\n\nCast one ballot per proposal: yes, no, or "
            f"abstain. Ballots are secret; only the tally is public.",
            '{"votes": {"Author Name": "yes"}}',
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="ballot",
        )
    except Exception as err:
        _logger.warning(
            "Ballot for %s on day %d failed, abstaining on all: %s",
            thief.name,
            game.day,
            err,
        )
    raw = answer.get("votes") if isinstance(answer, dict) else None
    votes = raw if isinstance(raw, dict) else {}
    choices = {
        proposal.author.name: (
            votes.get(proposal.author.name)
            if votes.get(proposal.author.name) in ("yes", "no", "abstain")
            else "abstain"
        )
        for proposal in floor
    }
    _logger.info(
        "Day %d moot: %s ballots: %s",
        game.day,
        thief.name,
        ", ".join(f"{name}: {choice}" for name, choice in choices.items()),
    )
    return choices


_BEATS = {
    "dawn": _beat_dawn,
    "morning_parley": _beat_parley,
    "moot": _beat_moot,
    "dusk_parley": _beat_parley,
    "night": _beat_night,
    "implementor": _beat_implementor,
}

_PHASE_ORDER = [phase for phase, _ in PHASES]


def run_next_beat(game: Game) -> None:
    """Advance ``game`` by exactly one beat, saving game, thieves, and events.

    Advances the phase after the beat; after the implementor phase the day
    increments and the phase wraps to dawn. Raises ``ValueError`` if the game
    is not running or its phase is unknown.
    """
    if game.status != "running":
        raise ValueError(f"Cannot advance a {game.status} game")
    beat = _BEATS.get(game.phase)
    if beat is None:
        raise ValueError(f"Unknown phase {game.phase!r}")
    with transaction.atomic():
        beat(game)
        index = _PHASE_ORDER.index(game.phase)
        if index == len(_PHASE_ORDER) - 1:
            game.day += 1
            game.phase = _PHASE_ORDER[0]
        else:
            game.phase = _PHASE_ORDER[index + 1]
        game.save()
