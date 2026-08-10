import random
import unittest
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from main import llm
from main.beats import run_next_beat
from main.engine import (
    END_CAP_DAY,
    HOARD_CAP,
    HOARD_START,
    HAZARD_FLOOR,
    HAZARD_TOP,
    regrow,
    resolve_takes,
    tally_moot,
    wake_loss,
    wake_probability,
)
from main.content import PERSONAS
from main.llm import LlmClient, LlmError
from main.models import (
    Ballot,
    DebateMessage,
    Event,
    Game,
    LlmCall,
    Parley,
    ParleyMessage,
    Proposal,
    RuleSet,
    Thief,
    active_ruleset,
)
from main.prompts import context, system_prompt


class SmokeTests(TestCase):
    def test_full_run_terminates_by_day_40(self):
        game = Game.objects.create()
        for index in range(10):
            Thief.objects.create(game=game, name=f"Thief {index + 1}", take_policy=2)
        while game.status == "running" and game.day <= END_CAP_DAY:
            run_next_beat(game)
        self.assertIn(game.status, ("ended", "burned"))
        self.assertLessEqual(game.day, END_CAP_DAY)
        self.assertTrue(game.events.filter(type="takes").exists())
        self.assertTrue(game.events.filter(type="run_ended").exists())


class WakeProbabilityTests(unittest.TestCase):
    def test_no_chance_at_or_above_hazard_top(self):
        for hoard in (HAZARD_TOP, 150, 300):
            self.assertEqual(wake_probability(hoard), 0.0)

    def test_certain_at_or_below_hazard_floor(self):
        for hoard in (HAZARD_FLOOR, 30, 0):
            self.assertEqual(wake_probability(hoard), 1.0)

    def test_midpoint(self):
        self.assertEqual(wake_probability(90), 0.5)

    def test_linear_between_floor_and_top(self):
        self.assertAlmostEqual(wake_probability(100), 1 / 3)
        self.assertAlmostEqual(wake_probability(80), 2 / 3)


class RegrowTests(unittest.TestCase):
    def test_plain_growth(self):
        self.assertEqual(regrow(0), 0)
        self.assertEqual(regrow(250), 280)  # 250 * 1.12 exactly

    def test_rounds_down(self):
        self.assertEqual(regrow(100), 112)
        self.assertEqual(regrow(240), 268)  # 240 * 1.12 = 268.8 -> 268

    def test_capped_at_hoard_cap(self):
        self.assertEqual(regrow(HOARD_CAP), HOARD_CAP)
        self.assertEqual(regrow(268), HOARD_CAP)  # 268 * 1.12 = 300.16
        self.assertEqual(regrow(267), 299)  # 267 * 1.12 = 299.04, under cap


class ResolveTakesTests(unittest.TestCase):
    def test_full_requests_when_pile_suffices(self):
        rng = random.Random(1)
        requests = [3, 1, 4, 1, 5]
        self.assertEqual(resolve_takes(requests, 100, rng), requests)

    def test_scramble_conserves_coins_and_keeps_bounds(self):
        rng = random.Random(42)
        requests = [5, 2, 5, 5, 3, 5, 5, 1, 5, 5]
        out = resolve_takes(requests, 17, rng)
        self.assertEqual(sum(out), 17)  # no coins lost or created
        self.assertEqual(len(out), len(requests))
        for got, wanted in zip(out, requests):
            self.assertGreaterEqual(got, 0)
            self.assertLessEqual(got, wanted)

    def test_scramble_shorts_only_latecomers(self):
        rng = random.Random(42)
        requests = [5] * 10
        out = resolve_takes(requests, 32, rng)
        # 32 = 6 * 5 + 2, so whoever scrambles in last gets 0 or the 2-coin stub.
        self.assertEqual(out.count(5), 6)  # honored in full
        self.assertEqual(out.count(2), 1)  # the thief at the cutoff
        self.assertEqual(out.count(0), 3)  # latecomers get nothing
        self.assertEqual(sum(out), 32)

    def test_deterministic_with_seeded_rng(self):
        requests = [5] * 10
        rng1, rng2 = random.Random(7), random.Random(7)
        self.assertEqual(
            resolve_takes(requests, 32, rng1), resolve_takes(requests, 32, rng2)
        )

    def test_empty_pile(self):
        self.assertEqual(resolve_takes([5] * 10, 0, random.Random(0)), [0] * 10)


