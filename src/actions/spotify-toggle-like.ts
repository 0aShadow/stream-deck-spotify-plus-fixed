import streamDeck, { action } from "@elgato/streamdeck";
import { SpotifyBaseAction } from "./spotify-base-action";
import { ButtonStates } from "../types";

@action({ UUID: "fr.dbenech.spotify-plus.toggle-like" })
export class SpotifyToggleLikeAction extends SpotifyBaseAction {
    protected async handleAction(): Promise<void> {
        await this.sendAction('togglelike');
    }

    protected updateImage(action: any, states: ButtonStates): void {
        const is_liked = states.is_liked;
        action.setImage(is_liked ? 'imgs/action/like_filled.png' : 'imgs/action/like.png');
    }
} 