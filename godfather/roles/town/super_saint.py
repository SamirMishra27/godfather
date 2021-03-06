import asyncio
from godfather.roles.base import Role
from godfather.roles.mixins import Townie

DESCRIPTION = 'You will blow up the last person to lynch you!'


class SuperSaint(Townie, Role):
    """
    A vengeful lunatic who will explode on getting lynched.

    - Win Condition: Lynch every criminal and evildoer

    + Abilities: Once the super saint gets lynched during day, the person last voted to lynch will die.
    """
    name = 'Super Saint'
    description = DESCRIPTION

    async def on_lynch(self, game, player):
        last_voted = game.votes[player.user.id][-1]
        last_voted.is_alive = False
        async with game.channel.typing():
            await game.channel.send('💣 **BOOOOOOOOOOOOOOM!!!**')
            await asyncio.sleep(2)
            await game.channel.send(f'{last_voted.user} hammered the super saint and was blown up! He was a *{last_voted.display_role}*')
