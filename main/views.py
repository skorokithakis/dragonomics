from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from main.models import (
    Ballot,
    DebateMessage,
    Event,
    Game,
    Parley,
    ParleyMessage,
    Proposal,
    RuleSet,
)


def index(request):
    games = Game.objects.order_by("-created", "-pk")
    return render(request, "index.html", {"games": games})


def game_day(request, pk, day=None):
    """One day of one game, grouped by phase.

    Without a ``day`` the latest day (``game.day``) is shown; days outside
    ``1..game.day`` are a 404. The page is read-only and audience-facing:
    parleys come from ``Parley`` rows, the Moot from ``Proposal``/``Ballot``/
    ``DebateMessage`` rows plus the ``floor`` and ``tally`` events, and dawn
    and night from ``Event`` rows.
    """
    game = get_object_or_404(Game, pk=pk)
    if day is None:
        day = game.day
    if day < 1 or day > game.day:
        raise Http404(f"Game {game.pk} has no day {day}")

    dawn = list(Event.objects.filter(game=game, day=day, phase="dawn").order_by("id"))
    night = list(Event.objects.filter(game=game, day=day, phase="night").order_by("id"))
    # The implementor section is audience-plane: the compile outcome (events)
    # and the law source that takes effect from the next dawn (RuleSet).
    implementor = list(
        Event.objects.filter(game=game, day=day, phase="implementor").order_by("id")
    )
    next_ruleset = (
        RuleSet.objects.filter(game=game, day=day + 1).order_by("-pk").first()
    )

    parleys = {}
    for window in ("morning", "dusk"):
        parleys[window] = list(
            Parley.objects.filter(game=game, day=day, window=window)
            .order_by("id")
            .select_related("opener")
            .prefetch_related(
                Prefetch(
                    "messages",
                    queryset=ParleyMessage.objects.select_related("thief").order_by(
                        "round", "order"
                    ),
                ),
                "participants",
            )
        )

    proposals = list(
        Proposal.objects.filter(game=game, day=day)
        .select_related("author")
        .prefetch_related(
            "seconded_by",
            Prefetch(
                "ballots",
                queryset=Ballot.objects.select_related("thief").order_by("id"),
            ),
        )
    )
    floor_event = Event.objects.filter(
        game=game, day=day, phase="moot", type="floor"
    ).first()
    tally_event = Event.objects.filter(
        game=game, day=day, phase="moot", type="tally"
    ).first()
    debate = list(
        DebateMessage.objects.filter(game=game, day=day)
        .select_related("thief")
        .order_by("round", "order")
    )
    floor_names = set(floor_event.payload.get("floor") or []) if floor_event else set()

    # The law's visible acts — announcements, malfunctions, and score moves
    # — can land in any phase. Dawn and night render them inline from their
    # own event lists; the moot and implementor sections get theirs here.
    law_acts = list(
        Event.objects.filter(
            game=game, day=day, type__in=("announce", "rule_error", "score_adjust")
        ).order_by("id")
    )
    moot_law_acts = [event for event in law_acts if event.phase == "moot"]
    implementor_law_acts = [event for event in law_acts if event.phase == "implementor"]

    context = {
        "game": game,
        "day": day,
        "prev_day": day - 1 if day > 1 else None,
        "next_day": day + 1 if day < game.day else None,
        "dawn": dawn,
        "night": night,
        "implementor": implementor,
        "next_ruleset": next_ruleset,
        "parleys": parleys,
        "proposals": proposals,
        "floor_event": floor_event,
        "tally_event": tally_event,
        "debate": debate,
        "floor_names": floor_names,
        "moot_law_acts": moot_law_acts,
        "implementor_law_acts": implementor_law_acts,
    }
    return render(request, "game_day.html", context)
