import math
from datetime import datetime, timedelta
from enum import IntEnum, auto

import discord

from godfather.errors import PhaseChangeError
from godfather.game.game_config import GameConfig, GameConfigException
from godfather.game.player_manager import PlayerManager
from godfather.game.vote_manager import VoteManager
from godfather.utils import alive_or_recent_jester, choice
from godfather.game.types import STALEMATE_PRIORITY_ORDER

from .night_actions import NightActions
from .player import Player

IDLE_TIMEOUT = 15 * 60  # 15 minutes

DEFAULT_CONFIG = {
    'day_duration': 5 * 60,
    'night_duration': 2 * 60,
    'max_players': None
}


def resolve_duration(arg: str):
    if not arg.isdigit():
        raise GameConfigException('Duration must be a valid number.')
    num = int(arg)
    if num < 30 or num > 1800:
        raise GameConfigException(
            'Duration must be between 30 seconds and 30 minutes.')
    return num


def resolve_max_players(arg: str):
    if arg == 'reset':
        return 18
    if not arg.isdigit():
        raise GameConfigException('Duration must be a valid number.')
    num = int(arg)
    if num < 3 or num > 18:
        raise GameConfigException('Maximum players must be between 3 and 18.')
    return num


def config_message(key, value):
    if key == 'day_duration':
        return 'Days will now last {} minutes.'.format(round(value / 60, 1))
    elif key == 'night_duration':
        return 'Nights will now last {} minutes.'.format(round(value / 60, 1))
    elif key == 'max_players':
        return 'This game will now accept up-to {} players'.format(value)


class Phase(IntEnum):
    PREGAME = auto()
    DAY = auto()
    NIGHT = auto()
    STANDBY = auto()


