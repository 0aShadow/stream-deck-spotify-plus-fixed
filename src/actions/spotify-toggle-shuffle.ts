import { action } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { ButtonStates } from "../types";

@action({ UUID: "fr.dbenech.spotify-plus.toggle-shuffle" })
export class SpotifyToggleShuffleAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await SpotifyBaseAction.sendAction('toggleshuffle');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        action.setImage(states.is_shuffle ? 'imgs/action/shuffle_on.png' : 'imgs/action/shuffle.png');
    }
} 