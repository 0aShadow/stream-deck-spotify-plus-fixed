import streamDeck, { LogLevel } from "@elgato/streamdeck";
import { SpotifyPlayerDial } from "./actions/spotify-player-dial";

streamDeck.logger.setLevel(LogLevel.INFO);
streamDeck.actions.registerAction(new SpotifyPlayerDial());
streamDeck.connect();