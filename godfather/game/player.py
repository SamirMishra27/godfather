import typing
import discord
from discord.ext import commands
from discord.ext.commands import errors


conv = commands.MemberConverter()

INNOCENT_FACTIONS = ['town', 'neutral.executioner', 'neutral.jester', 'neutral.survivor', 'neutral.amnesiac']


class Player:
    def __init__(self, user: discord.Member):
        self.user = user
        self.role = None
        self.faction = None
        self.is_alive = True
        self.votes: typing.List[Player] = []
        self.death_reason = ''
        self.visitors = []
        # when roles change: Goon -> GF, Exe -> Jester
        self.previous_roles = []
        # retributionist stuff
        self.is_revived = False
        self.revived_on = None

    async def send_pm(self, game, no_teammates=False):
        role_pm = (
            f'Hello {self.user}, you are a **{self.display_role}**. '
            f'{self.role.description}'  # This follows the previous line.
            f'\nWin Condition: {self.role.faction.win_con}'
        )

        await self.user.send(role_pm)
        if no_teammates:
            return
        if self.role.faction.informed:
            teammates = map(lambda player: f'{player.user.name} ({player.role.name})', game.players.filter(
                faction=self.role.faction.id, is_alive=True))
            if len(teammates) > 1:
                await self.user.send('Your team consists of: {}'.format(', '.join(teammates)))

    # generates the role's PM
    @property
    def role_pm(self):
        return (
            f'Hello {self.user}, you are a **{self.display_role}**. '
            f'{self.role.description}'  # This follows the previous line.
            f'\nWin Condition: {self.role.faction.win_con}'
        )

    @property
    def innocent(self):
        if hasattr(self.role, 'innocence_modifier'):
            return self.role.innocence_modifier()
        if self.role.faction.id in INNOCENT_FACTIONS:
            return True
        return False

    @property
    def full_role(self):
        all_roles = self.previous_roles + [self.role]
        if self.role.faction.id.startswith('neutral'):
            return ' -> '.join(map(str, all_roles))
        return ' -> '.join(map(str, all_roles))

    @property
    def display_role(self):
        if self.role.cleaned:
            return 'Cleaned'
        if self.role.faction.id.startswith('neutral'):
            return self.role.display_role()
        return f'{self.role.faction} {self.role.display_role()}'

    async def visit(self, visitor, actions):
        if visitor == self:
            return
        self.visitors.append(visitor)
        if hasattr(self.role, 'on_visit'):
            await self.role.on_visit(self, visitor, actions)

    # remove vote by 'user' from player
    def remove_vote(self, user: discord.Member):
        self.votes = [*filter(lambda p: p.user.id != user.id, self.votes)]

    # check if player is voted by 'user'
    def has_vote(self, user: discord.Member):
        return any(player.user.id == user.id for player in self.votes)

    # remove a player from the game
    async def remove(self, game, reason, modkill=False):
        self.is_alive = False
        self.votes = []
        self.death_reason = reason
        if hasattr(self.role, 'on_death') and not modkill:
            await self.role.on_death(game, self)

    @classmethod
    async def convert(cls, ctx, argument):
        # follow the strategy: numbers, names, standard discord.py conversions
        game = ctx.bot.games[ctx.channel.id]
        if argument.isdigit() and \
                int(argument) > 0 and \
                int(argument) <= len(game.players):
            return game.players[int(argument) - 1]

        found_member = None
        # case sensitive username search
        for member in ctx.guild.members:
            if member.name.lower() == argument.lower():
                found_member = member

        if found_member is None:
            found_member = await conv.convert(ctx, argument)
        if found_member is None or not found_member in game.players:
            raise errors.BadArgument('Player {} not found'.format(argument))
        return game.players[found_member]
