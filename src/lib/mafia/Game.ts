import { KlasaUser } from 'klasa';
import { Duration } from '@klasa/duration';

import Phase from '@mafia/Phase';
import PlayerManager from '@mafia/managers/PlayerManager';
import Godfather from '@lib/Godfather';
import Player from '@mafia/Player';
import VoteManager from '@mafia/managers/VoteManager';
import GodfatherChannel from '@lib/extensions/GodfatherChannel';
import NightActionsManager, { RoleEvent } from '@mafia/managers/NightActionsManager';
import Setup from './Setup';
import { GuildSettings } from '@lib/types/settings/GuildSettings';

export default class Game {

	public client: Godfather;
	public phase: Phase;
	public players: PlayerManager;
	public votes: VoteManager;
	public settings: GameSettings;
	public nightActions: NightActionsManager;
	public phaseEndAt?: Date = undefined;
	public cycle = 0;
	public setup?: Setup = undefined;
	public constructor(host: KlasaUser, public channel: GodfatherChannel) {
		this.client = channel.client as Godfather;
		this.phase = Phase.PREGAME;
		this.players = new PlayerManager(this);
		this.players.push(new Player(host, this));
		this.votes = new VoteManager(this);
		this.nightActions = new NightActionsManager(this);
		this.settings = {
			dayDuration: channel.guild.settings?.get(GuildSettings.DefaultDayDuration) as number,
			nightDuration: channel.guild.settings?.get(GuildSettings.DefaultNightDuration) as number
		};
	}

	public async startDay() {
		this.phase = Phase.STANDBY;
		const deadPlayers = await this.nightActions.resolve();
		if (deadPlayers.length > 0) {
			const deadText = [];
			for (const deadPlayer of deadPlayers) {
				const roleText = deadPlayer.cleaned
					? 'We could not determine their role.'
					: `They were a ${deadPlayer.role!.display}`;
				deadText.push(`${deadPlayer} died last night. ${roleText}`);
			}
			await this.channel.sendMessage(deadText.join('\n'));
		}
		// start voting phase
		this.nightActions.reset();
		this.phase = Phase.DAY;
		this.cycle++;
		const alivePlayers = this.players.filter(player => player.isAlive);
		// populate voting cache
		this.votes.reset();
		this.phaseEndAt = new Date();
		this.phaseEndAt.setSeconds(this.phaseEndAt.getSeconds() + (this.settings.dayDuration));

		await this.channel.sendMessage([
			`Day **${this.cycle}** will last ${Duration.toNow(this.phaseEndAt)}`,
			`With ${alivePlayers.length} alive, it takes ${this.majorityVotes} to lynch.`
		].join('\n'));
	}

	public async startNight() {
		this.phase = Phase.STANDBY;
		this.votes.reset();
		this.phaseEndAt = new Date();
		this.phaseEndAt.setSeconds(this.phaseEndAt.getSeconds() + this.settings.nightDuration);

		await this.channel.sendMessage(`Night **${this.cycle}** will last ${Duration.toNow(this.phaseEndAt)}. Send in your actions quickly!`);
		for (const player of this.players.filter(player => player.role!.canUseAction().check)) {
			await player.role!.onEvent(RoleEvent.NIGHT_START);
		}

		this.phase = Phase.NIGHT;
	}

	public get host() {
		return this.players[0];
	}

	public get hasStarted(): boolean {
		return this.phase !== Phase.PREGAME;
	}

	public get majorityVotes(): number {
		const alivePlayers = this.players.filter(player => player.isAlive);
		return Math.floor(alivePlayers.length / 2) + 1;
	}

	public delete(): void {
		this.client.games.delete(this.channel.id);
	}

}

export interface GameSettings {
	dayDuration: number;
	nightDuration: number;
}