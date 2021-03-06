import unittest
from unittest.mock import Mock, patch, PropertyMock

from godfather.game import Player


class MockFaction(Mock):
    name: str
    id: str

    def __str__(self):
        return self.name


class PlayerTestCase(unittest.TestCase):
    def test_role_pm(self):
        mock_user = Mock()
        mock_user.return_value = 'LemonGrass#3333'
        mock_role = Mock(description='Eats a lot of lemons.')
        mock_faction = Mock(win_con='Gets rid of lemons.')

        with patch(
            'godfather.game.player.Player.display_role', new_callable=PropertyMock
        ) as mock_display_role:
            mock_display_role.return_value = 'Neutral Role'
            player = Player(user=mock_user)
            player.user = 'LemonGrass#3333'
            player.role = mock_role
            player.role.faction = mock_faction

            expected_str = 'Hello LemonGrass#3333, you are a ' \
                '**Neutral Role**. ' \
                'Eats a lot of lemons.\nWin Condition: ' \
                'Gets rid of lemons.'
            self.assertEqual(player.role_pm, expected_str)

    def test_innocent(self):
        test_values = (
            (True, 'town', True),
            (True, 'not town', True),
            (False, 'town', False),
            (False, 'not town', False),
            (None, 'town', True),
            (None, 'not town', False)
        )

        for innocence_modifier, role_id, expected_bool in test_values:
            with self.subTest(innocence_modifier=innocence_modifier,
                              role_id=role_id, expected_bool=expected_bool):
                mock_user = Mock()
                if innocence_modifier is None:
                    mock_role = Mock(spec=False)
                else:
                    mock_role = Mock(**{
                        'innocence_modifier.return_value': innocence_modifier
                    })
                player = Player(mock_user)
                player.role = mock_role
                self.assertIs(player.innocent, expected_bool)

    def test_display_role(self):
        test_values = (
            ('neutral', 'Joker'),
            ('town', 'Town Joker'),
            ('mafia', 'Mafia Joker')
        )

        for faction_id, expected_str in test_values:
            with self.subTest(faction_id=faction_id, expected_str=expected_str):
                mock_faction = MockFaction()
                mock_faction.id = faction_id
                mock_faction.name = faction_id.capitalize()
                player = Player(Mock())
                player.role = Mock(**{
                    'display_role.return_value': 'Joker'
                })
                player.role.faction = mock_faction
                self.assertEqual(player.display_role, expected_str)
