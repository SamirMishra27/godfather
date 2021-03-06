from godfather.roles.mixins import SingleAction, Townie
from godfather.game.types import Priority

DESCRIPTION = 'You may watch one person at night to see who visits them.'


class Lookout(Townie, SingleAction):
    """
    Some think he has got eagle’s eyes. An observer who is master at camping outside homes to gain information.

    - Win Condition: lynch every criminal and evildoer

    + Abilities: Choose to watch one person at night to see who visits them.
    + You will exactly know who visited your target at night, showing their names.
    """
    name = 'Lookout'
    description = DESCRIPTION

    def __init__(self):
        super().__init__()
        self.action = 'watch'
        self.action_gerund = 'watching'
        self.action_priority = Priority.LOOKOUT
        self.action_text = 'watch a player'
        self.categories.append('Town Investigative')

    async def tear_down(self, _actions, player, target):
        if len(target.visitors) > 1:
            # get all visitors except self
            visitors = [*filter(lambda v: v.user.id !=
                                player.user.id, target.visitors)]
            # turn all visitors objects into their names
            visitors = map(lambda v: v.user.name, visitors)
            await player.user.send('Your target was visited by {}'.format(', '.join(visitors)))
        else:
            await player.user.send('Your target was visited by no one')
