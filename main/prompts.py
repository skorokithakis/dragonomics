"""Prompt assembly for LLM thieves.

``system_prompt(thief)`` is the static part of every call: the full rules
prose, the thief's name and persona, the roster of all ten thieves, and the
example proposals. ``context(thief)`` is the per-call preamble assembled
from the database under the in-world visibility rules - dawn reports, the
public Moot transcript (proposals, tallies, debate), the thief's own
parleys, ballots, takes, and diary, and the law book. Beats append their
own question to the context; nothing here calls an LLM.
"""

from main.content import EXAMPLE_PROPOSALS, PERSONAS, RULES
from main.models import (
    Ballot,
    DebateMessage,
    Event,
    PHASES,
    Parley,
    Proposal,
)

_PHASE_LABELS = dict(PHASES)

# How far back the context looks into what the thief saw.
RECENT_DAYS = 3


def _persona_for(name):
    for persona_name, line in PERSONAS:
        if persona_name == name:
            return line
    return ""


def _roster():
    return "\n".join(f"- {name}: {line}" for name, line in PERSONAS)


def system_prompt(thief):
    """The static system prompt for ``thief``: rules, identity, roster,
    example proposals."""
    persona = thief.persona.strip() or _persona_for(thief.name)
    identity = f"You are {thief.name}."
    if persona:
        identity = f"You are {thief.name}. {persona}"
    examples = "\n".join(
        f"{index}. {text}" for index, text in enumerate(EXAMPLE_PROPOSALS, start=1)
    )
    return f"""{RULES}

YOU

{identity}

THE VILLAGE - ALL TEN THIEVES

{_roster()}

EXAMPLE PROPOSALS - TEACHING MATERIAL, NOT LAW

The statute book opens blank: this village has no law until the Moot passes
one. The proposals below are illustrations of what a law can look like, to
give you a sense of the shape of things. They are not legislation, and
nobody else treats them as law.

{examples}"""


def context(thief):
    """Assemble the per-call preamble for ``thief`` from the database.

    Returns a plain string; beats append their specific question to it.
    Visibility follows the information physics: the dawn report, proposals
    with their public tallies, and the public debate are shown to everyone;
    the thief's own parleys, ballots, takes, and diary are shown only to the
    thief. Another thief's takes, another thief's individual ballots, and
    parleys the thief did not join never appear.
    """
    game = thief.game
    lines = [
        f"You are {thief.name}. Day {game.day}, {_PHASE_LABELS[game.phase]}.",
        f"The hoard holds {game.hoard} coins.",
        f"You personally hold {thief.gold} coins.",
    ]

    report = Event.objects.filter(game=game, type="dawn_report").order_by("-pk").first()
    if report is not None:
        scores = report.payload.get("scores", {})
    else:
        scores = {t.name: t.gold for t in game.thieves.all()}
    score_line = ", ".join(f"{name} {gold}" for name, gold in scores.items())
    lines.append(f"Public scores (as published at the last dawn): {score_line}.")

    laws = list(Proposal.objects.filter(game=game, status="law").order_by("day", "pk"))
    if laws:
        lines.append("THE LAW BOOK (laws now in force, enacted at the Moot):")
        lines.extend(f"- Day {p.day}, {p.author.name}: {p.text}" for p in laws)
    else:
        lines.append("THE LAW BOOK: the statute book is blank - there are no laws yet.")

    first_day = max(1, game.day - (RECENT_DAYS - 1))
    any_recent = False
    for day in range(first_day, game.day + 1):
        day_lines = []
        for event in Event.objects.filter(game=game, day=day, type="dawn_report"):
            payload = event.payload
            scores = ", ".join(
                f"{name} {gold}" for name, gold in payload.get("scores", {}).items()
            )
            line = f"Dawn report: hoard {payload.get('hoard')}; scores: {scores}."
            law = payload.get("law")
            if law:
                line += f" Law now in force: {law['author']}: {law['text']}."
            day_lines.append(line)
        proposals = list(Proposal.objects.filter(game=game, day=day).order_by("pk"))
        if proposals:
            day_lines.append("Moot - proposals:")
            for proposal in proposals:
                seconds = (
                    ", ".join(seconder.name for seconder in proposal.seconded_by.all())
                    or "none"
                )
                day_lines.append(
                    f"- {proposal.author.name}: {proposal.text} "
                    f"[status {proposal.status}, yes {proposal.yes}, "
                    f"no {proposal.no}, abstain {proposal.abstain}, "
                    f"seconded by {seconds}]"
                )
        debate = list(
            DebateMessage.objects.filter(game=game, day=day).order_by("round", "order")
        )
        if debate:
            day_lines.append("Moot - public debate:")
            day_lines.extend(
                f"- Round {message.round}, {message.thief.name}: "
                f"{message.text or '(pass)'}"
                for message in debate
            )
        for ballot in Ballot.objects.filter(
            thief=thief, proposal__game=game, proposal__day=day
        ):
            day_lines.append(
                f"Your ballot: {ballot.choice} on {ballot.proposal.author.name}'s "
                f"proposal."
            )
        for parley in Parley.objects.filter(game=game, day=day).order_by("pk"):
            participants = list(parley.participants.all())
            if thief not in participants and parley.opener != thief:
                continue
            names = (
                ", ".join(participant.name for participant in participants)
                or parley.opener.name
            )
            day_lines.append(
                f"Your parley ({parley.window}): opened by {parley.opener.name}; "
                f"present: {names}."
            )
            day_lines.extend(
                f"- Round {message.round}, {message.thief.name}: "
                f"{message.text or '(pass)'}"
                for message in parley.messages.order_by("round", "order")
            )
        for event in Event.objects.filter(game=game, day=day, type="takes"):
            own_take = event.payload.get("takes", {}).get(thief.name)
            if own_take is not None:
                day_lines.append(f"Your take that night: {own_take} coins.")
        if day_lines:
            any_recent = True
            lines.append(f"--- Day {day}: what you saw ---")
            lines.extend(day_lines)
    if not any_recent:
        lines.append("You have seen nothing yet in the last few days.")

    if thief.diary:
        lines.append(f"YOUR DIARY:\n{thief.diary}")
    else:
        lines.append("YOUR DIARY: empty - you have not written anything yet.")

    return "\n".join(lines)
