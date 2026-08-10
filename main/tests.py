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
    Thief,
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
        # Public scores come from the last dawn report.
        self.assertIn("Bram 12", text)
        self.assertIn("Sable 5", text)
        # Law book: only enacted proposals.
        self.assertIn("No thief shall take more than 3 coins.", text)
        self.assertIn("Day 1, Sable:", text)
        # Public Moot business: proposals and the public tally.
        self.assertIn("All takes shall be declared at the Moot.", text)
        self.assertIn("yes 1, no 1, abstain 1", text)
        self.assertIn("I propose we publish all takes.", text)
        # Own eyes only: own take, own ballot, own parley, own diary.
        self.assertIn("Your take that night: 3 coins.", text)
        self.assertIn("Your ballot: yes on Bram's proposal.", text)
        self.assertIn("Your parley (dusk): opened by Bram; present: Bram, Sable.", text)
        self.assertIn("I will take three tonight, trust me.", text)
        self.assertIn("I dreamt of gold again.", text)

    def test_context_hides_foreign_information(self):
        _game, bram, _sable, _merrick = self._make_game()
        text = context(bram)
        # Another thief's take never appears, even though it is in the DB.
        self.assertNotIn("Your take that night: 5", text)
        self.assertNotIn("Sable: 5", text)
        # Another thief's individual ballots never appear; only the tally did.
        self.assertNotIn("Your ballot: no", text)
        self.assertNotIn("Your ballot: abstain", text)
        self.assertNotIn("Sable voted", text)
        # A parley Bram did not join is invisible: no content, no existence.
        self.assertNotIn("The Bram must never hear of this plan.", text)
        self.assertNotIn("Your parley (morning)", text)

    def test_context_on_fresh_game(self):
        game = Game.objects.create()
        bram = Thief.objects.create(game=game, name="Bram")
        text = context(bram)
        self.assertIn("the statute book is blank", text)
        self.assertIn("You have seen nothing yet in the last few days.", text)
        self.assertIn("YOUR DIARY: empty", text)


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
        self.assertEqual(len(messages), 4)  # 2 participants, 2 rounds
        bram_rows = [m for m in messages if m.thief.name == "Bram"]
        sable_rows = [m for m in messages if m.thief.name == "Sable"]
        self.assertTrue(all(m.text == "" for m in bram_rows))  # passed every round
        self.assertTrue(all(m.text == "Hi." for m in sable_rows))
        bram_calls = LlmCall.objects.filter(thief__name="Bram", purpose="parley_speak")
        self.assertEqual(bram_calls.count(), 2)
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