class Game:
    def __init__(self, channel: discord.channel.TextChannel, bot):
        self.channel = channel
        self.bot = bot
        self.players = PlayerManager(self)
        self.phase = Phase.PREGAME
        self.cycle = 0
        # time at which the current phase ends
        self.phase_end_at: datetime = None
        self.night_actions = NightActions(self)
        self.setup = None  # the setup used
        # host-configurable stuff
        self.config = GameConfig(DEFAULT_CONFIG)
        self.config.add_key('day_duration', resolve_duration, config_message)
        self.config.add_key(
            'night_duration', resolve_duration, config_message)
        self.config.add_key('max_players', resolve_max_players, config_message)

        self.votes = VoteManager(self)
        # for drawing by timeout
        self.day_with_no_lynch = False
        self.night_with_no_kills = False
        self.cycles_with_no_kills = 0
        # for deleting idle games
        self.created_at = None

    @ classmethod
    def create(cls, ctx, bot):
        new_game = cls(ctx.channel, bot)
        new_game.players.add(ctx.author)
        new_game.created_at = datetime.now()
        return new_game

    async def update(self):
        if not self.has_started:
            diff = datetime.now() - self.created_at
            if diff.seconds >= IDLE_TIMEOUT:
                await self.channel.send('The game took too long to start, deleting it.')
                self.bot.games.pop(self.channel.id)
                return

        if self.phase == Phase.STANDBY:
            return

        curr_t = datetime.now()
        phase_end = self.phase_end_at
        if phase_end is not None and curr_t > phase_end:
            if self.phase == Phase.DAY:
                # no lynch achieved
                self.day_with_no_lynch = True
                if self.night_with_no_kills and self.day_with_no_lynch:
                    # cycle with no kills
                    self.cycles_with_no_kills += 1
                    self.night_with_no_kills = False
                    self.day_with_no_lynch = False

                await self.channel.send('Nobody was lynched')
            try:
                await self.increment_phase()
            except Exception as exc:
                raise PhaseChangeError(None, *exc.args)

    # finds a setup for the current player-size. if no setup is found, raises an Exception
    async def find_setup(self, setup_name: str = None):
        num_players = len(self.players)

        if setup_name:
            setup = self.bot.setups.get(setup_name)

            if not setup:
                raise ValueError('Setup not found.')

            if not setup.total_players == num_players:
                raise ValueError(
                    f'Chosen setup needs {setup.total_players} players, '
                    f'you currently have {num_players}'
                )

            return setup

        possible_setups = dict(filter(lambda s: s[1].total_players == num_players,
                                      self.bot.setups.items()))
        if len(possible_setups) == 0:
            # wip: custom exception types?
            raise ValueError('No possible setups found.')
        if len(possible_setups) == 1:
            return possible_setups[0]

        setup = await choice(
            self.bot, self.bot.get_user(self.host.id), self.channel,
            "Multiple setups found.\n"
            "Please choose one of the following:",
            list(possible_setups.keys())
        )
        if setup is None:
            raise ValueError('Prompt timed out.')
        return possible_setups.get(setup)

    # checks whether the game has ended, returns whether the game has ended and the winning faction
    def check_endgame(self):
        winning_faction = None
        independent_wins = []

        alive_players = self.players.filter(is_alive=True)

        for player in self.players:
            win_check = player.role.faction.has_won(self)
            if win_check:
                winning_faction = player.role.faction.name

            if hasattr(player.role.faction, 'has_won_independent'):
                independent_check = player.role.faction.has_won_independent(
                    player)
                if independent_check:
                    independent_wins.append(player)

        # draw by wipeout
        if len(alive_players) == 0:
            return (True, None, independent_wins)

        # 1v1s may need to be specially handled by the stalemate detector
        if len(alive_players) == 2:
            player1, player2 = alive_players
            if player1.role.name in STALEMATE_PRIORITY_ORDER and player2.role.name in STALEMATE_PRIORITY_ORDER:
                player1_priority, player2_priority = map(
                    lambda player: STALEMATE_PRIORITY_ORDER.index(player.role.name), alive_players)
                if player1_priority > player2_priority:
                    winning_faction = player1.role.faction.name
                else:
                    winning_faction = player2.role.faction.name

        if winning_faction:
            return (True, winning_faction, independent_wins)
        return (False, None, independent_wins)

    async def increment_phase(self):
        # If it is day, `phase_t` should be equal to night_duration and vice versa.
        # `phase_duration` is used at the end of the function.
        # `phase_t` is used in day/night starting messages.
        if self.phase == Phase.DAY:
            phase_duration = self.config['night_duration']
            phase_t = round(phase_duration / 60, 1)
        else:
            # Set it to day duration for all other phases.
            # Note that it should almost always be `Phase.NIGHT`.
            phase_duration = self.config['day_duration']
            phase_t = round(phase_duration / 60, 1)

        # night loop is the same as the pregame loop
        if self.cycle == 0 or self.phase == Phase.NIGHT:
            # resolve night actions
            self.phase = Phase.STANDBY  # so the event loop doesn't mess things up here
            dead_players = await self.night_actions.resolve()

            if len(dead_players) == 0 and self.cycle != 0:
                self.night_with_no_kills = True
            else:
                self.night_with_no_kills = False
                self.cycles_with_no_kills = 0

            if self.night_with_no_kills and self.day_with_no_lynch:
                # cycle with no kills
                self.cycles_with_no_kills += 1
                self.night_with_no_kills = False
                self.day_with_no_lynch = False

            for player in dead_players:
                role_text = 'We could not determine their role.' if player.role.cleaned else f'They were a {player.display_role}.'
                await self.channel.send(f'{player.user.name} died last night. {role_text}\n')

            # 3 consecutive nights w/o no kills = draw by timeout
            if self.cycles_with_no_kills >= 3:
                _, _, independent_wins = self.check_endgame()
                await self.channel.send('Nobody was killed in 3 consecutive cycles. Ending game...')
                return await self.end(None, independent_wins)

            game_ended, winning_faction, independent_wins = self.check_endgame()
            if game_ended:
                return await self.end(winning_faction, independent_wins)

            # clear visits
            for player in self.players:
                player.visitors.clear()

            # voting starts
            self.night_actions.reset()
            self.phase = Phase.DAY
            self.cycle = self.cycle + 1
            alive_players = self.players.filter(is_alive=True)
            # populate voting cache
            self.votes['nolynch'] = []
            self.votes['notvoting'] = []
            for player in alive_players:
                self.votes[player.user.id] = []
                self.votes['notvoting'].append(player)

            await self.channel.send(f'Day **{self.cycle}** will last {phase_t} minutes.'
                                    f' With {len(alive_players)} alive, it takes {self.majority_votes} to lynch.')
        else:
            self.phase = Phase.STANDBY
            # remove all votes from every player
            self.votes.clear()
            # 3 consecutive days with day timed out = auto-draw
            if self.cycles_with_no_kills >= 3:
                _, _, independent_wins = self.check_endgame()
                await self.channel.send('Nobody was lynched on 3 consecutive days. Ending game...')
                return await self.end(None, independent_wins)

            await self.channel.send(f'Night **{self.cycle}** will last {phase_t} minutes. '
                                    'Send in those actions quickly!')

            # recently lynched jesters and alive players are allowed to send in actions
            for player in filter(lambda p: alive_or_recent_jester(p, self), self.players):
                if hasattr(player.role, 'on_night'):
                    can_do, _ = player.role.can_do_action(self)
                    if not can_do:
                        continue
                    await player.role.on_night(self.bot, player, self)

            self.phase = Phase.NIGHT

        self.phase_end_at = datetime.now() \
            + timedelta(seconds=phase_duration)

    # lynch a player
    async def lynch(self, target: Player):
        async with self.channel.typing():
            await self.channel.send(f'{target.user.name} was lynched. He was a *{target.display_role}*.')
            await target.role.on_lynch(self, target)

        self.day_with_no_lynch = False
        self.cycles_with_no_kills = 0
        await target.remove(self, f'lynched D{self.cycle}')

    def replace(self, player: Player, replacement: discord.User):
        if self.phase == Phase.DAY:
            # swap all possible votes on player with the replacement
            votes_on_player = self.votes[player.user.id]
            self.votes[replacement.id] = votes_on_player
            del self.votes[player.user.id]
        player.user = replacement  

    # WIP: End the game
    # If a winning faction is not provided, game is ended
    # as if host ended the game without letting it finish
    async def end(self, winning_faction, independent_wins):
        bot = self.bot  # TODO: Move db stuff to separate func

        if winning_faction:
            await self.channel.send(f'The game is over. {winning_faction} wins! 🎉')
        else:
            await self.channel.send('The game is over. Nobody wins!')

        full_rolelist = '\n'.join(
            [f'{i+1}. {player.user.name} ({player.full_role})' for i, player in enumerate(self.players)])

        if independent_wins and len(independent_wins) > 0:
            ind_win_strings = [
                f'{player.user.name} ({player.role.name})' for player in independent_wins]
            await self.channel.send(f'Independent wins: {", ".join(ind_win_strings)}')

        await self.channel.send(f'**Final Rolelist**: ```{full_rolelist}```')
        del bot.games[self.channel.id]
        # update player stats
        if bot.db:
            with bot.db.conn.cursor() as cur:
                independent_win_roles = [
                    *map(lambda player: player.role.name, independent_wins)]
                cur.execute("INSERT INTO games (setup, winning_faction, independent_wins) VALUES (%s, %s, %s) RETURNING id;",
                            (self.setup.name, winning_faction, independent_win_roles))
                game_id, = cur.fetchone()
                with bot.db.conn.cursor() as cur2:
                    values = []
                    for player in self.players:
                        win = player in independent_wins or player.role.faction.name == winning_faction
                        values.append(cur2.mogrify('(%s, %s, %s, %s, %s)',
                                                   (player.user.id, player.role.faction.name, player.role.name, game_id, win)).decode('utf-8'))
                    query = "INSERT INTO players (player_id, faction, rolename, game_id, result) VALUES " + \
                        ",".join(values) + ";"
                    cur2.execute(query)

            bot.db.conn.commit()
            bot.logger.debug(
                'Added stats for {} players.'.format(len(self.players)))

    @ property
    def host(self):
        return self.players[0].user

    @ property
    def has_started(self):  # this might be useful
        return not self.phase == Phase.PREGAME

    @ property
    def majority_votes(self):
        return math.floor(len(self.players.filter(is_alive=True)) / 2) + 1
