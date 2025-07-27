import { action, DidReceiveSettingsEvent } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { SpotifySettings, ButtonStates } from '../types';

@action({ UUID: "fr.dbenech.spotify-plus.start-playlist" })
export class SpotifyStartPlaylistAction extends SpotifyBaseAction {
    private playlistUri: string = '';

    override onDidReceiveSettings(ev: DidReceiveSettingsEvent<SpotifySettings>): void {
        this.playlistUri = ev.payload.settings.playlistUri || '';
    }

    protected async handleAction(): Promise<void> {
        if (this.playlistUri) {
            await SpotifyBaseAction.sendAction('startplaylist', { playlistUri: this.playlistUri });
        }
    }

    protected updateImage(action: any, states: ButtonStates): void {
        action.setImage('imgs/action/playlist.png');
    }
}

