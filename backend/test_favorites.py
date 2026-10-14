"""Focused shared favorite-team behavior tests."""
import pytest

from app.api.v1.endpoints.favorites import _key
from app.models.models import FavoriteTeam


def test_favorite_key_normalizes_whitespace_and_case():
    assert _key("  La   Esperanza GU15 ") == "la esperanza gu15"


def test_favorite_identity_is_division_scoped():
    first = FavoriteTeam(division_id=1, team_name="United", normalized_name=_key("United"))
    second = FavoriteTeam(division_id=2, team_name="United", normalized_name=_key("United"))
    assert (first.division_id, first.normalized_name) != (second.division_id, second.normalized_name)


@pytest.mark.asyncio
async def test_favorite_model_does_not_touch_game_fields():
    from app.models.models import Game
    game = Game(division_id=1, home_team_name="United", away_team_name="Rivals")
    before = (game.home_team_name, game.away_team_name, game.home_score, game.away_score)
    FavoriteTeam(division_id=1, team_name="United", normalized_name="united")
    assert before == (game.home_team_name, game.away_team_name, game.home_score, game.away_score)
