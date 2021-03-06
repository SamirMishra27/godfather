# This file is part of Godfather
# Copyright (C) 2020 Soumil07

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from datetime import datetime
import json
import logging
import pathlib

import discord

from discord.ext import commands
from godfather.database import DB
from godfather.custom_help import CustomHelp
from godfather.errors import PhaseChangeError
from godfather.utils import CustomContext, ColoredFormatter, getlogger, alive_or_recent_jester, pluralize
from godfather.game.setup import Setup, SetupLoadError
from godfather.game import Phase


config = json.load(open('config.json'))
global_prefix = config.get('prefix', '=')


def prefix_callable(bot, _msg: discord.Message):
    # The `discord.Message` parameter is required because of how `discord.py` calls it interally.
    bot_id = bot.user.id

    return [global_prefix, f'<@{bot_id}> ', f'<@!{bot_id}> ']


def remove_prefix(text, prefix):
    return text[text.startswith(prefix) and len(prefix):]


class Godfather(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=prefix_callable,
            help_command=CustomHelp(verify_checks=False),
            description='A Discord bot for automatically hosting games of Mafia/Werewolf.',
            activity=discord.Activity(
                type=discord.ActivityType.listening, name='{}help'.format(global_prefix))
        )

        self.__version__ = (0, 9, 3)
        self.__release__ = 'beta'
        # needed for showing uptime
        self.connected_at = None
        self.setups = {}
        self.games = {}
        self.db = None

        # set logger
        self.logger = getlogger(config.get('logging', dict()))

        # set Discord.py's logger to our custom one
        discord_logger = logging.getLogger('discord')
        handler = logging.StreamHandler()
        # setFormatter returns `None`, so it can't be used while chaining
        handler.setFormatter(ColoredFormatter())
        discord_logger.addHandler(handler)

        # Load all extensions.
        self.load_extensions()

        # setup Postgres
        if 'postgres' in config:
            self.db = DB(**config.get('postgres')
                         )  # pylint: disable=invalid-name

    @property
    def invite(self):
        return 'https://discord.com/oauth2/authorize?client_id={}&scope=bot'.format(self.user.id)

    def run(self, *args, **kwargs):
        self.connected_at = datetime.now()
        super().run(*args, **kwargs)

    async def get_context(self, message):
        # pylint: disable=arguments-differ
        return await super().get_context(message, cls=CustomContext)

    async def on_ready(self):
        # initialize games map
        self.logger.info('Ready to serve %s guilds!', len(self.guilds))

        setup_errors: int = 0
        try:
            self.setups = Setup.parse_setuplist(open("setups/setups.yaml"))
        except SetupLoadError as exc:
            self.logger.error(str(exc))
            setup_errors += 1

        self.logger.info('Successfully loaded %s setups',
                         len(self.setups) - setup_errors)

    async def on_message(self, message):
        if message.content.replace('!', '') == self.user.mention:
            return await message.channel.send('My prefix in this server is: `{}`'.format(global_prefix))
        return await super().on_message(message)

    async def on_guild_join(self, guild: discord.Guild):
        if self.__release__ != 'beta':
            return
        with self.db.conn.cursor() as cur:
            cur.execute(
                'SELECT EXISTS(select 1 FROM approved_guilds WHERE id=%s);', [guild.id])
            result = cur.fetchone()[0]
            if not result:
                await guild.leave()
                self.logger.info('Leaving unapproved guild %s', guild.name)

    async def on_guild_remove(self, guild: discord.Guild):
        # Go over each channel and try to remove it from games.
        for channel in guild.channels:
            self.games.pop(channel.id, None)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        self.games.pop(channel.id, None)

    async def on_member_remove(self, member: discord.Member):
        for game in self.games.values():
            if member in game.players:
                player = game.players.get(member)
                if len(game.players.replacements) == 0:
                    # Modkill user if no replacements.
                    async with game.channel.typing():
                        phase_str = 'd' if game.phase == Phase.DAY else 'n'
                        await game.channel.send(
                            f'{player.user.name} was modkilled for leaving the server.'
                            f' They were a *{player.display_role}*.'
                        )
                        await player.remove(game, f'modkilled {phase_str}{game.cycle}', modkill=True)
                        game_ended, winning_faction, independent_wins = game.check_endgame()
                        if game_ended:
                            await game.end(winning_faction, independent_wins)
                        return
                else:
                    # Replace user.
                    replacement = game.players.replacements.popleft()
                    player.user = replacement
                    await game.channel.send(
                        f'{member} left the server.'
                        f'\n{replacement} has replaced {member}.'
                    )
                    await player.send_pm(game)
                    return

    async def on_command_error(self, ctx, error):
        # pylint: disable=too-many-return-statements, arguments-differ, too-many-branches
        if hasattr(ctx.command, 'on_error'):
            return
        if isinstance(error, commands.CommandNotFound):
            args = remove_prefix(ctx.message.content,
                                 ctx.bot.global_prefix).split(' ')
            command = args[0]

            if ctx.guild is not None:  # DM only
                return
            games = [
                *filter(lambda g: ctx.author in g.players, self.games.values())]
            if len(games) == 0:
                return
            pl_game = games[0]
            if pl_game.phase != Phase.NIGHT:
                return
            player = pl_game.players[ctx.author]

            if not alive_or_recent_jester(player, pl_game) \
                    or not hasattr(player.role, 'action'):
                return
            valid_actions = [player.role.action, 'noaction'] if not isinstance(player.role.action, list) \
                else player.role.action + ['noaction']
            if command.lower() not in valid_actions:
                return
            await player.role.on_pm_command(ctx, pl_game, player, args)

            return  # ignore invalid commands

        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'Missing required argument {error.param}')
        elif isinstance(error, (commands.ArgumentParsingError, commands.BadArgument)):
            return await ctx.send('Invalid input')
        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(error)
        elif isinstance(error, commands.CommandOnCooldown):
            retry_after = round(error.retry_after)
            return await ctx.send('You are using this command too fast: try again in {} second{}'.format(retry_after, pluralize(retry_after)))

        elif isinstance(error, PhaseChangeError):
            # Inform users that game has ended and remove channel id from `self.games`.
            await ctx.send('There was an error incrementing the phase. The game has ended.')
            self.games.pop(ctx.channel.id, None)
            return self.logger.exception(error, exc_info=(type(error), error, error.__traceback__))

        await ctx.send(f'Uncaught exception: ```{error}```')

        if config.get('ENV', '') == "production":
            # End game only if `env` is set to 'production'.
            await ctx.send('\nThe game has ended.')
            self.games.pop(ctx.channel.id, None)

        self.logger.exception(error, exc_info=(
            type(error), error, error.__traceback__))

    def load_extensions(self):
        for file in pathlib.Path('godfather/cogs/').iterdir():
            if file.stem in ['__pycache__', '__init__']:
                continue
            try:
                if file.is_file():
                    # load files normally using load_ext()
                    self.load_extension('godfather.cogs.{}'.format(file.stem))
                elif file.is_dir():
                    # load from dir/__init__.py
                    self.load_extension(
                        'godfather.cogs.{}.__init__'.format(file.stem))
                self.logger.info('Loaded cog %s', file.stem)
            except commands.ExtensionError as err:
                self.logger.error('Error loading extension %s', file.stem)
                self.logger.exception(err, exc_info=True, stack_info=True)

    @property
    def global_prefix(self):
        return global_prefix


if __name__ == "__main__":
    # Run the bot
    Godfather().run(config.get('token'), reconnect=True)
