import streamDeck, { action } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { ButtonStates } from "../types";

@action({ UUID: "fr.dbenech.spotify-plus.play-pause" })
export class SpotifyPlayPauseAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await this.sendAction('playpause');
        streamDeck.logger.info("Play/Pause action sent");
    }

    protected updateImage(action: any, states: ButtonStates): void {
        action.setImage(states.is_playing ? 'imgs/action/pause.png' : 'imgs/action/play.png');
    }
} 