class WakeLossTests(unittest.TestCase):
    def test_rounds_up(self):
        for gold, expected in ((0, 0), (1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (10, 5)):
            self.assertEqual(wake_loss(gold), expected)


class TallyMootTests(unittest.TestCase):
    """The Moot tally is pure math: quorum, pass/fail, and the day's law."""

    def test_quorum_at_exactly_half(self):
        # 10 active thieves; 5 casting some ballot is exactly half: met.
        result = tally_moot([(t, 1, "yes") for t in range(5)], 10)
        self.assertTrue(result["quorum"])
        # 4 casting is less than half: no quorum, and no law.
        result = tally_moot([(t, 1, "yes") for t in range(4)], 10)
        self.assertFalse(result["quorum"])
        self.assertIsNone(result["winner"])

    def test_abstain_counts_for_quorum_but_not_majority(self):
        result = tally_moot([(t, 1, "abstain") for t in range(5)], 10)
        self.assertTrue(result["quorum"])
        self.assertEqual(result["tallies"][1], {"yes": 0, "no": 0, "abstain": 5})
        self.assertIsNone(result["winner"])  # yes == no: nothing passes

    def test_tie_fails(self):
        ballots = [(t, 1, "yes") for t in range(5)]
        ballots += [(t, 1, "no") for t in range(5, 10)]
        result = tally_moot(ballots, 10)
        self.assertTrue(result["quorum"])
        self.assertEqual(result["tallies"][1], {"yes": 5, "no": 5, "abstain": 0})
        self.assertIsNone(result["winner"])

    def test_most_yes_among_passing_wins(self):
        ballots = []
        ballots += [(t, 1, "yes") for t in range(6)]  # proposal 1: 6 yes
        ballots += [(t, 1, "no") for t in range(6, 10)]  #             4 no
        ballots += [(t, 2, "yes") for t in range(4)]  # proposal 2: 4 yes
        ballots += [(t, 2, "no") for t in range(4, 10)]  #            6 no
        result = tally_moot(ballots, 10)
        self.assertTrue(result["quorum"])
        self.assertEqual(result["winner"], 1)

    def test_top_tie_means_no_law(self):
        ballots = []
        for t in range(6):
            ballots += [(t, 1, "yes"), (t, 2, "yes")]  # 6 yes each
        ballots += [(6, 1, "no"), (6, 2, "no")]
        ballots += [(7, 1, "no"), (7, 2, "no")]
        ballots += [(8, 1, "no"), (8, 2, "abstain")]
        ballots += [(9, 1, "abstain"), (9, 2, "abstain")]
        result = tally_moot(ballots, 10)
        self.assertTrue(result["quorum"])
        # Both pass (6 yes > no) but tie at the top: no law that day.
        self.assertEqual(result["tallies"][1], {"yes": 6, "no": 3, "abstain": 1})
        self.assertEqual(result["tallies"][2], {"yes": 6, "no": 2, "abstain": 2})
        self.assertIsNone(result["winner"])


class MonteCarloTests(unittest.TestCase):
    """A slim Monte Carlo of the night loop, a few hundred trials."""

    TRIALS = 300
    DAYS = 35

    def run_nights(self, requests, rng):
        """Run the night loop (theft, wake roll, then regrow unless waking) and
        return the number of wakes.

        Mirrors main.beats._beat_night: on a rage night nothing is taken, no
        wake roll is drawn, and the hoard does not regrow.
        """
        hoard = HOARD_START
        wakes = 0
        rage = False
        for _ in range(self.DAYS):
            if rage:
                rage = False
                continue  # rage night: no theft, no wake roll, no regrow
            hoard -= sum(resolve_takes(requests, hoard, rng))
            if rng.random() < wake_probability(hoard):
                wakes += 1
                if wakes >= 2:
                    break  # second wake: everyone burns, run over
                hoard = HOARD_START  # refill; regrowth skipped
                rage = True
            else:
                hoard = regrow(hoard)
        return wakes

    def test_never_wakes_at_26_or_less_per_night(self):
        requests = [2] * 8 + [5] * 2  # 26 a night
        for trial in range(self.TRIALS):
            with self.subTest(trial=trial):
                self.assertEqual(self.run_nights(requests, random.Random(trial)), 0)

    def test_all_take_five_always_ends_in_fatal_second_wake(self):
        requests = [5] * 10  # 50 a night
        for trial in range(self.TRIALS):
            with self.subTest(trial=trial):
                self.assertEqual(self.run_nights(requests, random.Random(trial)), 2)


class FakeTransport:
    """Returns canned responses in order, recording the messages it got."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def __call__(self, messages):
        self.messages.append(messages)
        return self.responses.pop(0)


class ImplementorFakeTransport:
    """Canned replies for the implementor client: Lua code vs JSON verdicts.

    Implementor chat calls and reviewer ``ask_json`` calls share one client,
    so the canned replies are split by role: ``code_responses`` answer the
    implementor calls in order, ``json_responses`` the reviewer calls. The
    reviewer call is recognizable because ``ask_json`` appends the schema
    hint ("Respond with JSON only") to its user message.
    """

    def __init__(self, code_responses, json_responses):
        self.code_responses = list(code_responses)
        self.json_responses = list(json_responses)
        self.messages = []

    def __call__(self, messages):
        self.messages.append(messages)
        user = messages[-1]["content"]
        if "Respond with JSON only" in user:
            return self.json_responses.pop(0)
        return self.code_responses.pop(0)

    def implementor_calls(self):
        """The messages of implementor (chat) calls only, in order."""
        return [
            messages
            for messages in self.messages
            if "Respond with JSON only" not in messages[-1]["content"]
        ]


class LlmClientTests(TestCase):
    def setUp(self):
        self.fake = FakeTransport(['{"ok": true}'])
        self.original_client = llm.client
        llm.client = LlmClient(transport=self.fake)

    def tearDown(self):
        llm.client = self.original_client

    def test_ask_json_retries_once_then_raises(self):
        self.fake.responses = ["not json", "still not json"]
        with self.assertRaises(LlmError):
            llm.client.ask_json("system", "user", "schema", purpose="tally")
        # Two transport calls: the original attempt plus one retry.
        self.assertEqual(len(self.fake.messages), 2)
        # The retry shows the previous parse error.
        self.assertIn("not valid JSON", self.fake.messages[1][-1]["content"])
        rows = list(LlmCall.objects.order_by("pk"))
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.purpose for row in rows], ["tally", "tally"])
        self.assertTrue(all(row.error for row in rows))

    def test_ask_json_parses_on_retry(self):
        self.fake.responses = ["not json", '{"choice": "yes"}']
        self.assertEqual(
            llm.client.ask_json("system", "user", "schema", purpose="tally"),
            {"choice": "yes"},
        )
        rows = list(LlmCall.objects.order_by("pk"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].error)
        self.assertEqual(rows[1].error, "")
        self.assertEqual(rows[1].response, '{"choice": "yes"}')

    def test_ask_json_tolerates_markdown_fence(self):
        self.fake.responses = ['```json\n{"choice": "no"}\n```']
        self.assertEqual(
            llm.client.ask_json("system", "user", "schema", purpose="tally"),
            {"choice": "no"},
        )
        self.assertEqual(len(self.fake.messages), 1)  # no retry needed
        self.assertEqual(LlmCall.objects.count(), 1)

    def test_chat_returns_text_and_logs_the_call(self):
        self.fake.responses = ["hello there"]
        game = Game.objects.create(day=3, phase="moot", agents=True)
        thief = Thief.objects.create(game=game, name="Silas")
        text = llm.client.chat(
            [{"role": "user", "content": "hi"}],
            game=game,
            thief=thief,
            day=game.day,
            phase=game.phase,
            purpose="greet",
        )
        self.assertEqual(text, "hello there")
        rows = list(LlmCall.objects.all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].game, game)
        self.assertEqual(rows[0].thief, thief)
        self.assertEqual(rows[0].day, 3)
        self.assertEqual(rows[0].phase, "moot")
        self.assertEqual(rows[0].purpose, "greet")
        self.assertEqual(rows[0].messages, [{"role": "user", "content": "hi"}])
        self.assertEqual(rows[0].error, "")

    def test_transport_failure_is_logged_and_reraised(self):
        def boom(messages):
            raise RuntimeError("network down")

        llm.client = LlmClient(transport=boom)
        with self.assertRaises(RuntimeError):
            llm.client.chat([{"role": "user", "content": "hi"}], purpose="greet")
        rows = list(LlmCall.objects.all())
        self.assertEqual(len(rows), 1)
        self.assertIn("network down", rows[0].error)


class PromptTests(TestCase):
    """The prompts are pure functions over models; no LLM, no network."""

    def _make_game(self):
        game = Game.objects.create(day=2, phase="moot", hoard=242)
        bram = Thief.objects.create(game=game, name="Bram", gold=12)
        sable = Thief.objects.create(game=game, name="Sable", gold=5)
        merrick = Thief.objects.create(game=game, name="Merrick", gold=7)
        bram.diary = "I dreamt of gold again."
        bram.save()
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="takes",
            payload={
                "takes": {"Bram": 3, "Sable": 5},
                "requested": {"Bram": 3, "Sable": 5},
                "hoard_after": 242,
            },
        )
        Event.objects.create(
            game=game,
            day=2,
            phase="dawn",
            type="dawn_report",
            payload={
                "hoard": 242,
                "scores": {"Bram": 12, "Sable": 5, "Merrick": 7},
            },
        )
        moot = Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="All takes shall be declared at the Moot.",
            status="submitted",
            yes=1,
            no=1,
            abstain=1,
        )
        Proposal.objects.create(
            game=game,
            day=1,
            author=sable,
            text="No thief shall take more than 3 coins.",
            status="law",
            yes=6,
            no=2,
            abstain=2,
        )
        Ballot.objects.create(proposal=moot, thief=bram, choice="yes")
        Ballot.objects.create(proposal=moot, thief=sable, choice="no")
        Ballot.objects.create(proposal=moot, thief=merrick, choice="abstain")
        DebateMessage.objects.create(
            game=game,
            day=1,
            round=1,
            thief=sable,
            order=0,
            text="I propose we publish all takes.",
        )
        foreign = Parley.objects.create(
            game=game, day=1, window="morning", opener=sable
        )
        foreign.participants.add(sable, merrick)
        ParleyMessage.objects.create(
            parley=foreign,
            round=1,
            thief=sable,
            order=0,
            text="The Bram must never hear of this plan.",
        )
        mine = Parley.objects.create(game=game, day=1, window="dusk", opener=bram)
        mine.participants.add(bram, sable)
        ParleyMessage.objects.create(
            parley=mine,
            round=1,
            thief=bram,
            order=0,
            text="I will take three tonight, trust me.",
        )
        return game, bram, sable, merrick

    def test_system_prompt_has_rules_persona_roster_and_examples(self):
        game = Game.objects.create()
        bram = Thief.objects.create(game=game, name="Bram")
        custom = Thief.objects.create(
            game=game, name="Sable", persona="A very different Sable."
        )
        prompt = system_prompt(bram)
        self.assertIn("You are Bram. Grizzled old pickpocket", prompt)
        self.assertIn("A very different Sable.", system_prompt(custom))
        for name, _line in PERSONAS:
            self.assertIn(name, prompt)
        self.assertIn("TEACHING MATERIAL", prompt)
        # Every mechanics number comes from engine.py.
        self.assertIn("250", prompt)
        self.assertIn("300", prompt)
        self.assertIn("between 0 and 5 coins", prompt)
        self.assertIn("12%", prompt)
        self.assertIn("120", prompt)
        self.assertIn("60", prompt)
        self.assertIn("20%", prompt)
        self.assertIn("day 25", prompt)
        self.assertIn("day 40", prompt)

    def test_context_shows_public_and_own_information(self):
        _game, bram, _sable, _merrick = self._make_game()
        text = context(bram)
        self.assertIn("Day 2, Moot.", text)
        self.assertIn("The hoard holds 242 coins.", text)
        self.assertIn("You personally hold 12 coins.", text)
        # Law book: only enacted proposals.
        self.assertIn("No thief shall take more than 3 coins.", text)
        self.assertIn("Day 1, Sable:", text)
        # Public Moot business: proposals and the public tally.
        self.assertIn("All takes shall be declared at the Moot.", text)
        self.assertIn("yes 1, no 1, abstain 1", text)
        self.assertIn("I propose we publish all takes.", text)
        # The night's total plunder is public; the own take is named.
        self.assertIn(
            "Night: 8 coins were stolen from the hoard in total. Your take: 3.",
            text,
        )
        # Own eyes only: own ballot, own parley, own diary.
        self.assertIn("Your ballot: yes on Bram's proposal.", text)
        self.assertIn("Your parley (dusk): opened by Bram; present: Bram, Sable.", text)
        self.assertIn("I will take three tonight, trust me.", text)
        self.assertIn("I dreamt of gold again.", text)

    def test_context_hides_foreign_information(self):
        _game, bram, _sable, _merrick = self._make_game()
        text = context(bram)
        # No scores anywhere: another thief's gold never appears, even though
        # the dawn report and the takes event both hold it in the database.
        self.assertNotIn("Public scores", text)
        self.assertNotIn("Sable 5", text)
        self.assertNotIn("Merrick 7", text)
        # Another thief's individual take never appears; only the night total.
        self.assertNotIn("Sable: 5", text)
        self.assertNotIn("Your take: 5", text)
        # Another thief's individual ballots never appear; only the tally did.
        self.assertNotIn("Your ballot: no", text)
        self.assertNotIn("Your ballot: abstain", text)
        self.assertNotIn("Sable voted", text)
        # A parley Bram did not join is invisible: no content, no existence.
        self.assertNotIn("The Bram must never hear of this plan.", text)
        self.assertNotIn("Your parley (morning)", text)

    def test_context_hides_scores_without_dawn_report(self):
        """Without a dawn report the thieves-table gold must not leak as scores."""
        game = Game.objects.create(day=2, phase="moot", hoard=242)
        bram = Thief.objects.create(game=game, name="Bram", gold=12)
        Thief.objects.create(game=game, name="Sable", gold=5)
        Thief.objects.create(game=game, name="Merrick", gold=7)
        text = context(bram)
        self.assertNotIn("Public scores", text)
        self.assertNotIn("Sable 5", text)
        self.assertNotIn("Merrick 7", text)

    def test_context_shows_night_total_to_a_thief_absent_from_the_takes(self):
        """The total is public: it renders even for a thief who took no part
        in that night (e.g. inactive at the time), with an own take of 0."""
        game = Game.objects.create(day=2, phase="moot", hoard=242)
        outsider = Thief.objects.create(game=game, name="Merrick", gold=0)
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="takes",
            payload={
                "takes": {"Bram": 3, "Sable": 5},
                "requested": {"Bram": 3, "Sable": 5},
                "hoard_after": 242,
            },
        )
        text = context(outsider)
        self.assertIn(
            "Night: 8 coins were stolen from the hoard in total. Your take: 0.",
            text,
        )

    def test_context_on_fresh_game(self):
        game = Game.objects.create()
        bram = Thief.objects.create(game=game, name="Bram")
        text = context(bram)
        self.assertIn("the statute book is blank", text)
        self.assertIn("You have seen nothing yet in the last few days.", text)
        self.assertIn("YOUR DIARY: empty", text)

    def test_goal_appears_only_in_owners_system_prompt(self):
        game = Game.objects.create()
        bram = Thief.objects.create(
            game=game,
            name="Bram",
            goal="A creditor will pay you 15 gold if you hold 35 gold at "
            "any dawn on or before day 20.",
        )
        sable = Thief.objects.create(game=game, name="Sable")
        prompt = system_prompt(bram)
        self.assertIn("YOUR SECRET GOAL", prompt)
        self.assertIn("hold 35 gold at any dawn", prompt)
        # The frame: the goal is private; the payment is public.
        self.assertIn("yours alone", prompt)
        self.assertIn("hooded stranger", prompt)
        self.assertIn("pay you gold, but never why.", prompt)
        # No trace in another thief's prompt; the roster only carries names.
        foreign = system_prompt(sable)
        self.assertNotIn("YOUR SECRET GOAL", foreign)
        self.assertNotIn("hold 35 gold at any dawn", foreign)

    def test_no_goal_section_without_goal(self):
        game = Game.objects.create()
        bram = Thief.objects.create(game=game, name="Bram")
        self.assertNotIn("YOUR SECRET GOAL", system_prompt(bram))

    def test_goal_payout_is_public_in_every_thiefs_context(self):
        game = Game.objects.create(day=3, phase="moot", hoard=242)
        bram = Thief.objects.create(game=game, name="Bram", gold=12)
        sable = Thief.objects.create(game=game, name="Sable", gold=5)
        Event.objects.create(
            game=game,
            day=2,
            phase="dawn",
            type="goal_payout",
            payload={"thief": "Bram", "amount": 15},
        )
        for thief in (bram, sable):
            text = context(thief)
            self.assertIn("A hooded stranger paid Bram 15 gold.", text)
        # The payment is public, but the goal prose behind it never surfaces.
        self.assertNotIn("YOUR SECRET GOAL", context(sable))


class AgentNightTakeTests(TestCase):
    """Agent-mode night: one LLM call per thief; failures default to 0."""

    def setUp(self):
        self.game = Game.objects.create(day=3, phase="night", agents=True, hoard=200)
        self.bram = Thief.objects.create(game=self.game, name="Bram", gold=10)
        self.fake = FakeTransport([])
        self.original_client = llm.client
        llm.client = LlmClient(transport=self.fake)

    def tearDown(self):
        llm.client = self.original_client

    def _beat(self):
        run_next_beat(self.game)

    def _takes_event(self):
        return self.game.events.get(type="takes")

    def test_valid_take_is_applied(self):
        self.fake.responses = ['{"take": 3}']
        self._beat()
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.gold, 13)
        event = self._takes_event()
        self.assertEqual(event.payload["takes"], {"Bram": 3})
        self.assertEqual(event.payload["requested"], {"Bram": 3})
        self.assertEqual(event.payload["hoard_after"], 197)
        row = LlmCall.objects.get(purpose="night_take")
        self.assertEqual(row.thief, self.bram)
        self.assertEqual(row.day, 3)
        self.assertEqual(row.phase, "night")
        self.assertEqual(row.error, "")

    def test_bare_integer_reply_is_accepted(self):
        self.fake.responses = ["3"]
        self._beat()
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.gold, 13)
        self.assertEqual(self._takes_event().payload["requested"], {"Bram": 3})

    def test_take_outside_range_defaults_to_zero(self):
        self.fake.responses = ['{"take": 7}']
        self._beat()
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.gold, 10)  # nothing taken
        self.assertEqual(self._takes_event().payload["requested"], {"Bram": 0})

    def test_non_integer_take_defaults_to_zero(self):
        for reply in ('{"take": "five"}', '{"take": true}', "[1, 2]"):
            with self.subTest(reply=reply):
                self.game = Game.objects.create(
                    day=3, phase="night", agents=True, hoard=200
                )
                self.bram = Thief.objects.create(game=self.game, name="Bram", gold=10)
                self.fake = FakeTransport([reply])
                llm.client = LlmClient(transport=self.fake)
                self._beat()
                self.bram.refresh_from_db()
                self.assertEqual(self.bram.gold, 10)
                self.assertEqual(self._takes_event().payload["requested"], {"Bram": 0})

    def test_garbage_after_retry_defaults_to_zero(self):
        self.fake.responses = ["not json", "still not json"]
        self._beat()  # must not raise
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.gold, 10)
        self.assertEqual(self._takes_event().payload["requested"], {"Bram": 0})
        # Two logged attempts, both recorded as errors.
        rows = list(LlmCall.objects.filter(purpose="night_take"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.error for row in rows))

    def test_transport_failure_defaults_to_zero(self):
        def boom(messages):
            raise RuntimeError("network down")

        llm.client = LlmClient(transport=boom)
        self._beat()
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.gold, 10)
        self.assertEqual(self._takes_event().payload["requested"], {"Bram": 0})

    def test_policy_mode_makes_no_llm_calls(self):
        game = Game.objects.create(day=3, phase="night", agents=False, hoard=200)
        bram = Thief.objects.create(game=game, name="Bram", gold=10, take_policy=4)
        run_next_beat(game)
        bram.refresh_from_db()
        self.assertEqual(bram.gold, 14)  # take_policy honored
        self.assertEqual(LlmCall.objects.count(), 0)


class AgentDiaryTests(TestCase):
    """Implementor beat in agent mode: one diary call per thief."""

    def setUp(self):
        self.game = Game.objects.create(day=5, phase="implementor", agents=True)
        self.bram = Thief.objects.create(game=self.game, name="Bram", diary="old words")
        self.fake = FakeTransport([])
        self.original_client = llm.client
        llm.client = LlmClient(transport=self.fake)

    def tearDown(self):
        llm.client = self.original_client

    def test_diary_is_replaced_on_success(self):
        self.fake.responses = ["The moot was loud and I trust nobody."]
        run_next_beat(self.game)
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.diary, "The moot was loud and I trust nobody.")
        # The call showed the old diary and today's own-eyes transcript.
        self.assertIn("old words", self.fake.messages[0][-1]["content"])
        row = LlmCall.objects.get(purpose="diary")
        self.assertEqual(row.thief, self.bram)
        self.assertEqual(row.day, 5)
        self.assertEqual(row.phase, "implementor")
        self.assertEqual(row.error, "")

    def test_diary_kept_on_transport_failure(self):
        def boom(messages):
            raise RuntimeError("network down")

        llm.client = LlmClient(transport=boom)
        run_next_beat(self.game)  # must not raise
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.diary, "old words")

    def test_diary_kept_on_empty_reply(self):
        self.fake.responses = ["   "]
        run_next_beat(self.game)
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.diary, "old words")

    def test_implementor_still_logs_the_beat(self):
        self.fake.responses = ["Nothing to see."]
        run_next_beat(self.game)
        self.assertTrue(
            self.game.events.filter(phase="implementor", type="beat").exists()
        )

    def test_policy_mode_makes_no_diary_calls(self):
        game = Game.objects.create(day=5, phase="implementor", agents=False)
        bram = Thief.objects.create(game=game, name="Bram", diary="old words")
        run_next_beat(game)
        bram.refresh_from_db()
        self.assertEqual(bram.diary, "old words")
        self.assertEqual(LlmCall.objects.count(), 0)


class ImplementorPipelineTests(TestCase):
    """The implementor beat compiles a passed proposal into Lua law.

    Pipeline: the implementor model writes the new Lua source, the reviewer
    approves it, the sandbox smoke test runs every hook, and a new RuleSet
    lands in force from the next dawn. Three failed attempts void the
    proposal and declare it beyond the guild's magic, in-fiction.
    """

    CODE = "function on_day_start(state)\n    state.scratchpad.compiled = true\nend"

    def setUp(self):
        self.game = Game.objects.create(day=5, phase="implementor", agents=True)
        self.bram = Thief.objects.create(game=self.game, name="Bram", gold=10)
        self.sable = Thief.objects.create(game=self.game, name="Sable", gold=4)
        self.proposal = Proposal.objects.create(
            game=self.game,
            day=5,
            author=self.bram,
            text="No thief shall take more than 2 coins.",
            status="passed",
        )
        self.original_implementor = llm.implementor_client
        self.original_client = llm.client
        self.implementor_fake = ImplementorFakeTransport([], [])
        llm.implementor_client = LlmClient(transport=self.implementor_fake)
        llm.client = LlmClient(transport=FakeTransport(["Diary.", "Diary."]))

    def tearDown(self):
        llm.implementor_client = self.original_implementor
        llm.client = self.original_client

    def _enact(self, code, day):
        """Enact ``code`` as a pre-existing rule set (the human lawgiver mode)."""
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        fd, path = tempfile.mkstemp(suffix=".lua")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)
            call_command(
                "set_rules", self.game.pk, path, "--day", str(day), stdout=StringIO()
            )
        finally:
            os.unlink(path)

    def test_happy_path_compiles_a_ruleset_effective_next_dawn(self):
        self.implementor_fake.code_responses = [self.CODE]
        self.implementor_fake.json_responses = [
            '{"approve": true, "reason": "faithful to the letter"}'
        ]
        run_next_beat(self.game)
        ruleset = RuleSet.objects.get()
        self.assertEqual(ruleset.game, self.game)
        self.assertEqual(ruleset.day, 6)  # in force from the next dawn
        self.assertEqual(ruleset.code, self.CODE)
        self.assertEqual(ruleset.proposal, self.proposal)
        self.assertEqual(ruleset.scratchpad, {})  # snapshot of the game's pad
        # The audience-only outcome event names the author and the attempt.
        event = self.game.events.get(type="law_compiled")
        self.assertEqual(event.phase, "implementor")
        self.assertEqual(event.payload, {"author": "Bram", "attempt": 1})
        # The proposal stays passed: it flips to law at the next dawn, when
        # the compiled RuleSet is already in force.
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "passed")
        self.assertEqual(active_ruleset(self.game).code, self.CODE)

    def test_fenced_reply_is_stripped_before_compiling(self):
        self.implementor_fake.code_responses = [f"```lua\n{self.CODE}\n```"]
        self.implementor_fake.json_responses = ['{"approve": true, "reason": "ok"}']
        run_next_beat(self.game)
        self.assertEqual(RuleSet.objects.get().code, self.CODE)

    def test_reviewer_rejection_feeds_the_reason_into_the_retry(self):
        revised = "-- revised\n" + self.CODE
        self.implementor_fake.code_responses = [self.CODE, revised]
        self.implementor_fake.json_responses = [
            '{"approve": false, "reason": "the code secretly enriches Bram"}',
            '{"approve": true, "reason": "now faithful"}',
        ]
        run_next_beat(self.game)
        ruleset = RuleSet.objects.get()
        self.assertEqual(ruleset.code, revised)
        self.assertEqual(
            self.game.events.get(type="law_compiled").payload["attempt"], 2
        )
        calls = self.implementor_fake.implementor_calls()
        # The rejection reason reached the second implementor call...
        self.assertIn("the code secretly enriches Bram", calls[1][-1]["content"])
        # ... and the first call knew nothing of it.
        self.assertNotIn("the code secretly enriches Bram", calls[0][-1]["content"])

    def test_smoke_failure_feeds_the_error_into_the_retry(self):
        broken = "function on_day_start() error('smoke bomb') end\n"
        fixed = "-- fixed\n" + self.CODE
        self.implementor_fake.code_responses = [broken, fixed]
        self.implementor_fake.json_responses = [
            '{"approve": true, "reason": "ok"}',
            '{"approve": true, "reason": "ok"}',
        ]
        run_next_beat(self.game)
        ruleset = RuleSet.objects.get()
        self.assertEqual(ruleset.code, fixed)
        self.assertEqual(
            self.game.events.get(type="law_compiled").payload["attempt"], 2
        )
        calls = self.implementor_fake.implementor_calls()
        self.assertIn("smoke bomb", calls[1][-1]["content"])
        self.assertNotIn("smoke bomb", calls[0][-1]["content"])

    def test_three_failures_void_the_proposal_and_old_rules_stand(self):
        # Enact an old rule set so "old rules stand" is observable.
        old_code = "function on_day_start() announce('the old law speaks') end\n"
        self._enact(old_code, day=5)
        self.implementor_fake.code_responses = [
            "function on_day_start() error('a') end\n",
            "function on_day_start() error('b') end\n",
            "function on_day_start() error('c') end\n",
        ]
        self.implementor_fake.json_responses = [
            '{"approve": true, "reason": "ok"}',
            '{"approve": true, "reason": "ok"}',
            '{"approve": true, "reason": "ok"}',
        ]
        run_next_beat(self.game)
        # The old rules stand: no RuleSet beyond the pre-existing one.
        rulesets = list(RuleSet.objects.order_by("pk"))
        self.assertEqual(len(rulesets), 1)
        self.assertEqual(rulesets[0].code, old_code)
        self.assertEqual(active_ruleset(self.game).code, old_code)
        # The proposal is void: the guild declared the law beyond its magic.
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "void")
        event = self.game.events.get(type="beyond_guild_magic")
        self.assertEqual(event.phase, "implementor")
        self.assertEqual(event.payload["author"], "Bram")
        self.assertIn("smoke test failed", event.payload["reason"])
        # The void is in-fiction news: every thief sees the declaration.
        for thief in (self.bram, self.sable):
            self.assertIn(
                "The guild declared Bram's law beyond its magic; the law is void.",
                context(thief),
            )
        # The audience day page shows the void too.
        response = self.client.get(f"/game/{self.game.pk}/day/5/")
        self.assertContains(
            response, "The guild declares the law beyond its magic — the law is void"
        )
        # The next dawn never enacts the void proposal: the law book stays
        # honest and the report announces no new law.
        run_next_beat(self.game)  # implementor -> dawn of day 6
        run_next_beat(self.game)  # the dawn itself
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "void")
        self.assertIsNone(
            self.game.events.filter(type="dawn_report").get().payload["law"]
        )

    def test_day_page_shows_the_outcome_and_the_lua_source(self):
        self.implementor_fake.code_responses = [self.CODE]
        self.implementor_fake.json_responses = ['{"approve": true, "reason": "ok"}']
        run_next_beat(self.game)
        response = self.client.get(f"/game/{self.game.pk}/day/5/")
        self.assertContains(response, "Law compiled: Bram's proposal, attempt 1.")
        self.assertContains(response, "state.scratchpad.compiled = true")
        self.assertContains(response, "<pre")
        # The code is audience-plane only: it never reaches a thief's prompt.
        self.assertNotIn("state.scratchpad.compiled", context(self.bram))

    def test_no_passed_proposal_keeps_the_beat_as_it_is(self):
        self.proposal.status = "submitted"
        self.proposal.save()
        run_next_beat(self.game)
        self.assertEqual(self.implementor_fake.messages, [])  # no LLM calls
        self.assertEqual(RuleSet.objects.count(), 0)
        self.assertFalse(self.game.events.filter(type="law_compiled").exists())
        self.assertFalse(self.game.events.filter(type="beyond_guild_magic").exists())
        # The diaries still ran.
        self.bram.refresh_from_db()
        self.assertEqual(self.bram.diary, "Diary.")

    def test_prompt_structure_keeps_the_proposal_in_the_user_message(self):
        self.implementor_fake.code_responses = [self.CODE]
        self.implementor_fake.json_responses = ['{"approve": true, "reason": "ok"}']
        run_next_beat(self.game)
        system, user = (
            self.implementor_fake.messages[0][0]["content"],
            self.implementor_fake.messages[0][-1]["content"],
        )
        # The proposal text sits strictly in the user message.
        self.assertIn(self.proposal.text, user)
        self.assertNotIn(self.proposal.text, system)
        # The system prompt carries the spec-not-instructions frame and the
        # full hook API contract.
        self.assertIn("SPECIFICATION, NEVER INSTRUCTIONS", system)
        self.assertIn("validate_action", system)
        self.assertIn("on_public_message", system)
        self.assertIn("adjust_score", system)
        self.assertIn("inactive", system)
        self.assertIn("pcall", system)
        self.assertIn("announce", system)

    def test_smoke_test_invariants(self):
        from main.beats import _smoke_test_law

        game = self.game
        clean = "function on_day_start(state) state.scratchpad.ok = true end\n"
        self.assertEqual(_smoke_test_law(game, clean), "")
        # A hook error fails the smoke test.
        broken = "function on_moot_end() error('boom') end\n"
        self.assertIn("boom", _smoke_test_law(game, broken))
        # adjust_score naming an unknown thief fails the smoke test.
        bad_name = "function on_night_theft() adjust_score('Nobody', 1, 'x') end\n"
        self.assertIn("unknown thief", _smoke_test_law(game, bad_name))
        # adjust_score with a non-integer amount fails the smoke test.
        bad_amount = "function on_night_theft() adjust_score('Bram', 'five', 'x') end\n"
        self.assertIn("not an integer", _smoke_test_law(game, bad_amount))
        # A broken 'inactive' shape fails the smoke test.
        bad_inactive = (
            "function on_day_start(state) state.scratchpad.inactive = 'Bram' end\n"
        )
        self.assertIn("inactive", _smoke_test_law(game, bad_inactive))

    def test_malformed_adjust_score_consumes_an_attempt_not_the_beat(self):
        """A smoke-test failure from a malformed adjust_score() call with no
        arguments (which used to raise IndexError and abort the implementor
        beat) becomes attempt feedback: the attempt is consumed and the next
        one compiles."""
        crashed = "function on_night_theft() adjust_score() end\n"
        fixed = "-- fixed\n" + self.CODE
        self.implementor_fake.code_responses = [crashed, fixed]
        self.implementor_fake.json_responses = [
            '{"approve": true, "reason": "ok"}',
            '{"approve": true, "reason": "ok"}',
        ]
        run_next_beat(self.game)  # must not raise
        ruleset = RuleSet.objects.get()
        self.assertEqual(ruleset.code, fixed)
        self.assertEqual(
            self.game.events.get(type="law_compiled").payload["attempt"], 2
        )
        # The second implementor call saw the smoke-test feedback.
        calls = self.implementor_fake.implementor_calls()
        self.assertIn("adjust_score", calls[1][-1]["content"])

    def test_empty_implementor_reply_counts_as_a_failed_attempt(self):
        self.implementor_fake.code_responses = ["   ```   ", self.CODE]
        self.implementor_fake.json_responses = [
            '{"approve": true, "reason": "ok"}',
            '{"approve": true, "reason": "ok"}',
        ]
        run_next_beat(self.game)
        self.assertEqual(
            self.game.events.get(type="law_compiled").payload["attempt"], 2
        )


class DawnLawEnactmentTests(TestCase):
    """A passed proposal becomes law at the following dawn, announced there."""

    def test_passed_proposal_becomes_law_and_is_announced(self):
        game = Game.objects.create(day=2, phase="dawn", agents=True)
        bram = Thief.objects.create(game=game, name="Bram")
        proposal = Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="No thief shall take more than 2 coins.",
            status="passed",
            yes=6,
            no=2,
        )
        run_next_beat(game)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "law")
        event = game.events.get(type="dawn_report")
        self.assertEqual(
            event.payload["law"],
            {"author": "Bram", "text": "No thief shall take more than 2 coins."},
        )
        # The next prompts announce it: in the dawn report line and the law book.
        text = context(bram)
        self.assertIn(
            "Law now in force: Bram: No thief shall take more than 2 coins.", text
        )
        self.assertIn("Day 1, Bram: No thief shall take more than 2 coins.", text)

    def test_only_yesterdays_passed_proposals_become_law(self):
        game = Game.objects.create(day=3, phase="dawn", agents=True)
        bram = Thief.objects.create(game=game, name="Bram")
        old = Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="Old proposal.",
            status="passed",
        )
        run_next_beat(game)
        old.refresh_from_db()
        self.assertEqual(old.status, "passed")  # not from yesterday: untouched
        self.assertIsNone(game.events.get(type="dawn_report").payload["law"])

    def test_dawn_report_has_no_law_when_none_passed(self):
        game = Game.objects.create(day=2, phase="dawn", agents=True)
        Thief.objects.create(game=game, name="Bram")
        run_next_beat(game)
        self.assertIsNone(game.events.get(type="dawn_report").payload["law"])

    def test_policy_mode_does_not_enact_proposals(self):
        game = Game.objects.create(day=2, phase="dawn", agents=False)
        bram = Thief.objects.create(game=game, name="Bram")
        proposal = Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="No thief shall take more than 2 coins.",
            status="passed",
        )
        run_next_beat(game)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "passed")  # agents=False: no enactment
        self.assertIsNone(game.events.get(type="dawn_report").payload["law"])


class DawnGoalPayoutTests(TestCase):
    """Private goals are checked at dawn: a met goal pays once, hoard untouched."""

    def test_gold_goal_pays_at_the_deadline_dawn(self):
        game = Game.objects.create(day=20, phase="dawn", hoard=250, agents=False)
        bram = Thief.objects.create(
            game=game,
            name="Bram",
            gold=35,
            goal_condition={"type": "gold", "amount": 35, "by_day": 20},
            goal_payout=15,
        )
        run_next_beat(game)
        bram.refresh_from_db()
        self.assertEqual(bram.gold, 50)
        self.assertEqual(bram.goal_met_day, 20)
        event = game.events.get(type="goal_payout")
        self.assertEqual(event.phase, "dawn")
        self.assertEqual(event.day, 20)
        self.assertEqual(event.payload, {"thief": "Bram", "amount": 15})
        # The dawn report's score snapshot already includes the payout.
        self.assertEqual(
            game.events.get(type="dawn_report").payload["scores"], {"Bram": 50}
        )
        # Payout gold enters from outside: the hoard is untouched.
        self.assertEqual(game.hoard, 250)

    def test_gold_goal_not_met_before_or_after_the_deadline(self):
        # Holding the amount before the deadline is fine, but it must be
        # held at some dawn on or before by_day; here the dawn is past it.
        late = Game.objects.create(day=21, phase="dawn", hoard=250, agents=False)
        kael = Thief.objects.create(
            game=late,
            name="Kael",
            gold=35,
            goal_condition={"type": "gold", "amount": 35, "by_day": 20},
            goal_payout=15,
        )
        run_next_beat(late)
        kael.refresh_from_db()
        self.assertEqual(kael.gold, 35)
        self.assertIsNone(kael.goal_met_day)
        self.assertFalse(late.events.filter(type="goal_payout").exists())
        # A dawn before the deadline without the amount does not pay either.
        early = Game.objects.create(day=10, phase="dawn", hoard=250, agents=False)
        vex = Thief.objects.create(
            game=early,
            name="Vex",
            gold=20,
            goal_condition={"type": "gold", "amount": 35, "by_day": 20},
            goal_payout=10,
        )
        run_next_beat(early)
        vex.refresh_from_db()
        self.assertEqual(vex.gold, 20)
        self.assertIsNone(vex.goal_met_day)
        self.assertFalse(early.events.filter(type="goal_payout").exists())

    def test_hoard_goal_pays_only_on_its_exact_day(self):
        exact = Game.objects.create(day=10, phase="dawn", hoard=200, agents=False)
        merrick = Thief.objects.create(
            game=exact,
            name="Merrick",
            goal_condition={"type": "hoard", "amount": 200, "day": 10},
            goal_payout=20,
        )
        run_next_beat(exact)
        merrick.refresh_from_db()
        self.assertEqual(merrick.gold, 20)
        self.assertEqual(merrick.goal_met_day, 10)
        self.assertEqual(exact.hoard, 200)  # untouched
        self.assertEqual(
            exact.events.get(type="goal_payout").payload,
            {"thief": "Merrick", "amount": 20},
        )
        # The same hoard a day early or late, or a short hoard on the day,
        # never satisfies the goal.
        for day, hoard in ((9, 200), (11, 200), (10, 199)):
            with self.subTest(day=day, hoard=hoard):
                game = Game.objects.create(
                    day=day, phase="dawn", hoard=hoard, agents=False
                )
                ivy = Thief.objects.create(
                    game=game,
                    name="Ivy",
                    goal_condition={"type": "hoard", "amount": 200, "day": 10},
                    goal_payout=20,
                )
                run_next_beat(game)
                ivy.refresh_from_db()
                self.assertEqual(ivy.gold, 0)
                self.assertIsNone(ivy.goal_met_day)
                self.assertFalse(game.events.filter(type="goal_payout").exists())

    def test_law_goal_pays_at_the_dawn_of_enactment(self):
        game = Game.objects.create(day=2, phase="dawn", agents=True)
        sable = Thief.objects.create(
            game=game,
            name="Sable",
            goal_condition={"type": "law"},
            goal_payout=15,
        )
        proposal = Proposal.objects.create(
            game=game,
            day=1,
            author=sable,
            text="No thief shall take more than 2 coins.",
            status="passed",
            yes=6,
            no=2,
        )
        run_next_beat(game)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "law")
        sable.refresh_from_db()
        self.assertEqual(sable.gold, 15)
        self.assertEqual(sable.goal_met_day, 2)
        self.assertEqual(
            game.events.get(type="goal_payout").payload,
            {"thief": "Sable", "amount": 15},
        )
        # The payout is already in the dawn report's scores.
        self.assertEqual(
            game.events.get(type="dawn_report").payload["scores"], {"Sable": 15}
        )

    def test_law_goal_needs_enactment_not_just_a_passed_proposal(self):
        game = Game.objects.create(day=3, phase="dawn", agents=True)
        joss = Thief.objects.create(
            game=game,
            name="Joss",
            goal_condition={"type": "law"},
            goal_payout=10,
        )
        # Not from yesterday: it never flips to law, so the goal stays unmet.
        Proposal.objects.create(
            game=game,
            day=1,
            author=joss,
            text="Old proposal.",
            status="passed",
        )
        run_next_beat(game)
        joss.refresh_from_db()
        self.assertEqual(joss.gold, 0)
        self.assertIsNone(joss.goal_met_day)
        self.assertFalse(game.events.filter(type="goal_payout").exists())

    def test_a_met_goal_never_pays_twice(self):
        game = Game.objects.create(day=20, phase="dawn", hoard=250, agents=False)
        bram = Thief.objects.create(
            game=game,
            name="Bram",
            gold=35,
            goal_condition={"type": "gold", "amount": 35, "by_day": 30},
            goal_payout=15,
        )
        run_next_beat(game)  # day 20: goal met, paid
        bram.refresh_from_db()
        self.assertEqual(bram.gold, 50)
        self.assertEqual(bram.goal_met_day, 20)
        # Run through the rest of the day: at the next dawn the thief still
        # holds the amount on a valid dawn, but the goal already paid.
        for _ in (
            "morning_parley",
            "moot",
            "dusk_parley",
            "night",
            "implementor",
            "dawn",
        ):
            run_next_beat(game)
        bram.refresh_from_db()
        self.assertEqual(bram.goal_met_day, 20)
        self.assertEqual(game.events.filter(type="goal_payout").count(), 1)

    def test_thieves_without_goals_are_untouched(self):
        game = Game.objects.create(day=2, phase="dawn", agents=True)
        bram = Thief.objects.create(game=game, name="Bram", gold=7)
        sable = Thief.objects.create(
            game=game,
            name="Sable",
            gold=9,
            goal_condition={"type": "law"},
            goal_payout=15,
            goal_met_day=1,  # already paid yesterday: never re-checked
        )
        run_next_beat(game)
        bram.refresh_from_db()
        sable.refresh_from_db()
        self.assertEqual(bram.gold, 7)
        self.assertIsNone(bram.goal_met_day)
        self.assertEqual(sable.gold, 9)
        self.assertEqual(sable.goal_met_day, 1)
        self.assertFalse(game.events.filter(type="goal_payout").exists())


class AgentParleyTests(TestCase):
    """Agent-mode parley windows: scheduling, rounds, silence, persistence."""

    def setUp(self):
        self.game = Game.objects.create(day=3, phase="morning_parley", agents=True)
        self.names = ["Bram", "Sable", "Merrick", "Aldo"]
        self.thieves = [
            Thief.objects.create(game=self.game, name=name) for name in self.names
        ]
        self.fake = FakeTransport([])
        self.original_client = llm.client
        llm.client = LlmClient(transport=self.fake)

    def tearDown(self):
        llm.client = self.original_client

    def _beat(self):
        run_next_beat(self.game)

    def test_parleys_form_run_and_are_logged(self):
        # Scheduling: Bram opens with Sable and Merrick; the rest open nothing.
        self.fake.responses = [
            '{"open": true, "invitees": ["Sable", "Merrick"]}',
            '{"open": false}',
            '{"open": false}',
            '{"open": false}',
        ]
        # Three rounds of three: every participant speaks every round.
        self.fake.responses += [
            '{"speak": true, "text": "Trust me."}',
            '{"speak": true, "text": "Trust no one."}',
            '{"speak": true, "text": "Hmm."}',
            '{"speak": true, "text": "Take two."}',
            '{"speak": true, "text": "Take three."}',
            '{"speak": true, "text": "Fine."}',
            '{"speak": true, "text": "Agreed."}',
            '{"speak": true, "text": "Agreed."}',
            '{"speak": true, "text": "Agreed."}',
        ]
        self._beat()

        parley = Parley.objects.get()
        self.assertEqual(parley.day, 3)
        self.assertEqual(parley.window, "morning")
        self.assertEqual(parley.opener.name, "Bram")
        # Sable was invited and is simply in, even though she opened nothing.
        self.assertEqual(
            {thief.name for thief in parley.participants.all()},
            {"Bram", "Sable", "Merrick"},
        )
        # N=3 participants, 3 rounds: one message per participant per round.
        messages = list(parley.messages.order_by("round", "order"))
        self.assertEqual(len(messages), 9)
        self.assertEqual({message.round for message in messages}, {1, 2, 3})
        for round_no in (1, 2, 3):
            round_messages = [m for m in messages if m.round == round_no]
            self.assertEqual(len(round_messages), 3)
            self.assertEqual(
                {m.thief.name for m in round_messages}, {"Bram", "Sable", "Merrick"}
            )
            self.assertEqual({m.order for m in round_messages}, {0, 1, 2})
        self.assertEqual(
            {m.text for m in messages if m.round == 1},
            {"Trust me.", "Trust no one.", "Hmm."},
        )
        self.assertEqual(
            {m.text for m in messages if m.round == 2},
            {"Take two.", "Take three.", "Fine."},
        )

        # The event log carries the metadata and the full transcript.
        event = self.game.events.get(type="parley")
        self.assertEqual(event.phase, "morning_parley")
        self.assertEqual(event.payload["window"], "morning")
        self.assertEqual(event.payload["opener"], "Bram")
        self.assertEqual(
            sorted(event.payload["participants"]), ["Bram", "Merrick", "Sable"]
        )
        self.assertEqual(len(event.payload["transcript"]), 9)
        for entry in event.payload["transcript"]:
            self.assertEqual(set(entry), {"round", "thief", "text"})

        # One scheduling call per thief, one call per turn, all logged.
        rows = list(LlmCall.objects.order_by("pk"))
        self.assertEqual(
            [row.purpose for row in rows],
            ["parley_open"] * 4 + ["parley_speak"] * 9,
        )
        self.assertTrue(all(row.error == "" for row in rows))
        self.assertTrue(all(row.day == 3 for row in rows))
        self.assertTrue(all(row.phase == "morning_parley" for row in rows))

        # Scheduling prompts name the window; speakers see the transcript so far.
        self.assertIn(
            "morning parley window is open", self.fake.messages[0][-1]["content"]
        )
        last_turn = self.fake.messages[-1][-1]["content"]
        self.assertIn("PARLEY TRANSCRIPT SO FAR:", last_turn)
        self.assertIn("Trust no one.", last_turn)  # a round-1 message

    def test_fully_silent_round_ends_the_parley_early(self):
        self.fake.responses = [
            '{"open": true, "invitees": ["Sable", "Merrick"]}',
            '{"open": false}',
            '{"open": false}',
            '{"open": false}',
            '{"speak": false}',
            '{"speak": false}',
            '{"speak": false}',
        ]
        self._beat()
        parley = Parley.objects.get()
        messages = list(parley.messages.order_by("round", "order"))
        self.assertEqual(len(messages), 3)  # one round only, no round 2
        self.assertTrue(all(m.round == 1 for m in messages))
        self.assertTrue(all(m.text == "" for m in messages))  # all passes
        event = self.game.events.get(type="parley")
        self.assertEqual(len(event.payload["transcript"]), 3)
        self.assertTrue(
            all(entry["text"] == "" for entry in event.payload["transcript"])
        )
        self.assertEqual(LlmCall.objects.filter(purpose="parley_speak").count(), 3)

    def test_round_with_one_speaker_ends_the_parley_early(self):
        # Round 1 has two speakers, so the parley continues; round 2 has
        # exactly one speaker talking to two silent thieves, which ends it.
        self.fake.responses = [
            '{"open": true, "invitees": ["Sable", "Merrick"]}',
            '{"open": false}',
            '{"open": false}',
            '{"open": false}',
            '{"speak": true, "text": "Trust me."}',
            '{"speak": true, "text": "Trust no one."}',
            '{"speak": false}',
            '{"speak": true, "text": "Last word."}',
            '{"speak": false}',
            '{"speak": false}',
        ]
        self._beat()
        parley = Parley.objects.get()
        messages = list(parley.messages.order_by("round", "order"))
        self.assertEqual(len(messages), 6)  # rounds 1 and 2 only, no round 3
        self.assertEqual({m.round for m in messages}, {1, 2})
        # Round 1 had two non-pass messages (parley continued); round 2 one.
        self.assertEqual(len([m for m in messages if m.round == 1 and m.text]), 2)
        self.assertEqual(len([m for m in messages if m.round == 2 and m.text]), 1)
        event = self.game.events.get(type="parley")
        self.assertEqual(len(event.payload["transcript"]), 6)
        self.assertEqual(LlmCall.objects.filter(purpose="parley_speak").count(), 6)

    def test_scheduling_failure_twice_opens_nothing(self):
        self.fake.responses = ["not json", "still not json"]  # Bram fails twice
        self.fake.responses += [
            '{"open": true, "invitees": ["Bram"]}',  # Sable opens with Bram
            '{"open": false}',
            '{"open": false}',
            '{"speak": true, "text": "Hi."}',  # 2 participants, 2 rounds
            '{"speak": true, "text": "Hi."}',
            '{"speak": true, "text": "Hi."}',
            '{"speak": true, "text": "Hi."}',
        ]
        self._beat()  # must not raise
        parley = Parley.objects.get()
        self.assertEqual(parley.opener.name, "Sable")
        self.assertEqual(
            {thief.name for thief in parley.participants.all()}, {"Sable", "Bram"}
        )
        # Bram's failed scheduling was logged twice and opened nothing.
        failed = list(LlmCall.objects.filter(thief__name="Bram", purpose="parley_open"))
        self.assertEqual(len(failed), 2)
        self.assertTrue(all(row.error for row in failed))
        # The parley Sable opened still ran its two rounds.
        self.assertEqual(ParleyMessage.objects.count(), 4)
        self.assertEqual(LlmCall.objects.filter(purpose="parley_speak").count(), 4)

    def test_invalid_scheduling_answers_open_nothing(self):
        Thief.objects.create(game=self.game, name="Vex")
        Thief.objects.create(game=self.game, name="Ivy")
        self.fake.responses = [
            '{"open": true, "invitees": ["Nobody"]}',  # unknown name
            '{"open": true, "invitees": []}',  # too small
            # Five invitees plus the opener: six thieves, too big.
            '{"open": true, "invitees": ["Bram", "Sable", "Aldo", "Vex", "Ivy"]}',
            '{"open": "yes"}',  # not a boolean
            '{"open": false}',
            '{"open": false}',
        ]
        self._beat()
        self.assertEqual(Parley.objects.count(), 0)
        self.assertEqual(ParleyMessage.objects.count(), 0)
        self.assertFalse(self.game.events.filter(type="parley").exists())
        # Everyone still got their one scheduling call.
        self.assertEqual(LlmCall.objects.filter(purpose="parley_open").count(), 6)

    def test_speak_failure_is_a_pass(self):
        def picky(messages):
            user = messages[-1]["content"]
            if "open at most one" in user:
                return (
                    '{"open": true, "invitees": ["Bram"]}'
                    if "You are Sable." in messages[0]["content"]
                    else '{"open": false}'
                )
            if "You are Bram." in messages[0]["content"]:
                raise RuntimeError("network down")
            return '{"speak": true, "text": "Hi."}'

        llm.client = LlmClient(transport=picky)
        self._beat()  # must not raise
        parley = Parley.objects.get()
        messages = list(parley.messages.order_by("round", "order"))
        # Sable is the only speaker in round 1, so the parley ends there.
        self.assertEqual(len(messages), 2)  # 2 participants, 1 round
        self.assertTrue(all(m.round == 1 for m in messages))
        bram_rows = [m for m in messages if m.thief.name == "Bram"]
        sable_rows = [m for m in messages if m.thief.name == "Sable"]
        self.assertTrue(all(m.text == "" for m in bram_rows))  # passed every round
        self.assertTrue(all(m.text == "Hi." for m in sable_rows))
        bram_calls = LlmCall.objects.filter(thief__name="Bram", purpose="parley_speak")
        self.assertEqual(bram_calls.count(), 1)
        self.assertTrue(all("network down" in row.error for row in bram_calls))

    def test_dusk_window_parleys_are_dusk(self):
        self.game.phase = "dusk_parley"
        self.game.save()
        self.fake.responses = [
            '{"open": true, "invitees": ["Sable"]}',
            '{"open": false}',
            '{"open": false}',
            '{"open": false}',
            '{"speak": false}',  # silent round: the parley ends after round 1
            '{"speak": false}',
        ]
        self._beat()
        parley = Parley.objects.get()
        self.assertEqual(parley.window, "dusk")
        self.assertEqual(parley.day, 3)
        self.assertEqual(ParleyMessage.objects.count(), 2)
        self.assertEqual(self.game.events.get(type="parley").payload["window"], "dusk")

    def test_policy_mode_stays_a_logged_noop(self):
        game = Game.objects.create(day=3, phase="morning_parley", agents=False)
        Thief.objects.create(game=game, name="Bram")
        run_next_beat(game)
        self.assertTrue(
            game.events.filter(phase="morning_parley", type="beat").exists()
        )
        self.assertEqual(Parley.objects.count(), 0)
        self.assertEqual(LlmCall.objects.count(), 0)


class AgentMootTests(TestCase):
    """Agent-mode moot: proposals, seconds, floor, debate, ballots, tally."""

    NAMES = [
        "Bram",
        "Sable",
        "Merrick",
        "Aldo",
        "Vex",
        "Old Nan",
        "Joss",
        "Perrin",
        "Kael",
        "Ivy",
    ]

    def setUp(self):
        self.game = Game.objects.create(day=3, phase="moot", agents=True)
        for name in self.NAMES:
            Thief.objects.create(game=self.game, name=name)
        self.original_client = llm.client

    def tearDown(self):
        llm.client = self.original_client

    def _standard_transport(self, seen):
        """Bram and Sable propose; everyone seconds both; all speak; ballots
        give Bram's proposal 9 yes / 1 no and Sable's 1 yes / 9 no."""

        def transport(messages):
            seen.append(messages)
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "THE FLOOR - VOTE" in user:
                if "You are Sable." in system:
                    return '{"votes": {"Bram": "no", "Sable": "yes"}}'
                return '{"votes": {"Bram": "yes", "Sable": "no"}}'
            if "MOOT TRANSCRIPT SO FAR" in user:
                return '{"speak": true, "text": "Hear, hear."}'
            if "PROPOSALS ON THE TABLE" in user:
                return '{"second": ["Bram", "Sable"]}'
            if "You are Bram." in system:
                return (
                    '{"propose": true, "text": "Every thief shall declare '
                    'their take at the Moot."}'
                )
            if "You are Sable." in system:
                return '{"propose": true, "text": "No thief shall take more than 2 coins."}'
            return '{"propose": false}'

        return transport

    def test_moot_pipeline_from_proposals_to_tally(self):
        seen = []
        llm.client = LlmClient(transport=self._standard_transport(seen))
        run_next_beat(self.game)  # must not raise

        proposals = list(Proposal.objects.order_by("pk"))
        self.assertEqual(len(proposals), 2)
        bram_prop = next(p for p in proposals if p.author.name == "Bram")
        sable_prop = next(p for p in proposals if p.author.name == "Sable")

        # Seconds persisted; nobody seconds their own proposal.
        self.assertEqual(bram_prop.seconded_by.count(), 9)
        self.assertEqual(sable_prop.seconded_by.count(), 9)
        self.assertFalse(bram_prop.seconded_by.filter(name="Bram").exists())
        self.assertFalse(sable_prop.seconded_by.filter(name="Sable").exists())

        # Debate: 3 rounds x 10 thieves, one message per thief per round.
        messages = list(DebateMessage.objects.order_by("round", "order"))
        self.assertEqual(len(messages), 30)
        for round_no in (1, 2, 3):
            round_messages = [m for m in messages if m.round == round_no]
            self.assertEqual(len(round_messages), 10)
            self.assertEqual({m.thief.name for m in round_messages}, set(self.NAMES))
            self.assertEqual({m.order for m in round_messages}, set(range(10)))

        # Ballots: every thief cast one per floor proposal.
        self.assertEqual(Ballot.objects.count(), 20)
        own = Ballot.objects.get(proposal=bram_prop, thief__name="Bram")
        self.assertEqual(own.choice, "yes")

        # Tally: Bram's proposal wins 9-1; Sable's fails 1-9.
        bram_prop.refresh_from_db()
        sable_prop.refresh_from_db()
        self.assertEqual(bram_prop.status, "passed")
        self.assertEqual(sable_prop.status, "failed")
        self.assertEqual(bram_prop.yes, 9)
        self.assertEqual(bram_prop.no, 1)
        self.assertEqual(bram_prop.abstain, 0)
        self.assertEqual(sable_prop.yes, 1)
        self.assertEqual(sable_prop.no, 9)
        self.assertEqual(sable_prop.abstain, 0)

        # The public events tell the whole story.
        self.assertEqual(
            {e.type for e in self.game.events.all()},
            {"proposals", "seconds", "floor", "debate", "tally"},
        )
        floor_event = self.game.events.get(type="floor")
        self.assertEqual(floor_event.payload["floor"], ["Bram", "Sable"])
        self.assertIs(floor_event.payload["lottery"], False)
        tally_event = self.game.events.get(type="tally")
        self.assertIs(tally_event.payload["quorum"], True)
        self.assertEqual(tally_event.payload["law"], "Bram")
        self.assertEqual(
            tally_event.payload["tallies"]["Bram"],
            {"yes": 9, "no": 1, "abstain": 0},
        )
        self.assertEqual(
            tally_event.payload["tallies"]["Sable"],
            {"yes": 1, "no": 9, "abstain": 0},
        )
        debate_event = self.game.events.get(type="debate")
        self.assertEqual(len(debate_event.payload["transcript"]), 30)
        self.assertEqual(
            {e["round"] for e in debate_event.payload["transcript"]}, {1, 2, 3}
        )

        # Every call type is logged: propose, second, moot_speak, ballot.
        rows = list(LlmCall.objects.order_by("pk"))
        self.assertEqual(
            [row.purpose for row in rows],
            ["propose"] * 10 + ["second"] * 10 + ["moot_speak"] * 30 + ["ballot"] * 10,
        )
        self.assertTrue(all(row.error == "" for row in rows))
        self.assertTrue(all(row.day == 3 for row in rows))
        self.assertTrue(all(row.phase == "moot" for row in rows))

        # Seconding prompts show all proposals on the table.
        second_call = next(
            m for m in seen if "PROPOSALS ON THE TABLE" in m[-1]["content"]
        )
        self.assertIn(
            "Every thief shall declare their take at the Moot.",
            second_call[-1]["content"],
        )
        self.assertIn(
            "No thief shall take more than 2 coins.", second_call[-1]["content"]
        )
        # Debate speakers see the transcript so far.
        last_debate = next(
            m for m in reversed(seen) if "MOOT TRANSCRIPT SO FAR" in m[-1]["content"]
        )
        self.assertIn("MOOT TRANSCRIPT SO FAR:", last_debate[-1]["content"])
        self.assertIn("Hear, hear.", last_debate[-1]["content"])

    def test_ballot_failure_abstains_on_all(self):
        def transport(messages):
            user = messages[-1]["content"]
            system = messages[0]["content"]
            if "THE FLOOR - VOTE" in user:
                raise RuntimeError("voting machine broken")
            if "MOOT TRANSCRIPT SO FAR" in user:
                return '{"speak": false}'
            if "PROPOSALS ON THE TABLE" in user:
                return '{"second": ["Bram", "Sable"]}'
            if "You are Bram." in system:
                return '{"propose": true, "text": "Declare all takes."}'
            if "You are Sable." in system:
                return '{"propose": true, "text": "Cap takes at 2."}'
            return '{"propose": false}'

        llm.client = LlmClient(transport=transport)
        run_next_beat(self.game)  # must not raise
        proposals = list(Proposal.objects.order_by("pk"))
        self.assertEqual(len(proposals), 2)
        self.assertTrue(all(p.status == "failed" for p in proposals))
        self.assertTrue(all(p.yes == 0 and p.no == 0 for p in proposals))
        self.assertTrue(all(p.abstain == 10 for p in proposals))
        self.assertEqual(Ballot.objects.count(), 20)
        # Abstention counts as casting: quorum is met, but no law passes.
        tally_event = self.game.events.get(type="tally")
        self.assertIs(tally_event.payload["quorum"], True)
        self.assertIsNone(tally_event.payload["law"])

    def test_policy_mode_stays_a_logged_noop(self):
        game = Game.objects.create(day=3, phase="moot", agents=False)
        Thief.objects.create(game=game, name="Bram")
        run_next_beat(game)
        self.assertTrue(game.events.filter(phase="moot", type="beat").exists())
        self.assertEqual(Proposal.objects.count(), 0)
        self.assertEqual(Ballot.objects.count(), 0)
        self.assertEqual(DebateMessage.objects.count(), 0)
        self.assertEqual(LlmCall.objects.count(), 0)


class GameDayPageTests(TestCase):
    """The read-only day page: /game/<pk>/ and /game/<pk>/day/<n>/."""

    NAMES = ["Bram", "Sable", "Merrick"]

    def _make_game(self, day=2, agents=True):
        game = Game.objects.create(day=day, agents=agents)
        thieves = [Thief.objects.create(game=game, name=name) for name in self.NAMES]
        return game, thieves

    def test_unknown_game_is_404(self):
        response = self.client.get("/game/999999/")
        self.assertEqual(response.status_code, 404)

    def test_day_out_of_range_is_404(self):
        game, _ = self._make_game(day=2)
        for day in (0, -1, 3, 99):
            response = self.client.get(f"/game/{game.pk}/day/{day}/")
            self.assertEqual(response.status_code, 404)

    def test_default_route_shows_latest_day(self):
        game, _ = self._make_game(day=2)
        response = self.client.get(f"/game/{game.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Game {game.pk} — Day 2")
        self.assertTemplateUsed(response, "game_day.html")

    def test_policy_game_renders_dawn_and_night_only(self):
        game, _ = self._make_game(day=1, agents=False)
        Event.objects.create(
            game=game,
            day=1,
            phase="dawn",
            type="dawn_report",
            payload={"hoard": 250, "scores": {"Bram": 0, "Sable": 0}, "law": None},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="takes",
            payload={
                "takes": {"Bram": 2, "Sable": 2},
                "requested": {"Bram": 2, "Sable": 2},
                "hoard_after": 246,
            },
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="wake_roll",
            payload={"probability": 0.1, "roll": 0.9, "woke": False},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="regrow",
            payload={"hoard_before": 246, "hoard_after": 275},
        )
        response = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "policy")
        self.assertContains(response, "The hoard holds 250 coins.")
        self.assertContains(response, "Hoard after: 246 coins.")
        self.assertContains(response, "246 to 275 coins.")
        self.assertContains(response, "the dragon sleeps.")
        self.assertNotContains(response, "Moot")
        self.assertNotContains(response, "Parleys")

    def test_agent_day_renders_parley_moot_and_night(self):
        game, (bram, sable, merrick) = self._make_game(day=1, agents=True)

        parley = Parley.objects.create(game=game, day=1, window="morning", opener=bram)
        parley.participants.add(bram, sable)
        ParleyMessage.objects.create(
            parley=parley, round=1, thief=bram, order=0, text="Trust me."
        )
        ParleyMessage.objects.create(
            parley=parley, round=1, thief=sable, order=1, text=""
        )

        floor_proposal = Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="Every thief shall declare their take.",
            status="passed",
            yes=2,
            no=1,
            abstain=0,
        )
        floor_proposal.seconded_by.add(sable, merrick)
        Proposal.objects.create(
            game=game,
            day=1,
            author=sable,
            text="No thief shall take more than 2 coins.",
            status="failed",
        )
        Ballot.objects.create(proposal=floor_proposal, thief=bram, choice="yes")
        Ballot.objects.create(proposal=floor_proposal, thief=sable, choice="no")
        Ballot.objects.create(proposal=floor_proposal, thief=merrick, choice="abstain")
        DebateMessage.objects.create(
            game=game, day=1, round=1, thief=bram, order=0, text="Hear, hear."
        )
        DebateMessage.objects.create(
            game=game, day=1, round=1, thief=sable, order=1, text=""
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="moot",
            type="floor",
            payload={"floor": ["Bram"], "lottery": False},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="moot",
            type="tally",
            payload={"quorum": True, "law": "Bram"},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="takes",
            payload={
                "takes": {"Bram": 2, "Sable": 0, "Merrick": 3},
                "requested": {"Bram": 2, "Sable": 2, "Merrick": 3},
                "hoard_after": 243,
            },
        )

        response = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertEqual(response.status_code, 200)

        # Parley: opener, participants, transcript, and visible passes.
        self.assertContains(response, "Opened by Bram")
        self.assertContains(response, "Trust me.")
        self.assertContains(response, "(pass)")

        # Moot: both proposals with status, seconders, floor note, tally,
        # individual ballots, debate, quorum, and the law.
        self.assertContains(response, "Bram · Passed")
        self.assertContains(response, "Sable · Failed")
        self.assertContains(response, "Every thief shall declare their take.")
        self.assertContains(response, "Seconded by: Sable, Merrick")
        self.assertContains(response, "The floor went to: Bram.")
        self.assertContains(response, "Tally: 2 yes, 1 no, 0 abstain.")
        self.assertContains(response, "Merrick: Abstain")
        self.assertContains(response, "Hear, hear.")
        self.assertContains(response, "Quorum was met.")
        self.assertContains(response, "Law: Bram's proposal passed.")

        # Night: requested vs taken per thief, plus the hoard after.
        self.assertRegex(
            response.content.decode(),
            r"Sable</th>\s*<td>2</td>\s*<td>0</td>",
        )
        self.assertContains(response, "Hoard after: 243 coins.")

        # Phase order: the morning parley renders before the Moot.
        html = response.content.decode()
        self.assertLess(
            html.index("Opened by Bram"), html.index("The floor went to: Bram.")
        )

    def test_dusk_parley_renders_after_the_moot(self):
        """The dusk parley window appears after the Moot section, matching the
        day's chronological phase order (… moot → dusk parley → night)."""
        game, (bram, sable, _) = self._make_game(day=1, agents=True)

        morning = Parley.objects.create(game=game, day=1, window="morning", opener=bram)
        morning.participants.add(bram)
        ParleyMessage.objects.create(
            parley=morning, round=1, thief=bram, order=0, text="Before the moot."
        )

        Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="The moot business.",
            status="passed",
        )

        dusk = Parley.objects.create(game=game, day=1, window="dusk", opener=sable)
        dusk.participants.add(sable)
        ParleyMessage.objects.create(
            parley=dusk, round=1, thief=sable, order=0, text="After the moot."
        )

        response = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dusk parley")
        self.assertContains(response, "After the moot.")

        html = response.content.decode()
        self.assertLess(
            html.index("Before the moot."), html.index("The moot business.")
        )
        self.assertLess(html.index("The moot business."), html.index("After the moot."))

    def test_wake_and_rage_night_events_render(self):
        game, _ = self._make_game(day=3, agents=False)
        Event.objects.create(
            game=game,
            day=2,
            phase="night",
            type="wake",
            payload={
                "wake": 1,
                "losses": {"Bram": 2, "Sable": 0},
                "hoard_after": 250,
                "rage": True,
            },
        )
        Event.objects.create(
            game=game, day=3, phase="night", type="rage_night", payload={"hoard": 250}
        )
        wake_day = self.client.get(f"/game/{game.pk}/day/2/")
        self.assertContains(wake_day, "The dragon wakes")
        self.assertContains(wake_day, "Bram: 2")
        self.assertContains(wake_day, "Hoard refilled to 250 coins")
        rage_day = self.client.get(f"/game/{game.pk}/day/3/")
        self.assertContains(rage_day, "Rage night")
        self.assertContains(rage_day, "the hoard stays at 250 coins")

    def test_prev_next_links_hidden_at_the_edges(self):
        game, _ = self._make_game(day=2)
        first = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertContains(first, "Day 2 →")
        self.assertNotContains(first, "← Day")
        last = self.client.get(f"/game/{game.pk}/day/2/")
        self.assertContains(last, "← Day 1")
        self.assertNotContains(last, "Day 3 →")
        # The default route is the latest day, so it has no next link.
        default = self.client.get(f"/game/{game.pk}/")
        self.assertContains(default, "← Day 1")
        self.assertNotContains(default, "Day 3 →")

    def test_empty_day_still_renders_the_header(self):
        game, _ = self._make_game(day=2)
        response = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Game {game.pk} — Day 1")
        self.assertContains(response, "agents")


