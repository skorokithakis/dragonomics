from django.core.management.base import BaseCommand, CommandError

from main.models import Game, Proposal, RuleSet


class Command(BaseCommand):
    help = (
        "Enact a rule set for a game: store the Lua source from a file as "
        "the law in force from a given day, snapshotting the game scratchpad."
    )

    def add_arguments(self, parser):
        parser.add_argument("game_id", type=int)
        parser.add_argument("lua_file")
        parser.add_argument(
            "--day",
            type=int,
            default=None,
            help="First day the rules are in force (default: game.day + 1).",
        )
        parser.add_argument(
            "--proposal",
            type=int,
            default=None,
            help="The proposal this rule set implements.",
        )

    def handle(self, *args, **options):
        try:
            game = Game.objects.get(pk=options["game_id"])
        except Game.DoesNotExist:
            raise CommandError(f"No game with id {options['game_id']}.")
        try:
            with open(options["lua_file"]) as f:
                code = f.read()
        except OSError as exc:
            raise CommandError(f"Cannot read {options['lua_file']}: {exc}")
        proposal = None
        if options["proposal"] is not None:
            try:
                proposal = Proposal.objects.get(
                    pk=options["proposal"], game=game
                )
            except Proposal.DoesNotExist:
                raise CommandError(
                    f"No proposal with id {options['proposal']} for game {game.pk}."
                )
        day = options["day"] if options["day"] is not None else game.day + 1
        ruleset = RuleSet.objects.create(
            game=game,
            day=day,
            code=code,
            proposal=proposal,
            scratchpad=dict(game.scratchpad),
        )
        self.stdout.write(
            f"Enacted ruleset {ruleset.pk} for game {game.pk} from day {ruleset.day}."
        )
