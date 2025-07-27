import { action, DidReceiveSettingsEvent } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { SpotifySettings, ButtonStates } from '../types';

@action({ UUID: "fr.dbenech.spotify-plus.volume-up" })
export class SpotifyVolumeUpAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await SpotifyBaseAction.sendAction('volumeup');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        // Volume up n'a pas besoin de changer d'image
    }
}

@action({ UUID: "fr.dbenech.spotify-plus.volume-down" })
export class SpotifyVolumeDownAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await SpotifyBaseAction.sendAction('volumedown');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        // Volume down n'a pas besoin de changer d'image
    }
}

@action({ UUID: "fr.dbenech.spotify-plus.volume-mute" })
export class SpotifyVolumeMuteAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await SpotifyBaseAction.sendAction('volumemute');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        action.setImage(states.is_muted ? 'imgs/action/mute.png' : 'imgs/action/volume.png');
    }
}

@action({ UUID: "fr.dbenech.spotify-plus.volume-set" })
export class SpotifyVolumeSetAction extends SpotifyBaseAction {
    private volume: number = 50;

    override onDidReceiveSettings(ev: DidReceiveSettingsEvent<SpotifySettings>): void {
        this.volume = ev.payload.settings.volume || 50;
    }

    protected async handleAction(): Promise<void> {
        await SpotifyBaseAction.sendAction('volumeset', { value: this.volume });
    }

    protected updateImage(action: any, states: ButtonStates): void {
        action.setImage('imgs/action/volume.png');
    }
} 