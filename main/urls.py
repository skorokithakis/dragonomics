"""The main application's URLs."""

from django.urls import path

from . import views

app_name = "main"
urlpatterns = [
    path("", views.index, name="index"),
    # Both routes share one name so that a pk-only reverse resolves to the
    # latest-day default and a pk+day reverse to the explicit day.
    path("game/<int:pk>/", views.game_day, name="game_day"),
    path("game/<int:pk>/day/<int:day>/", views.game_day, name="game_day"),
]
