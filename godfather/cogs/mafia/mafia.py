import asyncio
import inspect
from datetime import datetime
import typing
from collections import defaultdict
from functools import reduce
import math

import discord
from discord.ext import commands

from Levenshtein import jaro_winkler

from godfather.cogs.mafia.checks import *  # pylint: disable=wildcard-import, unused-wildcard-import
from godfather.errors import PhaseChangeError
from godfather.game import Game, Phase, Player
from godfather.game.vote_manager import VoteError
from godfather.game.setup import Setup, SetupLoadError
from godfather.roles import all_roles, role_categories
from godfather.utils import (CustomContext, confirm, from_now,
                             emotes)


class Mafia(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=['create', 'create-game'])
    @commands.guild_only()
    async def creategame(self, ctx: CustomContext):
        """
        Creates a game of mafia in this channel.
        To join an existing game, use the `join` command.
        Hosts may delete running games using the `delete` command.
        """
        if ctx.channel.id in self.bot.games:
            return await ctx.send('A game of mafia is already running '
                                  'in this channel.')

        # prevent the user from joining if they are already in a different game
        other_games = list(filter(
            lambda game: ctx.author in game.players, self.bot.games.values()))
        if len(other_games) > 0:
            other_game = other_games[0]
            return await ctx.send(
                'You are already playing another game in the channel {} ({})'.format(
                    other_game.channel.mention, other_game.channel.guild.name)
            )

        new_game = Game.create(ctx, self.bot)
        self.bot.games[ctx.channel.id] = new_game
        return await ctx.send('Started a game of mafia in '
                              f'{ctx.message.channel.mention}, '
                              f'hosted by **{ctx.message.author}**')

    @commands.command(aliases=['in'])
    @game_only()
    async def join(self, ctx: CustomContext):
        """
        Adds you to the playerlist of an ongoing game.
        """
        game = self.bot.games[ctx.channel.id]

        if ctx.author in game.players:
            return await ctx.send('You have already joined this game.')

        # prevent the user from joining if they are already in a different game
        other_games = list(filter(
            lambda game: ctx.author in game.players, self.bot.games.values()))
        if len(other_games) > 0:
            other_game = other_games[0]
            return await ctx.send(
                'You are already playing another game in the channel {} ({}).'.format(
                    other_game.channel.mention, other_game.channel.guild.name)
            )

        # If game has already started, can only be replacement
        if game.has_started:
            if ctx.author in game.players.replacements:
                return await ctx.send('You are already a replacement.')

            to_replace = await confirm(
                ctx.bot, ctx.author, ctx.channel,
                'Sign-ups for this game have ended. '
                'Would you like to be a replacement?'
            )
            if to_replace is None:  # If timeout
                return
            if not to_replace:
                return await ctx.message.add_reaction('❌')

            game.players.add(ctx.author, replacement=True)
            return await ctx.send('You have decided to become a replacement.')

        max_players = game.config['max_players']
        if max_players and len(game.players) >= max_players:
            return await ctx.send('This game can accept a maximum of {} players.'.format(max_players))

        game.players.add(ctx.author)
        return await ctx.send(' Game joined successfully.')

    @commands.command(aliases=['out'])
    @game_only()
    async def leave(self, ctx: CustomContext):
        """
        Leave an ongoing game.
        Leaving a game that has started and has no replacements will result in a mod-kill.
        """
        game = self.bot.games[ctx.channel.id]

        if ctx.author in game.players.replacements:
            game.players.replacements.remove(ctx.author)
            return await ctx.send("You're not a replacement anymore.")
        elif ctx.author not in game.players:
            return await ctx.send('You have not joined this game')
        elif ctx.author.id == game.host.id:
            return await ctx.send('The host cannot leave the game.')

        if game.has_started:
            player = game.players.get(ctx.author)
            if not player.is_alive:
                return await ctx.send('Dead players cannot leave the game.')
            replace_text = ('Are you sure you want to leave the game?'
                            'You will be mod-killed.') \
                if len(game.players.replacements) == 0 \
                else ('Are you sure you want to leave the game? '
                      'You will be replaced out.')
            confirm_replacement = await confirm(
                ctx.bot, ctx.author, ctx.channel, replace_text
            )
            if confirm_replacement is None:
                return
            if not confirm_replacement:
                return await ctx.message.add_reaction('❌')

            player = game.players[ctx.author]

            if len(game.players.replacements) == 0:
                phase_str = 'd' if game.phase == Phase.DAY else 'n'
                async with game.channel.typing():
                    await game.channel.send(f'{player.user.name} was modkilled. They were a *{player.display_role}*.')
                    await player.remove(game, f'modkilled {phase_str}{game.cycle}')
                    game_ended, winning_faction, independent_wins = game.check_endgame()
                    if game_ended:
                        await game.end(winning_faction, independent_wins)
                    return

            else:
                replacement = game.players.replacements.popleft()
                game.replace(player, replacement)
                await ctx.send(f'{replacement} has replaced {ctx.author}.')
                await player.send_pm(game)
                return

        else:
            game.players.remove(ctx.author)
            if ctx.author.id in game.votes:
                del game.votes[ctx.author.id]
            return await ctx.send('✅ Game left successfully')

    @commands.command()
    @game_only()
    @commands.cooldown(1, 5.0, commands.BucketType.channel)
    async def playerlist(self, ctx: CustomContext):
        """
        Shows everyone who has signed up for the current game.
        """
        game = self.bot.games[ctx.channel.id]
        msg = f'**Players: {len(game.players)}**\n'
        msg += game.players.show(show_replacements=True)

        return await ctx.send(msg)

    @commands.command()
    @game_started_only()
    @game_only()
    @commands.cooldown(1, 5.0, commands.BucketType.channel)
    async def remaining(self, ctx):
        """
        Shows when the current day/night ends.
        """
        game = self.bot.games[ctx.channel.id]
        phase_str = 'Day' if game.phase == Phase.DAY else 'Night'
        await ctx.send(f'🕰️ {phase_str} {game.cycle} ends {self._remaining(game)}')

    # the status and remaining command both need this
    def _remaining(self, game, show_in=True):
        if datetime.now() > game.phase_end_at:
            return 'any second now...'
        return from_now(game.phase_end_at, show_in)

    @commands.command()
    @commands.cooldown(1, 5.0, commands.BucketType.channel)
    async def setupinfo(self, ctx: CustomContext, setup_name: typing.Optional[str] = None):
        # show the current setup if a game is ongoing
        found_setup = None
        if (ctx.game and ctx.game.setup and setup_name is None):
            found_setup = ctx.game.setup

        if (found_setup is None and setup_name is None) or setup_name == 'all':
            txt = ('**All available setups:** (to view a specific setup, use '
                   f'{self.bot.global_prefix}setupinfo <name>)')
            txt += '```\n'
            for setup in self.bot.setups.values():
                txt += f'{setup.name} ({len(setup.roles)} players)\n'
            txt += '```'
            return await ctx.send(txt)

        if found_setup is None:
            found_setup = self.bot.setups.get(setup_name)

        if not found_setup:
            return await ctx.send(
                f"Couldn't find {setup_name}, use {self.bot.global_prefix}setupinfo to view all setups."
            )

        txt = [
            f'**{found_setup.name}** ({found_setup.total_players} players)', '```\n']
        for i, role in enumerate(found_setup.roles):
            txt.append(f'{i+1}. {role.title()}')
        txt.append('```')

        await ctx.send('\n'.join(txt))

    @ commands.command()
    @commands.cooldown(1, 5.0, commands.BucketType.channel)
    async def roleinfo(self, ctx: CustomContext, *, rolename: typing.Optional[str] = None):
        """
        Shows information about the given role.
        If used without any arguments, shows you a list of all roles supported in the bot.
        """
        if rolename is None:
            def accumulator(facroles, role):
                facroles[role().faction.category_name].append(role.name)
                return facroles
            fac_roles = reduce(
                accumulator, all_roles.values(), defaultdict(list))
            embed = discord.Embed()
            embed.color = 0x000000
            embed.set_author(name='All supported roles',
                             icon_url=self.bot.user.avatar_url)
            embed.set_footer(
                text='For information on a specific role, use roleinfo command.')
            embed.description = ''
            for faction, roles in fac_roles.items():
                roles.sort()
                for role in roles:
                    emote_name = role if faction == 'Neutral' else faction
                    emote = emotes.get(emote_name, '❓')
                    embed.description += '{} **{}**\n'.format(emote, role)
                embed.description += '\n'
            return await ctx.send(embed=embed)

        if rolename in role_categories:
            return await ctx.invoke(self.bot.get_command('categoryinfo'), category=rolename)

        for role in all_roles.values():
            role = role()  # initialize the class
            if role.name.lower() == rolename.lower():
                if role.__doc__ is None:
                    return await ctx.send('No documentation on {} available.'.format(rolename))

                annotations = []
                annotations.append(role.faction.category_name)
                if role.unique:
                    annotations.append('Unique')

                embed = discord.Embed()
                embed.color = 0x000000
                embed.set_author(name=f'{role.name} ({"; ".join(annotations)})',
                                 icon_url=self.bot.user.avatar_url)
                embed.description = '```diff\n'
                embed.description += inspect.getdoc(role)
                embed.description += '```'
                embed.set_footer(
                    text=f'Categories: {", ".join(sorted(role.categories))}')

                return await ctx.send(embed=embed)

        for role in all_roles.values():
            if jaro_winkler(role.name.lower(), rolename.lower()) > 0.85:
                await ctx.send('Couldn\'t find the role "{}". Did you mean {}?'.format(rolename, role.name))

                def check(msg):
                    return msg.author == ctx.author and msg.content.lower() in ['yes', 'y', 'yeah']
                try:
                    response = await self.bot.wait_for('message', timeout=10.0, check=check)
                    return await ctx.invoke(ctx.command, rolename=role.name)
                except asyncio.TimeoutError:
                    return
        await ctx.send("Couldn't find that role!")

    @ commands.command()
    @ game_only()
    @ game_started_only()
    async def rolepm(self, ctx: CustomContext):
        """
        Sends you your role PM.
        """
        player = ctx.game.players[ctx.author]
        try:
            await player.send_pm(ctx.game)
            can_do, _ = player.role.can_do_action(ctx.game)
            if ctx.game.phase == Phase.NIGHT and can_do:
                await player.role.on_night(ctx.bot, player, ctx.game)
            await ctx.message.add_reaction('✅')
        except discord.Forbidden:
            await ctx.send('Cannot send you your role PM. Make sure your DMs are enabled!')

    @ commands.command(aliases=['start'])
    @ host_only()
    @ game_only()
    async def startgame(self, ctx: CustomContext,
                        r_setup: typing.Optional[str] = None):
        """
        Starts the game.
        To view a list of available setups, use the `setupinfo` command.
        """
        game = ctx.game

        if game.has_started:
            await ctx.send("Game has already started!")
            return

        if game.setup and len(game.players) != game.setup.total_players:
            return await ctx.send('Custom setup used needs {} players.'.format(game.setup.total_players))

        if game.setup is None:
            try:
                game.phase = Phase.STANDBY
                found_setup = await game.find_setup(r_setup)
            except ValueError as err:  # pylint: disable=broad-except
                return await ctx.send(err)
            finally:
                game.phase = Phase.PREGAME
            game.setup = found_setup

        # set to standby so people can't join while the bot is sending rolepms
        game.phase = Phase.STANDBY
        await ctx.send(f'Chose the setup **{game.setup.name}**. '
                       'Randing roles...')

        no_dms = await game.setup.assign_roles(game)
        await ctx.send('Sent all role PMs!')

        if len(no_dms) > 0:
            no_dms = [*map(lambda usr: usr.name, no_dms)]
            await ctx.send(f"I couldn't DM {', '.join(no_dms)}."
                           f" Use the {self.bot.global_prefix}rolepm command to receive your PM.")

        # flags
        flags = {flag_name: game.setup.flags[flag_name]
                 for flag_name in Setup.all_flags}

        if "night_start" in flags and flags['night_start']:
            game.cycle = 1
            game.phase = Phase.DAY
        try:
            await game.increment_phase()
        except Exception as exc:
            raise PhaseChangeError(None, *exc.args)

    @commands.command(aliases=['vtl'])
    @day_only()
    @game_started_only()
    @player_only()
    @game_only()
    async def vote(self, ctx: CustomContext, *, target: Player):
        """
        Vote to lynch a player.
        """
        game: Game = ctx.game
        try:
            hammered = game.votes.vote(game.players[ctx.author], target)
        except VoteError as err:
            return await ctx.send(*err.args)

        await ctx.send(f'Voted {target.user.name}')

        if hammered and not game.phase == Phase.STANDBY:
            game.phase = Phase.STANDBY

            await game.lynch(target)
            game_ended, winning_faction, independent_wins = game.check_endgame()
            if game_ended:
                await game.end(winning_faction, independent_wins)
            else:
                game.phase = Phase.DAY
                await game.increment_phase()

    @commands.command(aliases=['vtnl', 'nl'])
    @day_only()
    @game_started_only()
    @player_only()
    @game_only()
    async def nolynch(self, ctx: CustomContext):
        """
        Vote to end day without a lynch.
        """
        game = self.bot.games[ctx.channel.id]
        try:
            nolynch = game.votes.no_lynch(game.players[ctx.author])
        except VoteError as err:
            return await ctx.send(*err.args)
        await ctx.send('You have voted to no-lynch.')

        if nolynch and not game.phase == Phase.STANDBY:
            game.phase = Phase.STANDBY
            game.day_with_no_lynch = True
            await ctx.send('Nobody was lynched!')
            game.phase = Phase.DAY
            await game.increment_phase()

    @commands.command()
    @day_only()
    @game_started_only()
    @player_only()
    @game_only()
    async def unvote(self, ctx: CustomContext):
        """
        Remove your vote from a player/nolynch.
        """
        unvoted = ctx.game.votes.unvote(ctx.game.players[ctx.author])
        if unvoted:
            return await ctx.message.add_reaction('✅')

        await ctx.send('No votes to remove.')

    @commands.command(aliases=['vc', 'votes'])
    @day_only()
    @game_started_only()
    @player_only()
    @game_only()
    @commands.cooldown(1, 5.0, commands.BucketType.channel)
    async def votecount(self, ctx: CustomContext):
        """
        Shows the current vote count.
        """
        msg = ctx.game.votes.show()
        return await ctx.send(msg)

    @commands.command(aliases=['delete'])
    @host_only()
    @game_only()
    async def deletegame(self, ctx: CustomContext):
        """
        Lets a host delete an ongoing game. (provided it has not started yet)
        """
        if ctx.game.has_started:
            confirmation = await confirm(self.bot, ctx.author, ctx.channel,
                                         'Are you sure you want to delete an ongoing game?')
            if not confirmation:
                return
        del self.bot.games[ctx.channel.id]
        return await ctx.message.add_reaction('✅')

    @commands.command()
    @host_only()
    @game_only()
    async def usesetup(self, ctx: CustomContext, *, setup_data: str):
        """
        Lets you use a custom setup for your game.

        The simplest form is a list of roles separated by commas, for example `usesetup Vigilante, Goon, Vanilla x5`

        To add your own name and start games at night, you may need to use [YAML](https://yaml.org/). The format for that is:

        ```yaml
        roles: [Vigilante, Goon, Vanilla x5]
        night_start: true
        name: your_setup_name
        ```
        """
        setup_data = setup_data.strip('```yaml\n')
        try:
            ctx.game.setup = Setup(setup_data)
        except SetupLoadError as err:
            return await ctx.send(err)

        return await ctx.send('Using the setup **{}** with {} players.'.format(
            ctx.game.setup.name, len(ctx.game.setup.roles)
        ))

    @commands.command()
    @game_only()
    async def status(self, ctx: CustomContext):
        """
        Shows useful information about the game.
        """
        if (ctx.game.phase == Phase.PREGAME):
            return await ctx.send(
                'The game in {} hasn\'t started yet. Use {}in to join it!'.format(
                    ctx.channel.mention, ctx.prefix)
            )
        elif (ctx.game.phase == Phase.STANDBY):
            return await ctx.send('The bot is currently processing the game. Sit tight! 👀')
        embed = discord.Embed()
        phase_str = 'Day {} 🌅' if ctx.game.phase == Phase.DAY else 'Night {} 🌃'

        embed.add_field(name='Host', value=ctx.game.host)
        embed.add_field(name='Phase', value=phase_str.format(ctx.game.cycle))
        embed.add_field(name='Time remaining',
                        value=self._remaining(ctx.game, show_in=False))
        embed.add_field(name='Players', value='```diff\n{}```'.format(
            ctx.game.players.show(codeblock=True)))
        return await ctx.send(embed=embed)

    @commands.command(name='changehost')
    @game_only()
    @player_only()
    async def change_host(self, ctx: CustomContext):
        """
        Votes to change the host. With a majority vote, the next player on the playerlist becomes the new host.
        If the host uses this command, they are changed immediately.
        """
        to_change = False
        game = ctx.game
        if game.has_started:
            return await ctx.send('You cannot change the host after the game has started.')
        if len(game.players) < 3:
            return await ctx.send('You need at least 3 players to change the host.')

        if ctx.author == game.host:
            to_change = True
        else:
            if ctx.author.id in game.players.vote_kicks:
                return await ctx.send('You have already voted to change the host.')
            game.players.vote_kicks.add(ctx.author.id)
            await ctx.message.add_reaction('✅')
            majority_votes = math.floor(len(game.players) / 2) + 1
            if len(game.players.vote_kicks) >= majority_votes:
                to_change = True

        if to_change:
            # host is always player #1, so preserve that order adding the new host to #1 and moving the old host to the bottom
            # why doesn't playermanage extend list yet??
            old_host = game.players.players.pop(0)
            game.players.players.insert(len(game.players), old_host)
            game.players.vote_kicks.clear()
            return await ctx.send('The host is now {}'.format(game.host))

    @commands.command(aliases=['category'])
    async def categoryinfo(self, ctx: CustomContext, *, category: str):
        """
        Shows the list of roles in a specific category
        """
        if category not in role_categories:
            return await ctx.send('Category "{}" not found'.format(category))
        category_roles = role_categories.get(category)
        out = 'Roles in {}: {}'.format(category, ', '.join(
            map(lambda role: role.name, category_roles)))
        return await ctx.send(out)