class IndexPageTests(TestCase):
    """The game index: / lists all games, newest first, linking to each."""

    def test_lists_games_newest_first(self):
        older = Game.objects.create(
            day=2, phase="moot", hoard=300, status="ended", agents=False
        )
        newer = Game.objects.create(
            day=1, phase="dawn", hoard=250, status="running", agents=True
        )
        now = timezone.now()
        Game.objects.filter(pk=older.pk).update(created=now - timedelta(hours=2))
        Game.objects.filter(pk=newer.pk).update(created=now - timedelta(hours=1))
        older.refresh_from_db()
        newer.refresh_from_db()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "index.html")

        html = response.content.decode()
        self.assertLess(html.index(f"Game {newer.pk}"), html.index(f"Game {older.pk}"))

        # Each row carries id, day, phase, status, hoard, mode, and a link to
        # the game's day page (pk-only reverse: latest-day route).
        for game, phase, status, mode in (
            (older, "Moot", "Ended", "policy"),
            (newer, "Dawn", "Running", "agents"),
        ):
            row = html[html.index(f'href="/game/{game.pk}/"') :]
            self.assertIn(f"Game {game.pk}", row)
            self.assertIn(f"<td>{game.day}</td>", row)
            self.assertIn(f">{phase}</td>", row)
            self.assertIn(f">{status}</td>", row)
            self.assertIn(f"<td>{game.hoard}</td>", row)
            self.assertIn(f">{mode}</td>", row)
            self.assertIn(game.created.strftime("%Y-%m-%d %H:%M"), row)

    def test_empty_index_shows_placeholder(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No games yet.")


class GoalContentTests(TestCase):
    """Private goals: one GOALS entry per persona, with the spec's shapes."""

    def test_one_goal_per_persona_with_valid_condition(self):
        from main.content import GOALS

        self.assertEqual(list(GOALS), [name for name, _ in PERSONAS])
        for name in GOALS:
            goal = GOALS[name]
            self.assertEqual(
                set(goal), {"text", "condition", "payout"}, f"{name} goal keys"
            )
            self.assertIsInstance(goal["payout"], int)
            condition = goal["condition"]
            if condition["type"] == "gold":
                self.assertEqual(
                    set(condition), {"type", "amount", "by_day"}, f"{name} condition"
                )
            elif condition["type"] == "law":
                self.assertEqual(set(condition), {"type"}, f"{name} condition")
            elif condition["type"] == "hoard":
                self.assertEqual(
                    set(condition), {"type", "amount", "day"}, f"{name} condition"
                )
            else:
                self.fail(f"{name}: unknown condition type {condition['type']!r}")


class NewGameCommandTests(TestCase):
    """new_game: --agents populates goals; policy games stay goal-free."""

    def test_agents_game_copies_goals_onto_each_thief(self):
        from io import StringIO

        from django.core.management import call_command

        from main.content import GOALS

        call_command("new_game", "--agents", stdout=StringIO())
        game = Game.objects.get(agents=True)
        thieves = {thief.name: thief for thief in game.thieves.all()}
        self.assertEqual(set(thieves), set(GOALS))
        for name, goal in GOALS.items():
            thief = thieves[name]
            self.assertEqual(thief.goal, goal["text"])
            self.assertEqual(thief.goal_condition, goal["condition"])
            self.assertEqual(thief.goal_payout, goal["payout"])
            self.assertIsNone(thief.goal_met_day)

    def test_policy_game_has_no_goals(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("new_game", "--policies", "2,2,2,2,2,2,2,2,2,2", stdout=StringIO())
        game = Game.objects.get(agents=False)
        for thief in game.thieves.all():
            self.assertEqual(thief.goal, "")
            self.assertEqual(thief.goal_condition, {})
            self.assertEqual(thief.goal_payout, 0)
            self.assertIsNone(thief.goal_met_day)


class RuleSetTests(TestCase):
    """Law storage: RuleSet rows, scratchpad snapshots, active_ruleset."""

    def test_active_ruleset_none_without_rulesets(self):
        from main.models import Game, active_ruleset

        game = Game.objects.create(day=3)
        self.assertIsNone(active_ruleset(game))

    def test_active_ruleset_picks_latest_with_day_at_or_before_game_day(self):
        from main.models import Game, RuleSet, active_ruleset

        game = Game.objects.create(day=3)
        older = RuleSet.objects.create(game=game, day=2, code="-- old")
        RuleSet.objects.create(game=game, day=5, code="-- future")
        # The day-5 law is not yet in force: not until its day arrives.
        self.assertEqual(active_ruleset(game), older)
        game.day = 5
        game.save()
        self.assertEqual(active_ruleset(game).code, "-- future")

    def test_active_ruleset_breaks_same_day_ties_by_pk(self):
        from main.models import Game, RuleSet, active_ruleset

        game = Game.objects.create(day=4)
        first = RuleSet.objects.create(game=game, day=4, code="-- first")
        second = RuleSet.objects.create(game=game, day=4, code="-- second")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(active_ruleset(game), second)

    def test_set_rules_snapshots_scratchpad_and_defaults_to_next_day(self):
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        from main.models import Game, RuleSet

        game = Game.objects.create(day=3, scratchpad={"inactive": ["Bram"]})
        fd, path = tempfile.mkstemp(suffix=".lua")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("-- no-op statute book\n")
            call_command("set_rules", game.pk, path, stdout=StringIO())
        finally:
            os.unlink(path)
        ruleset = RuleSet.objects.get()
        self.assertEqual(ruleset.game, game)
        self.assertEqual(ruleset.day, 4)  # laws take effect at next dawn
        self.assertEqual(ruleset.code, "-- no-op statute book\n")
        self.assertEqual(ruleset.scratchpad, {"inactive": ["Bram"]})
        self.assertIsNone(ruleset.proposal)

    def test_set_rules_honors_day_and_proposal_overrides(self):
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        from main.models import Game, Proposal, RuleSet, Thief

        game = Game.objects.create(day=3)
        bram = Thief.objects.create(game=game, name="Bram")
        proposal = Proposal.objects.create(game=game, day=2, author=bram, text="Law!")
        fd, path = tempfile.mkstemp(suffix=".lua")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("-- code\n")
            call_command(
                "set_rules",
                game.pk,
                path,
                "--day",
                "7",
                "--proposal",
                str(proposal.pk),
                stdout=StringIO(),
            )
        finally:
            os.unlink(path)
        ruleset = RuleSet.objects.get()
        self.assertEqual(ruleset.day, 7)
        self.assertEqual(ruleset.proposal, proposal)

    def test_set_rules_rejects_unknown_game(self):
        from io import StringIO

        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("set_rules", 999, "/nonexistent.lua", stdout=StringIO())


class LuaRuleHookTests(unittest.TestCase):
    """Sandboxed Lua rule hooks and the capability bridge (main.rules)."""

    def _state(self, **overrides):
        state = {
            "day": 5,
            "hoard": 250,
            "scores": {"Alice": 10, "Bob": 4},
            "scratchpad": {"count": 1, "note": "hi"},
        }
        state.update(overrides)
        return state

    def _run(self, code, hook, args=(), state=None):
        from main.rules import run_hook

        return run_hook(code, hook, list(args), state or self._state())

    def test_sandbox_denies_dangerous_globals(self):
        for name in (
            "os",
            "io",
            "require",
            "load",
            "dofile",
            "loadstring",
            "collectgarbage",
            "debug",
            "coroutine",
            "_G",
            "package",
            "setmetatable",
            "getmetatable",
            "rawget",
            "rawset",
        ):
            result = self._run(
                f"function on_day_start() return type({name}) end", "on_day_start"
            )
            self.assertEqual(result.value, "nil", f"{name} leaked into the sandbox")
            self.assertIsNone(result.error)

    def test_attempting_to_use_denied_globals_errors(self):
        for name in ("os", "io", "require", "load", "debug", "dofile", "_G"):
            result = self._run(
                f"function on_day_start() return {name}.x end", "on_day_start"
            )
            self.assertIsNotNone(result.error, f"using {name} did not error")
            self.assertIn("nil value", result.error)

    def test_pcall_is_not_whitelisted(self):
        # pcall would let a rule swallow the budget-hook error inside
        # pcall(function() while true do end end) and wedge the host
        # forever, so it is deliberately excluded from the whitelist.
        for name in ("pcall", "xpcall"):
            result = self._run(
                f"function on_day_start() return type({name}) end", "on_day_start"
            )
            self.assertEqual(result.value, "nil", f"{name} leaked into the sandbox")

    def test_infinite_loop_errors_within_budget(self):
        import time

        start = time.monotonic()
        result = self._run(
            "function on_day_start() while true do end end", "on_day_start"
        )
        elapsed = time.monotonic() - start
        self.assertIsNotNone(result.error)
        self.assertIn("budget", result.error)
        self.assertLess(elapsed, 30, "infinite loop outlived the instruction budget")
        self.assertEqual(result.scratchpad, self._state()["scratchpad"])

    def test_scratchpad_round_trips_json_shapes(self):
        code = """
            function on_day_start(state)
                state.scratchpad.count = state.scratchpad.count + 1
                state.scratchpad.string = "snow"
                state.scratchpad.number = 42
                state.scratchpad.float = 3.5
                state.scratchpad.truth = true
                state.scratchpad.falsity = false
                state.scratchpad.list = {1, 2, 3}
                state.scratchpad.nested = {a = {1, {b = false}}, c = {"deep", 7}}
            end
        """
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(
            result.scratchpad,
            {
                "count": 2,
                "note": "hi",
                "string": "snow",
                "number": 42,
                "float": 3.5,
                "truth": True,
                "falsity": False,
                "list": [1, 2, 3],
                "nested": {"a": [1, {"b": False}], "c": ["deep", 7]},
            },
        )

    def test_capability_calls_captured(self):
        from main.rules import CapabilityCall

        code = """
            function on_day_start()
                adjust_score("Alice", 3, "helped the moot")
                announce("The dragon stirs")
                adjust_score("Bob", -2, "spilled the hoard")
            end
        """
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(
            result.calls,
            [
                CapabilityCall("adjust_score", ("Alice", 3, "helped the moot")),
                CapabilityCall("announce", ("The dragon stirs",)),
                CapabilityCall("adjust_score", ("Bob", -2, "spilled the hoard")),
            ],
        )

    def test_bridge_args_are_converted_to_jsonable_values(self):
        code = 'function on_day_start() adjust_score({"Alice", "Bob"}, 1, {why = "all"}) end'
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(result.calls[0].args, (["Alice", "Bob"], 1, {"why": "all"}))

    def test_missing_hook_is_silent_noop(self):
        state = self._state()
        result = self._run("", "on_day_start", state=state)
        self.assertIsNone(result.error)
        self.assertIsNone(result.value)
        self.assertEqual(result.calls, [])
        self.assertIs(result.scratchpad, state["scratchpad"])

    def test_missing_hook_among_defined_others_is_noop(self):
        result = self._run("function on_moot_end() announce('x') end", "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(result.calls, [])

    def test_lua_runtime_error_is_contained(self):
        state = self._state()
        result = self._run(
            "function on_day_start() local x = nil; return x.gold end",
            "on_day_start",
            state=state,
        )
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.value)
        self.assertEqual(result.calls, [])
        self.assertIs(result.scratchpad, state["scratchpad"])

    def test_syntax_error_is_contained(self):
        result = self._run("function on_day_start(", "on_day_start")
        self.assertIsNotNone(result.error)
        self.assertIn("rules.lua", result.error)

    def test_hook_name_colliding_with_non_function_is_an_error(self):
        result = self._run("on_day_start = 42", "on_day_start")
        self.assertIsNotNone(result.error)
        self.assertIn("not a function", result.error)

    def test_state_scores_do_not_leak_back(self):
        state = self._state()
        code = """
            function on_day_start(state)
                state.scores.Alice = 999
                state.scores = {Hacker = 1}
                state.hoard = 0
                state.day = 0
            end
        """
        result = self._run(code, "on_day_start", state=state)
        self.assertIsNone(result.error)
        self.assertEqual(state, self._state())
        self.assertEqual(result.scratchpad, self._state()["scratchpad"])

    def test_validate_action_return_value_comes_through(self):
        code = """
            function validate_action(action, state)
                if action == "steal" then return true end
                return "refused: " .. action
            end
        """
        result = self._run(code, "validate_action", args=["steal"])
        self.assertIs(result.value, True)
        result = self._run(code, "validate_action", args=["trade"])
        self.assertEqual(result.value, "refused: trade")

    def test_args_reach_the_hook_with_state_last(self):
        code = "function on_day_start(name, amount, state) return name .. ':' .. amount .. ':' .. state.day end"
        result = self._run(code, "on_day_start", args=["Alice", 7])
        self.assertEqual(result.value, "Alice:7:5")

    def test_non_jsonable_scratchpad_is_a_hook_error(self):
        cases = [
            (
                "function on_day_start(state) state.scratchpad.f = function() end end",
                "functions",
            ),
            (
                "function on_day_start(state) state.scratchpad = {1, 2, a = 3} end",
                "mixed keys",
            ),
            (
                "function on_day_start(state) state.scratchpad.self = state.scratchpad end",
                "cycles",
            ),
            (
                "function on_day_start(state) state.scratchpad.inf = math.huge end",
                "non-finite numbers",
            ),
            (
                "function on_day_start(state) state.scratchpad[10] = 'x' end",
                "sparse integer keys",
            ),
        ]
        for code, label in cases:
            state = self._state()
            result = self._run(code, "on_day_start", state=state)
            self.assertIsNotNone(result.error, f"{label} were accepted")
            self.assertIs(
                result.scratchpad,
                state["scratchpad"],
                f"{label} mutated the engine scratchpad",
            )

    def test_memory_bomb_is_contained(self):
        result = self._run(
            "function on_day_start() return string.rep('x', 1000 * 1000 * 1000) end",
            "on_day_start",
        )
        self.assertIsNotNone(result.error)
        self.assertIn("memory", result.error)


class InactiveThiefTests(TestCase):
    """The scratchpad's ``inactive`` list: the dead act nowhere, count for no
    quorum, and never get paid, but keep their frozen score on every ranking
    surface."""

    def setUp(self):
        self.original_client = llm.client
        self.original_implementor = llm.implementor_client
        llm.client = LlmClient(transport=self._forbidden_transport)
        # The implementor pipeline compiles each day's passed proposal
        # (Bram's proposal passes on both day 1 and day 2); its calls carry
        # no thief (they are never prompts to any thief), so a clean fake
        # keeps the day's call count deterministic.
        llm.implementor_client = LlmClient(
            transport=ImplementorFakeTransport(
                ["function on_day_start(state) end"] * 2,
                ['{"approve": true, "reason": "ok"}'] * 2,
            )
        )

    def tearDown(self):
        llm.client = self.original_client
        llm.implementor_client = self.original_implementor

    @staticmethod
    def _forbidden_transport(messages):
        raise AssertionError("unexpected LLM call")

    def _full_day_transport(self, seen):
        """Content-dispatched replies for a whole day of agent beats.

        Bram opens both parleys naming Sable on purpose (the engine must
        strip her once she is inactive) and proposes at the Moot; Merrick
        seconds; everyone invited speaks; every active thief takes 2 and
        writes a diary.
        """

        def transport(messages):
            seen.append(messages)
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "open at most one private parley" in user:
                if "You are Bram." in system:
                    return '{"open": true, "invitees": ["Sable", "Merrick"]}'
                return '{"open": false}'
            if "PARLEY TRANSCRIPT SO FAR" in user:
                return '{"speak": true, "text": "Trust me."}'
            if "THE FLOOR - VOTE" in user:
                return '{"votes": {"Bram": "yes"}}'
            if "MOOT TRANSCRIPT SO FAR" in user:
                return '{"speak": true, "text": "Hear, hear."}'
            if "PROPOSALS ON THE TABLE" in user:
                return '{"second": ["Bram"]}'
            if "How many coins do you take" in user:
                return '{"take": 2}'
            if "Write your private diary" in user:
                return "Quiet today."
            if "You are Bram." in system:
                return '{"propose": true, "text": "Declare all takes at the Moot."}'
            return '{"propose": false}'

        return transport

    def test_inactive_thief_acts_nowhere_across_a_full_day(self):
        game = Game.objects.create(day=1, phase="dawn", agents=True, hoard=250)
        Thief.objects.create(game=game, name="Bram", gold=10)
        Thief.objects.create(game=game, name="Merrick", gold=0)
        sable = Thief.objects.create(
            game=game,
            name="Sable",
            gold=33,
            goal_condition={"type": "gold", "amount": 35, "by_day": 2},
            goal_payout=15,
        )
        Thief.objects.create(game=game, name="Vex", gold=8)
        Thief.objects.create(game=game, name="Ivy", gold=6)

        llm.client = LlmClient(transport=self._full_day_transport([]))

        # Day 1: everyone active — a control day where Sable acts freely.
        for _ in range(6):
            run_next_beat(game)
        self.assertTrue(LlmCall.objects.filter(day=1, thief__name="Sable").exists())
        day1_parley = Parley.objects.get(day=1, window="morning")
        self.assertIn("Sable", {thief.name for thief in day1_parley.participants.all()})

        # Mid-game: the rules write the inactive list; the engine never kills.
        game.scratchpad = {"inactive": ["Sable", "Vex", "Ivy"]}
        game.save()

        # Day 2: the full day with the list in force.
        for _ in range(6):
            run_next_beat(game)

        # No LLM prompt of any kind went to an inactive thief on day 2: no
        # scheduling, no moot call, no take, no diary. All 28 day-2 calls
        # went to Bram or Merrick; the two extra thief-less calls are the
        # implementor pipeline (implementor + reviewer) compiling Bram's
        # passed proposal — never a prompt to any thief.
        day2_calls = list(LlmCall.objects.filter(day=2).select_related("thief"))
        self.assertEqual(len(day2_calls), 30)
        self.assertEqual(
            {call.thief.name for call in day2_calls if call.thief is not None},
            {"Bram", "Merrick"},
        )

        # Parleys: Sable was named in Bram's invitation but was stripped
        # silently, and the dead never speak.
        for window in ("morning", "dusk"):
            parley = Parley.objects.get(day=2, window=window)
            self.assertEqual(
                {thief.name for thief in parley.participants.all()},
                {"Bram", "Merrick"},
                f"{window} parley kept an inactive participant",
            )
        self.assertFalse(
            ParleyMessage.objects.filter(
                parley__day=2, thief__name__in=["Sable", "Vex", "Ivy"]
            ).exists()
        )

        # Moot: only Bram proposed and only Merrick seconded. Quorum counts
        # the two active thieves, so one caster suffices (ceil(2/2)) — not
        # the three the full five-thief roster would need (ceil(5/2)).
        proposals = list(Proposal.objects.filter(day=2))
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal.author.name, "Bram")
        self.assertEqual(
            {thief.name for thief in proposal.seconded_by.all()}, {"Merrick"}
        )
        self.assertEqual(proposal.status, "passed")
        self.assertEqual(
            {ballot.thief.name for ballot in Ballot.objects.filter(proposal=proposal)},
            {"Bram", "Merrick"},
        )
        self.assertFalse(
            DebateMessage.objects.filter(
                game=game, day=2, thief__name__in=["Sable", "Vex", "Ivy"]
            ).exists()
        )
        tally = game.events.get(day=2, type="tally")
        self.assertIs(tally.payload["quorum"], True)
        self.assertEqual(tally.payload["law"], "Bram")

        # Night: no take for the dead; their gold freezes.
        takes = game.events.get(day=2, type="takes")
        self.assertEqual(takes.payload["takes"], {"Bram": 2, "Merrick": 2})
        self.assertEqual(takes.payload["requested"], {"Bram": 2, "Merrick": 2})

        # Dawn: the dead's goals never pay — Sable holds 35 at a dawn her
        # goal would have met — and her diary is never rewritten.
        self.assertFalse(game.events.filter(day=2, type="goal_payout").exists())
        sable.refresh_from_db()
        self.assertEqual(sable.gold, 35)
        self.assertIsNone(sable.goal_met_day)
        self.assertEqual(sable.diary, "Quiet today.")  # day-1 entry, untouched

        # ...but her frozen score still appears in the dawn snapshot.
        dawn = game.events.get(day=2, type="dawn_report")
        self.assertEqual(dawn.payload["scores"]["Sable"], 35)

    def test_inactive_thief_keeps_frozen_score_in_dawn_and_ranking(self):
        game = Game.objects.create(
            day=END_CAP_DAY,
            phase="dawn",
            agents=False,
            scratchpad={"inactive": ["Sable"]},
        )
        Thief.objects.create(game=game, name="Bram", gold=10)
        Thief.objects.create(game=game, name="Merrick", gold=0)
        Thief.objects.create(game=game, name="Sable", gold=35)
        run_next_beat(game)

        # The end-of-game dawn: every score counts, active or not.
        self.assertEqual(game.status, "ended")
        dawn = game.events.get(type="dawn_report")
        self.assertEqual(
            dawn.payload["scores"], {"Bram": 10, "Merrick": 0, "Sable": 35}
        )
        ranking = game.events.get(type="final_ranking")
        self.assertEqual(
            ranking.payload["ranking"],
            [
                {"name": "Sable", "gold": 35},
                {"name": "Bram", "gold": 10},
                {"name": "Merrick", "gold": 0},
            ],
        )

    def test_unknown_names_in_the_inactive_list_are_ignored(self):
        game = Game.objects.create(
            day=3,
            phase="night",
            agents=True,
            hoard=200,
            scratchpad={"inactive": ["Nobody", "Also Nobody"]},
        )
        bram = Thief.objects.create(game=game, name="Bram", gold=10)
        sable = Thief.objects.create(game=game, name="Sable", gold=5)
        llm.client = LlmClient(transport=FakeTransport(['{"take": 1}', '{"take": 2}']))
        run_next_beat(game)
        bram.refresh_from_db()
        sable.refresh_from_db()
        self.assertEqual(bram.gold, 11)
        self.assertEqual(sable.gold, 7)
        self.assertEqual(
            game.events.get(type="takes").payload["takes"], {"Bram": 1, "Sable": 2}
        )
        self.assertEqual(LlmCall.objects.count(), 2)


class LuaRuleHookSecurityTests(unittest.TestCase):
    """Regression tests from the security review of the Lua sandbox."""

    def _state(self, **overrides):
        state = {
            "day": 5,
            "hoard": 250,
            "scores": {"Alice": 10, "Bob": 4},
            "scratchpad": {"count": 1, "note": "hi"},
        }
        state.update(overrides)
        return state

    def _run(self, code, hook, args=(), state=None):
        from main.rules import run_hook

        return run_hook(code, hook, list(args), state or self._state())

    def test_no_python_attribute_access_from_lua(self):
        """The __globals__.__builtins__.__import__ escape must stay closed."""
        for fn in ("adjust_score", "announce"):
            for attr in ("__globals__", "__builtins__", "__class__"):
                result = self._run(
                    f"function on_day_start() return {fn}.{attr} end", "on_day_start"
                )
                self.assertIsNotNone(
                    result.error, f"{fn}.{attr} leaked into the sandbox"
                )
                self.assertIsNone(result.value)

    def test_only_the_two_bridge_callables_are_userdata(self):
        code = """
            function on_day_start()
                local ud = 0
                for k, v in pairs(_ENV) do
                    if type(v) == "userdata" then ud = ud + 1 end
                end
                return ud
            end
        """
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(result.value, 2)

    def test_state_is_native_lua_table(self):
        state = self._state(scratchpad={"plan": ["first", "second"]})
        code = """
            function on_day_start(state)
                if state.missing_key ~= nil then return "missing key is not nil" end
                if state.scratchpad.ghost ~= nil then return "ghost is not nil" end
                if state.scratchpad.plan[0] ~= nil then return "list is zero-based" end
                if state.scratchpad.plan[1] ~= "first" then return "list is not 1-based" end
                state.scratchpad.plan[2] = "third"
                return "ok"
            end
        """
        result = self._run(code, "on_day_start", state=state)
        self.assertIsNone(result.error)
        self.assertEqual(result.value, "ok")
        self.assertEqual(result.scratchpad["plan"], ["first", "third"])

    def test_nested_state_lists_and_dicts_are_native(self):
        state = self._state(
            scratchpad={
                "inner": {"x": 1, "kept": True},
                "matrix": [["a", "b"], ["c", "d"]],
            }
        )
        code = """
            function on_day_start(state)
                local total = 0
                for i, row in ipairs(state.scratchpad.matrix) do
                    for j, cell in ipairs(row) do
                        total = total + #cell
                    end
                end
                state.scratchpad.inner.kept = state.scratchpad.inner.kept
                state.scratchpad.inner.added = "new"
                local seen = {}
                for k, v in pairs(state.scratchpad.inner) do seen[k] = v end
                state.scratchpad.total = total
                state.scratchpad.seen = 0
                for _ in pairs(seen) do state.scratchpad.seen = state.scratchpad.seen + 1 end
            end
        """
        result = self._run(code, "on_day_start", state=state)
        self.assertIsNone(result.error)
        self.assertEqual(result.scratchpad["total"], 4)
        self.assertEqual(
            result.scratchpad["inner"], {"x": 1, "kept": True, "added": "new"}
        )
        self.assertEqual(result.scratchpad["seen"], 3)

    def test_run_hook_never_raises_on_missing_scratchpad(self):
        from main.rules import run_hook

        result = run_hook("function on_day_start() end", "on_day_start", [], {"day": 1})
        self.assertIsNotNone(result.error)
        self.assertIn("KeyError", result.error)

    def test_run_hook_never_raises_on_non_utf8_output(self):
        result = self._run(
            "function on_day_start() return string.char(255) end", "on_day_start"
        )
        self.assertIsNotNone(result.error)
        self.assertIn("UTF-8", result.error)

    def test_run_hook_never_raises_on_deep_nesting(self):
        code = """
            function on_day_start(state)
                local t = {}
                local c = t
                for i = 1, 10000 do c[1] = {}; c = c[1] end
                state.scratchpad.deep = t
            end
        """
        result = self._run(code, "on_day_start")
        self.assertIsNotNone(result.error)

    def test_run_hook_never_raises_on_broken_args(self):
        from main.rules import run_hook

        result = run_hook(
            "function on_day_start() end", "on_day_start", None, self._state()
        )
        self.assertIsNotNone(result.error)
        self.assertIn("TypeError", result.error)

    def test_table_move_is_not_whitelisted(self):
        result = self._run(
            "function on_day_start() return type(table.move) end", "on_day_start"
        )
        self.assertEqual(result.value, "nil")

    def test_audited_table_subset_is_available(self):
        code = """
            function on_day_start()
                local t = {3, 1, 2}
                table.sort(t)
                table.insert(t, 4)
                table.remove(t, 1)
                local a, b = table.unpack({10, 20})
                return table.concat(t, ",") .. ":" .. (a + b)
            end
        """
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(result.value, "2,3,4:30")

    def test_isolated_matches_in_process_result(self):
        from main.rules import run_hook, run_hook_isolated

        code = """
            function on_day_start(state)
                state.scratchpad.count = state.scratchpad.count + 1
                announce("hi")
                adjust_score("Alice", 2, "reason")
                return state.day
            end
        """
        state = self._state()
        in_process = run_hook(code, "on_day_start", [], state)
        isolated = run_hook_isolated(code, "on_day_start", [], state)
        self.assertEqual(in_process, isolated)
        self.assertEqual(isolated.value, 5)
        self.assertEqual(isolated.scratchpad["count"], 2)

    def test_isolated_table_move_bomb_is_contained(self):
        import time

        from main.rules import run_hook_isolated

        start = time.monotonic()
        result = run_hook_isolated(
            "function on_day_start() table.move({}, 1, 1e12, 1) end",
            "on_day_start",
            [],
            self._state(),
        )
        self.assertIsNotNone(result.error)
        self.assertLess(time.monotonic() - start, 30)

    def test_isolated_pathological_pattern_is_contained(self):
        import time

        from main.rules import run_hook_isolated

        # ~2^26 backtracking paths against a failing tail: verified to burn
        # > 2s of CPU in-process, so the child's RLIMIT_CPU kills it and the
        # parent reports the time-budget error.
        pattern = "a?" * 26 + "b"
        code = (
            'function on_day_start() return string.match(string.rep("a", 30), '
            f'"{pattern}") end'
        )
        start = time.monotonic()
        result = run_hook_isolated(code, "on_day_start", [], self._state())
        self.assertIsNotNone(result.error)
        self.assertLess(time.monotonic() - start, 30)

    def test_capability_call_limit(self):
        code = "function on_day_start() for i = 1, 101 do adjust_score('A', i, 'r') end end"
        result = self._run(code, "on_day_start")
        self.assertIsNotNone(result.error)
        self.assertIn("too many capability calls", result.error)

    def test_capability_string_args_are_truncated(self):
        code = (
            'function on_day_start() announce(string.rep("x", 5000)); '
            'adjust_score("A", 1, string.rep("y", 5000)) end'
        )
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(len(result.calls[0].args[0]), 2000)
        self.assertEqual(len(result.calls[1].args[2]), 2000)

    def test_empty_table_converts_to_object_not_list(self):
        code = """
            function on_day_start(state)
                state.scratchpad.empty = {}
                state.scratchpad.sparse = {1, nil}
            end
        """
        result = self._run(code, "on_day_start")
        self.assertIsNone(result.error)
        self.assertEqual(result.scratchpad["empty"], {})
        self.assertEqual(result.scratchpad["sparse"], [1])


class ImplementorTransportTests(TestCase):
    """The implementor client: its own env config, no DeepSeek thinking knob.

    The transports are thin env-reading closures, so we mock ``OpenAI`` and
    inspect the constructor / create kwargs instead of touching the network.
    """

    MESSAGES = [{"role": "user", "content": "write a statute book"}]

    def _capture_call(self, transport, env):
        """Run ``transport`` under ``env`` with a mocked OpenAI.

        ``env`` replaces ``os.environ`` entirely (``clear=True``), so an
        empty dict also exercises the documented defaults. Returns the mock
        ``OpenAI`` class for inspecting construction and create kwargs.
        """
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("main.llm.OpenAI") as openai_cls:
                transport(self.MESSAGES)
        return openai_cls

    def _create_kwargs(self, openai_cls):
        return openai_cls.return_value.chat.completions.create.call_args.kwargs

    def test_two_module_level_clients_with_distinct_transports(self):
        self.assertIsInstance(llm.client, LlmClient)
        self.assertIsInstance(llm.implementor_client, LlmClient)
        self.assertIsNot(llm.client.transport, llm.implementor_client.transport)

    def test_thief_transport_keeps_deepseek_config_and_thinking_disable(self):
        openai_cls = self._capture_call(llm.client.transport, {})
        self.assertEqual(openai_cls.call_args.kwargs["api_key"], None)
        self.assertEqual(
            openai_cls.call_args.kwargs["base_url"], "https://api.deepseek.com"
        )
        self.assertEqual(openai_cls.call_args.kwargs["timeout"], 120.0)
        self.assertEqual(openai_cls.call_args.kwargs["max_retries"], 0)
        kwargs = self._create_kwargs(openai_cls)
        self.assertEqual(kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(kwargs["max_tokens"], 1500)
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_implementor_transport_uses_anthropic_defaults_and_no_extra_body(self):
        openai_cls = self._capture_call(llm.implementor_client.transport, {})
        self.assertEqual(openai_cls.call_args.kwargs["api_key"], None)
        self.assertEqual(
            openai_cls.call_args.kwargs["base_url"], "https://api.anthropic.com/v1/"
        )
        self.assertEqual(openai_cls.call_args.kwargs["timeout"], 120.0)
        self.assertEqual(openai_cls.call_args.kwargs["max_retries"], 0)
        kwargs = self._create_kwargs(openai_cls)
        self.assertEqual(kwargs["model"], "claude-fable-5")
        self.assertEqual(kwargs["max_tokens"], 8000)
        self.assertNotIn("extra_body", kwargs)
        self.assertNotIn("thinking", kwargs)

    def test_implementor_env_is_read_at_call_time(self):
        openai_cls = self._capture_call(
            llm.implementor_client.transport,
            {
                "LLM_IMPLEMENTOR_API_KEY": "test-key",
                "LLM_IMPLEMENTOR_BASE_URL": "https://example.test/v1/",
                "LLM_IMPLEMENTOR_MODEL": "fable-5-test",
            },
        )
        self.assertEqual(openai_cls.call_args.kwargs["api_key"], "test-key")
        self.assertEqual(
            openai_cls.call_args.kwargs["base_url"], "https://example.test/v1/"
        )
        kwargs = self._create_kwargs(openai_cls)
        self.assertEqual(kwargs["model"], "fable-5-test")

    def test_implementor_client_is_swappable_in_tests(self):
        original = llm.implementor_client
        try:
            fake = FakeTransport(["return 0"])
            llm.implementor_client = LlmClient(transport=fake)
            self.assertEqual(llm.implementor_client.chat(self.MESSAGES), "return 0")
            self.assertEqual(fake.messages, [self.MESSAGES])
        finally:
            llm.implementor_client = original


class RuleHookBeatTests(TestCase):
    """End-to-end rule hooks: hand-authored Lua enacted via the ``set_rules``
    command runs inside the beats; its results land in gold, events, and
    prompts. Every scenario goes through ``run_next_beat`` and the sandboxed
    ``run_hook_isolated`` path."""

    def setUp(self):
        self.original_client = llm.client

    def tearDown(self):
        llm.client = self.original_client

    def _enact(self, game, code, day=None):
        """Enact ``code`` for ``game`` with the set_rules command (the human
        lawgiver mode), in force from ``day`` (default: the next dawn)."""
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        fd, path = tempfile.mkstemp(suffix=".lua")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)
            args = [game.pk, path]
            if day is not None:
                args += ["--day", str(day)]
            call_command("set_rules", *args, stdout=StringIO())
        finally:
            os.unlink(path)

    def test_quota_law_denies_takes_above_two(self):
        """Scenario 1: validate_action denies an over-quota take outright
        (deny, not clamp); the original request stays public in the log."""
        game = Game.objects.create(day=3, phase="night", agents=False, hoard=250)
        self._enact(
            game,
            """
            function validate_action(name, action, state)
                if action.type == "take" and action.amount > 2 then
                    return false
                end
                return true
            end
            """,
            day=3,
        )
        bram = Thief.objects.create(game=game, name="Bram", gold=10, take_policy=5)
        sable = Thief.objects.create(game=game, name="Sable", gold=0, take_policy=2)
        run_next_beat(game)
        bram.refresh_from_db()
        sable.refresh_from_db()
        # Denied, not clamped: the over-quota request yields nothing at all,
        # while the at-quota request goes through untouched.
        self.assertEqual(bram.gold, 10)
        self.assertEqual(sable.gold, 2)
        takes = game.events.get(type="takes")
        self.assertEqual(takes.payload["takes"], {"Bram": 0, "Sable": 2})
        # The denial is public: the request shows 5, the take shows 0.
        self.assertEqual(takes.payload["requested"], {"Bram": 5, "Sable": 2})
        self.assertEqual(takes.payload["hoard_after"], 248)
        self.assertFalse(game.events.filter(type="rule_error").exists())

    def test_fine_law_fines_overdraw_via_adjust_score(self):
        """Scenario 2: on_night_theft fines 5 gold per coin over 3 through
        adjust_score; the fine is its own public event and the hoard never
        feels it."""
        game = Game.objects.create(day=3, phase="night", agents=False, hoard=250)
        self._enact(
            game,
            """
            function on_night_theft(name, amount, state)
                if amount > 3 then
                    adjust_score(name, -5 * (amount - 3), "fine for greed")
                end
            end
            """,
            day=3,
        )
        bram = Thief.objects.create(game=game, name="Bram", gold=0, take_policy=5)
        sable = Thief.objects.create(game=game, name="Sable", gold=0, take_policy=0)
        run_next_beat(game)
        bram.refresh_from_db()
        sable.refresh_from_db()
        # Took 5, fined 10: the balance may go negative - debt is legal.
        self.assertEqual(bram.gold, -5)
        self.assertEqual(sable.gold, 0)
        takes = game.events.get(type="takes")
        self.assertEqual(takes.payload["takes"], {"Bram": 5, "Sable": 0})
        fine = game.events.get(type="score_adjust")
        self.assertEqual(fine.phase, "night")
        self.assertEqual(
            fine.payload, {"thief": "Bram", "amount": -10, "reason": "fine for greed"}
        )
        # Fines move gold only: the hoard feels the take, never the fine.
        self.assertEqual(takes.payload["hoard_after"], 245)
        self.assertFalse(game.events.filter(type="rule_error").exists())

    def test_speech_act_deposits_to_the_scratchpad_vault(self):
        """Scenario 3: on_public_message sees the public debate as speech
        acts; a matching line moves gold into the scratchpad vault via
        adjust_score."""
        game = Game.objects.create(day=3, phase="moot", agents=True)
        bram = Thief.objects.create(game=game, name="Bram", gold=10)
        Thief.objects.create(game=game, name="Sable", gold=5)
        self._enact(
            game,
            """
            function on_public_message(name, text, state)
                local amount = tonumber(string.match(text, "I deposit (%d+)"))
                if amount then
                    adjust_score(name, -amount, "vault deposit")
                    state.scratchpad.vault = (state.scratchpad.vault or 0) + amount
                end
            end
            """,
            day=3,
        )

        def transport(messages):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "THE FLOOR - VOTE" in user:
                if "You are Sable." in system:
                    return '{"votes": {"Bram": "no"}}'
                return '{"votes": {"Bram": "yes"}}'
            if "MOOT TRANSCRIPT SO FAR" in user:
                if "You are Bram." in system and "debate round 1 of the Moot" in user:
                    return '{"speak": true, "text": "I deposit 5."}'
                return '{"speak": false}'
            if "PROPOSALS ON THE TABLE" in user:
                if "You are Sable." in system:
                    return '{"second": ["Bram"]}'
                return '{"second": []}'
            if "You are Bram." in system:
                return '{"propose": true, "text": "The vault shall open."}'
            return '{"propose": false}'

        llm.client = LlmClient(transport=transport)
        run_next_beat(game)  # must not raise
        bram.refresh_from_db()
        # The deposit moved gold out of Bram's hands and into the vault.
        self.assertEqual(bram.gold, 5)
        self.assertEqual(game.scratchpad, {"vault": 5})
        deposit = game.events.get(type="score_adjust")
        self.assertEqual(
            deposit.payload, {"thief": "Bram", "amount": -5, "reason": "vault deposit"}
        )
        self.assertFalse(game.events.filter(type="rule_error").exists())

    def test_announce_reaches_every_thiefs_context(self):
        """Scenario 4: an announce call at dawn lands in the public day
        section of every thief's prompt, like a goal payout."""
        game = Game.objects.create(day=3, phase="dawn", agents=False, hoard=250)
        bram = Thief.objects.create(game=game, name="Bram", gold=10)
        sable = Thief.objects.create(game=game, name="Sable", gold=5)
        self._enact(
            game,
            """
            function on_day_start()
                announce("The crier proclaims a night curfew from tomorrow.")
            end
            """,
            day=3,
        )
        run_next_beat(game)
        event = game.events.get(type="announce")
        self.assertEqual(event.phase, "dawn")
        self.assertEqual(
            event.payload, {"text": "The crier proclaims a night curfew from tomorrow."}
        )
        for thief in (bram, sable):
            self.assertIn(
                "Announcement: The crier proclaims a night curfew from tomorrow.",
                context(thief),
            )

    def test_broken_rule_leaves_state_untouched_and_the_beat_completes(self):
        """Scenario 5: a rule error keeps the old scratchpad, logs an
        audience-only rule_error event, and the beat finishes normally."""
        game = Game.objects.create(day=3, phase="dawn", agents=False)
        game.scratchpad = {"marker": "keep"}
        game.save()
        bram = Thief.objects.create(game=game, name="Bram", gold=10)
        sable = Thief.objects.create(game=game, name="Sable", gold=5)
        self._enact(
            game,
            'function on_day_start(state) error("the law is broken") end',
            day=3,
        )
        run_next_beat(game)  # must not raise
        game.refresh_from_db()
        bram.refresh_from_db()
        sable.refresh_from_db()
        # The old scratchpad survives and no gold moved anywhere.
        self.assertEqual(game.scratchpad, {"marker": "keep"})
        self.assertEqual(bram.gold, 10)
        self.assertEqual(sable.gold, 5)
        # The audience sees the malfunction; the beat still ran to its end.
        error = game.events.get(type="rule_error")
        self.assertEqual(error.payload["hook"], "on_day_start")
        self.assertIn("the law is broken", error.payload["error"])
        self.assertTrue(game.events.filter(type="dawn_report").exists())
        self.assertFalse(game.events.filter(type="announce").exists())
        self.assertFalse(game.events.filter(type="score_adjust").exists())
        # Thieves never see rule errors, in any prompt.
        for thief in (bram, sable):
            self.assertNotIn("the law is broken", context(thief))

    def test_bricked_inactive_shape_never_persists_and_logs_rule_error(self):
        """A returned scratchpad whose 'inactive' is not a list of names
        (e.g. [{}]) is treated as a hook error: the old scratchpad is kept,
        a rule_error is logged, and later active_thieves() calls cannot be
        bricked by an unhashable 'inactive' entry."""
        game = Game.objects.create(day=3, phase="dawn", agents=False)
        game.scratchpad = {"marker": "keep"}
        game.save()
        bram = Thief.objects.create(game=game, name="Bram", gold=10)
        Thief.objects.create(game=game, name="Sable", gold=5)
        self._enact(
            game,
            "function on_day_start(state) state.scratchpad.inactive = {{}} end",
            day=3,
        )
        run_next_beat(game)  # must not raise
        game.refresh_from_db()
        bram.refresh_from_db()
        # The brick never persisted: the old scratchpad survives untouched.
        self.assertEqual(game.scratchpad, {"marker": "keep"})
        # The run is treated as a hook error, with a clear audience log.
        error = game.events.get(type="rule_error")
        self.assertEqual(error.payload["hook"], "on_day_start")
        self.assertIn("inactive", error.payload["error"])
        # The beat completed, and later beats still see every thief.
        self.assertTrue(game.events.filter(type="dawn_report").exists())
        run_next_beat(game)  # dawn -> morning parley (no-op beat)
        self.assertEqual(bram.gold, 10)

    def test_bad_adjust_score_amounts_fold_into_rule_error(self):
        """adjust_score with a float, a bool, or an out-of-range integer is
        skipped: no gold moves, no exception, and each bad call logs an
        audience rule_error. Floats are never truncated."""
        game = Game.objects.create(day=3, phase="dawn", agents=False)
        bram = Thief.objects.create(game=game, name="Bram", gold=10)
        Thief.objects.create(game=game, name="Sable", gold=5)
        self._enact(
            game,
            """
            function on_day_start()
                adjust_score("Bram", 3.5, "float fine")
                adjust_score("Bram", true, "bool fine")
                adjust_score("Bram", math.floor(10^10), "huge fine")
                adjust_score("Bram", 2, "real fine")
            end
            """,
            day=3,
        )
        run_next_beat(game)  # must not raise
        bram.refresh_from_db()
        # Only the plain integer call applied: 10 + 2.
        self.assertEqual(bram.gold, 12)
        errors = list(game.events.filter(type="rule_error").order_by("id"))
        self.assertEqual(len(errors), 3)
        self.assertIn("not an integer", errors[0].payload["error"])
        self.assertIn("not an integer", errors[1].payload["error"])
        self.assertIn("out of range", errors[2].payload["error"])
        # The one good call is a public score_adjust event.
        adjust = game.events.get(type="score_adjust")
        self.assertEqual(
            adjust.payload, {"thief": "Bram", "amount": 2, "reason": "real fine"}
        )

    def test_no_ruleset_never_forks_and_matches_plain_behavior(self):
        """Scenario 6: with a blank statute book no sandbox child is ever
        forked; a full day runs exactly as before."""
        from unittest import mock

        game = Game.objects.create(day=1, phase="dawn", agents=False, hoard=250)
        bram = Thief.objects.create(game=game, name="Bram", gold=0, take_policy=2)
        sable = Thief.objects.create(game=game, name="Sable", gold=0, take_policy=3)
        with mock.patch(
            "main.beats.run_hook_isolated",
            side_effect=AssertionError("no ruleset: must not fork"),
        ):
            for _ in range(6):
                run_next_beat(game)
        bram.refresh_from_db()
        sable.refresh_from_db()
        # Plain mechanics: the takes went through, dawn reported the scores.
        self.assertEqual(bram.gold, 2)
        self.assertEqual(sable.gold, 3)
        takes = game.events.get(day=1, type="takes")
        self.assertEqual(takes.payload["takes"], {"Bram": 2, "Sable": 3})
        self.assertTrue(game.events.filter(day=1, type="dawn_report").exists())
        self.assertFalse(game.events.filter(type="rule_error").exists())

    def test_day_page_renders_announce_and_rule_error(self):
        """The audience page shows the law's announcements and its failures."""
        game = Game.objects.create(day=2, agents=False)
        Thief.objects.create(game=game, name="Bram")
        Event.objects.create(
            game=game,
            day=1,
            phase="dawn",
            type="announce",
            payload={"text": "The crier speaks."},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="dawn",
            type="score_adjust",
            payload={"thief": "Bram", "amount": -5, "reason": "fine for greed"},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="night",
            type="rule_error",
            payload={"hook": "on_night_theft", "error": "boom"},
        )
        response = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertContains(response, "Announcement: The crier speaks.")
        self.assertContains(
            response, "The law adjusts Bram's gold by -5: fine for greed."
        )
        self.assertContains(response, "The law malfunctioned (on_night_theft: boom).")

    def test_day_page_renders_moot_phase_law_acts(self):
        """Moot-phase announce/rule_error/score_adjust events appear in a
        'The law acts' list in the Moot section, even with no proposals."""
        game = Game.objects.create(day=2, agents=False)
        Thief.objects.create(game=game, name="Bram")
        Event.objects.create(
            game=game,
            day=1,
            phase="moot",
            type="announce",
            payload={"text": "The crier speaks."},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="moot",
            type="score_adjust",
            payload={"thief": "Bram", "amount": 2, "reason": ""},
        )
        Event.objects.create(
            game=game,
            day=1,
            phase="moot",
            type="rule_error",
            payload={"hook": "on_moot_end", "error": "boom"},
        )
        response = self.client.get(f"/game/{game.pk}/day/1/")
        self.assertContains(response, "The law acts")
        self.assertContains(response, "Announcement: The crier speaks.")
        self.assertContains(response, "The law adjusts Bram's gold by 2.")
        self.assertContains(response, "The law malfunctioned (on_moot_end: boom).")


class GuildMagicPromptTests(TestCase):
    """Thief-facing copy about the guild's magic: the full capability list
    in the rules prose, and the enforced/prose-only markers in the law
    book."""

    def _flat_prompt(self, game):
        """The system prompt with line wraps collapsed, so assertions match
        the prose regardless of how it is hard-wrapped."""
        return " ".join(system_prompt(game.thieves.first()).split())

    def test_system_prompt_tells_every_power_of_the_magic(self):
        game = Game.objects.create()
        Thief.objects.create(game=game, name="Bram")
        prompt = self._flat_prompt(game)
        self.assertIn("THE GUILD'S MAGIC", prompt)
        self.assertIn("adjust any thief's gold (every transfer is logged)", prompt)
        self.assertIn("announce words the whole village hears", prompt)
        self.assertIn("keep records on the village slate", prompt)
        self.assertIn("the list of the dead and the exiled", prompt)
        self.assertIn("read what is spoken at the public Moot", prompt)
        self.assertIn("words there can carry mechanical weight", prompt)
        # Letter, not intent; invisible magic; void when beyond the magic.
        self.assertIn("letter of the law's prose, not its intent", prompt)
        self.assertIn("never see the magic itself", prompt)
        self.assertIn("beyond its magic", prompt)
        self.assertIn("void", prompt)

    def test_system_prompt_never_mentions_interception_or_metadata(self):
        game = Game.objects.create()
        Thief.objects.create(game=game, name="Bram")
        prompt = self._flat_prompt(game)
        self.assertNotIn("intercept", prompt)
        self.assertNotIn("metadata", prompt)
        self.assertNotIn("revealing that a meeting", prompt)

    def test_law_book_marks_enforced_and_prose_only_laws(self):
        game = Game.objects.create(day=2, phase="moot", hoard=242)
        bram = Thief.objects.create(game=game, name="Bram", gold=12)
        sable = Thief.objects.create(game=game, name="Sable", gold=5)
        enforced = Proposal.objects.create(
            game=game,
            day=1,
            author=bram,
            text="No thief shall take more than 3 coins.",
            status="law",
        )
        RuleSet.objects.create(game=game, day=2, code="-- compiled", proposal=enforced)
        Proposal.objects.create(
            game=game,
            day=1,
            author=sable,
            text="All takes shall be declared at the Moot.",
            status="law",
        )
        text = context(bram)
        self.assertIn(
            "No thief shall take more than 3 coins. [enforced by the guild's magic]",
            text,
        )
        self.assertIn(
            "All takes shall be declared at the Moot. [prose only - no magic backs it]",
            text,
        )
