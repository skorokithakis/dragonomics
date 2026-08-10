from django.core.management.base import BaseCommand, CommandError

from main.beats import run_next_beat
from main.models import Game, PHASES

_PHASE_LABELS = dict(PHASES)


def _summarize(game: Game) -> str:
    """Return a short human-readable account of the game's most recent beat."""
    last = game.events.order_by("-pk").first()
    events = game.events.filter(day=last.day, phase=last.phase).order_by("pk")
    bits = [f"Day {last.day}: {_PHASE_LABELS[last.phase]}"]
    for event in events:
        payload = event.payload
        if event.type == "dawn_report":
            bit = f"hoard {payload['hoard']}"
            law = payload.get("law")
            if law:
                bit += f"; law in force: {law['author']}"
            bits.append(bit)
        elif event.type == "proposals":
            bits.append(f"{len(payload['proposals'])} proposals")
        elif event.type == "floor":
            floor = ", ".join(payload["floor"]) or "none"
            lottery = " (lottery)" if payload.get("lottery") else ""
            bits.append(f"floor: {floor}{lottery}")
        elif event.type == "tally":
            tallies = ", ".join(
                f"{name} yes {t['yes']} no {t['no']} ab {t['abstain']}"
                for name, t in payload["tallies"].items()
            )
            law = payload.get("law")
            bits.append(f"tally {tallies}; {f'law: {law}' if law else 'no law'}")
        elif event.type == "parley":
            participants = [
                name for name in payload["participants"] if name != payload["opener"]
            ]
            with_ = f" with {', '.join(participants)}" if participants else ""
            messages = sum(
                1 for m in payload.get("transcript", []) if m.get("text")
            )
            bits.append(
                f"{payload['window']} parley: {payload['opener']}{with_}"
                f" ({messages} messages)"
            )
        elif event.type == "rescue_roll":
            if payload["roll"] is None:
                bits.append("day 40 hard cap - rescued, run over")
            elif payload["rescued"]:
                bits.append(f"end roll {payload['roll']:.2f} — rescued, run over")
            else:
                bits.append(f"end roll {payload['roll']:.2f}, no rescue")
        elif event.type == "run_ended":
            bits.append(f"GAME OVER ({payload['reason']} on day {payload['day']})")
        elif event.type == "final_ranking":
            ranking = ", ".join(
                f"{entry['name']} {entry['gold']}" for entry in payload["ranking"]
            )
            bits.append(f"ranking: {ranking}")
        elif event.type == "takes":
            takes = ", ".join(
                f"{name} +{amount}"
                for name, amount in payload["takes"].items()
                if amount
            )
            bits.append(f"takes {takes}; hoard {payload['hoard_after']}")
        elif event.type == "wake_roll":
            outcome = "WAKE!" if payload["woke"] else "no wake"
            bits.append(f"wake roll {payload['roll']:.2f} ({outcome})")
        elif event.type == "regrow":
            bits.append(f"regrow {payload['hoard_before']} -> {payload['hoard_after']}")
        elif event.type == "wake":
            losses = ", ".join(
                f"{name} -{loss}" for name, loss in payload["losses"].items() if loss
            )
            bits.append(
                f"first wake: losses {losses}; hoard {payload['hoard_after']} (rage)"
            )
        elif event.type == "rage_night":
            bits.append(f"rage night, hoard {payload['hoard']}")
    return "; ".join(bits)


class Command(BaseCommand):
    help = "Advance a game by one beat and print what happened."

    def add_arguments(self, parser):
        parser.add_argument(
            "game_id",
            nargs="?",
            type=int,
            help="Game id to advance (defaults to the latest running game).",
        )

    def handle(self, *args, **options):
        if options["game_id"] is not None:
            game = Game.objects.filter(pk=options["game_id"]).first()
            if game is None:
                raise CommandError(f"No game with id {options['game_id']}.")
        else:
            game = Game.objects.filter(status="running").order_by("-pk").first()
            if game is None:
                raise CommandError("No running game; create one with new_game first.")
        try:
            run_next_beat(game)
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(_summarize(game))
