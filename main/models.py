from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    pass


PHASES = [
    ("dawn", "Dawn"),
    ("morning_parley", "Morning parley"),
    ("moot", "Moot"),
    ("dusk_parley", "Dusk parley"),
    ("night", "Night"),
    ("implementor", "Implementor"),
]

STATUSES = [
    ("running", "Running"),
    ("ended", "Ended"),
    ("burned", "Burned"),
]

PROPOSAL_STATUSES = [
    ("submitted", "Submitted"),
    ("floor", "Floor"),
    ("failed", "Failed"),
    ("passed", "Passed"),
    ("law", "Law"),
]

VOTES = [
    ("yes", "Yes"),
    ("no", "No"),
    ("abstain", "Abstain"),
]

WINDOWS = [
    ("morning", "Morning"),
    ("dusk", "Dusk"),
]


class Game(models.Model):
    hoard = models.IntegerField(default=250)
    day = models.IntegerField(default=1)
    phase = models.CharField(max_length=32, choices=PHASES, default="dawn")
    wakes = models.IntegerField(default=0)
    rage = models.BooleanField(default=False)
    status = models.CharField(max_length=32, choices=STATUSES, default="running")
    agents = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Game {self.pk}"


class Thief(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="thieves")
    name = models.CharField(max_length=100)
    gold = models.IntegerField(default=0)
    take_policy = models.IntegerField(
        default=0, choices=[(i, str(i)) for i in range(6)]
    )
    persona = models.TextField(blank=True, default="")
    diary = models.TextField(blank=True, default="")

    def __str__(self):
        return self.name


class Event(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="events")
    day = models.IntegerField()
    phase = models.CharField(max_length=32, choices=PHASES)
    type = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.type} on day {self.day} ({self.phase})"


class Proposal(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="proposals")
    day = models.IntegerField()
    author = models.ForeignKey(Thief, on_delete=models.CASCADE, related_name="proposals")
    text = models.TextField()
    status = models.CharField(
        max_length=32, choices=PROPOSAL_STATUSES, default="submitted"
    )
    yes = models.IntegerField(default=0)
    no = models.IntegerField(default=0)
    abstain = models.IntegerField(default=0)
    seconded_by = models.ManyToManyField(Thief, related_name="seconded_proposals", blank=True)

    def __str__(self):
        return f"{self.author}'s proposal on day {self.day} ({self.status})"


class Ballot(models.Model):
    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="ballots")
    thief = models.ForeignKey(Thief, on_delete=models.CASCADE, related_name="ballots")
    choice = models.CharField(max_length=32, choices=VOTES)

    def __str__(self):
        return f"{self.thief} votes {self.choice} on {self.proposal}"


class DebateMessage(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="debate_messages")
    day = models.IntegerField()
    round = models.IntegerField()
    thief = models.ForeignKey(Thief, on_delete=models.CASCADE, related_name="debate_messages")
    text = models.TextField(blank=True, default="")
    order = models.IntegerField()

    def __str__(self):
        return f"{self.thief} in debate on day {self.day} (round {self.round})"


class Parley(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="parleys")
    day = models.IntegerField()
    window = models.CharField(max_length=32, choices=WINDOWS)
    opener = models.ForeignKey(Thief, on_delete=models.CASCADE, related_name="opened_parleys")
    participants = models.ManyToManyField(Thief, related_name="parleys", blank=True)

    def __str__(self):
        return f"Parley on day {self.day} ({self.window})"


class ParleyMessage(models.Model):
    parley = models.ForeignKey(Parley, on_delete=models.CASCADE, related_name="messages")
    round = models.IntegerField()
    thief = models.ForeignKey(Thief, on_delete=models.CASCADE, related_name="parley_messages")
    text = models.TextField(blank=True, default="")
    order = models.IntegerField()

    def __str__(self):
        return f"{self.thief} in {self.parley} (round {self.round})"


class LlmCall(models.Model):
    game = models.ForeignKey(
        Game, on_delete=models.CASCADE, related_name="llm_calls", null=True, blank=True
    )
    thief = models.ForeignKey(
        Thief, on_delete=models.CASCADE, related_name="llm_calls", null=True, blank=True
    )
    day = models.IntegerField(null=True, blank=True)
    phase = models.CharField(max_length=32, choices=PHASES, null=True, blank=True)
    purpose = models.CharField(max_length=100, blank=True, default="")
    messages = models.JSONField(default=list)
    response = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"LlmCall {self.pk} ({self.purpose})"
