import { KlasaMessage, KlasaUser } from 'klasa';
import { ApplyOptions } from '@skyra/decorators';
import { ChannelType } from '@klasa/dapi-types';
import Game from '@mafia/Game';
import { Message } from '@klasa/core';
import GodfatherCommand, { GodfatherCommandOptions } from '@lib/GodfatherCommand';
import GodfatherChannel from '@lib/extensions/GodfatherChannel';

@ApplyOptions<GodfatherCommandOptions>({
	aliases: ['creategame'],
	cooldown: 5,
	extendedHelp: [
		'To join an existing game, use the `join` command.',
		'Hosts may delete running games using the `delete` command.'
	].join('\n'),
	description: 'Creates a game of mafia in the current channel.',
	runIn: [ChannelType.GuildText]
})
export default class extends GodfatherCommand {

	public async run(msg: KlasaMessage): Promise<Message[]> {
		if (this.client.games.has(msg.channel.id)) {
			throw 'A game of Mafia is already running in this channel.';
		}
		const game = new Game(msg.author as KlasaUser, msg.channel as GodfatherChannel);
		this.client.games.set(msg.channel.id, game);
		return msg.sendMessage(`Started a game of Mafia in ${msg.channel} hosted by **${msg.author.tag}**.`);
	}

}