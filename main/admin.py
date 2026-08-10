from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Ballot,
    DebateMessage,
    Event,
    Game,
    LlmCall,
    Parley,
    ParleyMessage,
    Proposal,
    Thief,
    User,
)

admin.site.register(User, UserAdmin)


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("pk", "day", "phase", "status", "hoard", "created")
    list_filter = ("phase", "status")


@admin.register(Thief)
class ThiefAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "gold", "take_policy")
    list_filter = ("game",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("game", "day", "phase", "type", "created")
    list_filter = ("phase", "type")


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = ("game", "day", "author", "status", "yes", "no", "abstain")
    list_filter = ("status", "day")


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ("proposal", "thief", "choice")
    list_filter = ("choice",)


@admin.register(DebateMessage)
class DebateMessageAdmin(admin.ModelAdmin):
    list_display = ("game", "day", "round", "thief", "order")
    list_filter = ("day", "round")


@admin.register(Parley)
class ParleyAdmin(admin.ModelAdmin):
    list_display = ("game", "day", "window", "opener")
    list_filter = ("window", "day")


@admin.register(ParleyMessage)
class ParleyMessageAdmin(admin.ModelAdmin):
    list_display = ("parley", "round", "thief", "order")
    list_filter = ("round",)


@admin.register(LlmCall)
class LlmCallAdmin(admin.ModelAdmin):
    list_display = ("pk", "game", "thief", "day", "phase", "purpose", "created")
    list_filter = ("phase", "purpose")
