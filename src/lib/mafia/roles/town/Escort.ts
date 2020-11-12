import ActionRole from '@mafia/mixins/ActionRole';
import Townie from '@mafia/mixins/Townie';
import NightActionsManager, { NightActionPriority } from '@mafia/managers/NightActionsManager';
import Player from '@mafia/Player';

class Escort extends ActionRole {

	public name = 'Escort';
	public description = 'You may roleblock somebody each night.';
	public action = 'block';
	public actionText = 'block a player';
	public actionGerund = 'blocking';
	public priority = NightActionPriority.ESCORT;

	public setUp(actions: NightActionsManager, target: Player) {
		for (const action of actions.filter(act => act.actor === target)) {
			if (!action.flags?.canBlock) continue;
			// escorts blocking SKs get stabbed instead
			if (action.actor.role.name === 'Serial Killer') {
				action.target = this.player;
				continue;
			}
			actions.splice(actions.indexOf(action), 1);
			actions.record.setAction(target.user.id, 'roleblock', { result: true, by: [this.player] });
		}
	}

	public tearDown(actions: NightActionsManager, target: Player) {
		const success = actions.record.get('roleblock').get(target.user.id).by.find(player => player.user.id === this.player.user.id);
		if (success) return target.user.send('Somebody occupied your night. You were roleblocked!');
	}

}

export default Townie(Escort);