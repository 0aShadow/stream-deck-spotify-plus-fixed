import { action } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { ButtonStates } from "../types";

@action({ UUID: "fr.dbenech.spotify-plus.next-track" })
export class SpotifyNextTrackAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await SpotifyBaseAction.sendAction('next');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        // Next track button doesn't need to change its image
        action.setImage('imgs/action/next.png');
    }
} 