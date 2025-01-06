import { action } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { ButtonStates } from "../types";

@action({ UUID: "fr.dbenech.spotify-plus.previous-track" })
export class SpotifyPreviousTrackAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await this.sendAction('previous');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        action.setImage('imgs/action/previous.png');
    }
} 