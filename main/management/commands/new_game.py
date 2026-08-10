from django.core.management.base import BaseCommand, CommandError

from main.content import GOALS, PERSONAS
from main.models import Game, Thief

TAKE_POLICY_RANGE = range(6)


class Command(BaseCommand):
    help = "Create a new game with 10 thieves and print its id."

    def add_arguments(self, parser):
        parser.add_argument(
            "--policies",
            default=None,
            help=(
                "Comma-separated take policies for the 10 thieves, e.g. "
                "2,2,2,2,2,2,2,2,5,5 (default: all 2)."
            ),
        )
        parser.add_argument(
            "--agents",
            action="store_true",
            help=(
                "Create an agent game: the 10 thieves are LLM personas from "
                "main/content.py; take_policy is not used."
            ),
        )

    def handle(self, *args, **options):
        if options["agents"]:
            if options["policies"] is not None:
                raise CommandError("--policies cannot be combined with --agents.")
            game = Game.objects.create(agents=True)
            for name, persona in PERSONAS:
                goal = GOALS[name]
                Thief.objects.create(
                    game=game,
                    name=name,
                    persona=persona,
                    goal=goal["text"],
                    goal_condition=goal["condition"],
                    goal_payout=goal["payout"],
                )
            self.stdout.write(
                f"Created agent game {game.pk} with 10 thieves "
                f"({', '.join(name for name, _ in PERSONAS)})"
            )
            return
        if options["policies"] is None:
            policies = [2] * 10
        else:
            try:
                policies = [int(p.strip()) for p in options["policies"].split(",")]
            except ValueError:
                raise CommandError("--policies must be comma-separated integers.")
            if len(policies) != 10:
                raise CommandError("--policies must contain exactly 10 values.")
            if any(p not in TAKE_POLICY_RANGE for p in policies):
                raise CommandError("Take policies must be integers between 0 and 5.")
        game = Game.objects.create()
        for index, policy in enumerate(policies, start=1):
            Thief.objects.create(game=game, name=f"Thief {index}", take_policy=policy)
        self.stdout.write(
            f"Created game {game.pk} with 10 thieves (policies {policies})"
        )
