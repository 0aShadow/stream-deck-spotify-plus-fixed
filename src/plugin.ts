import streamDeck, { LogLevel, DidReceiveGlobalSettingsEvent, DidReceiveSettingsEvent } from "@elgato/streamdeck";
import { SpotifyPlayerDial } from "./actions/spotify-player-dial";
import { SpotifyNextTrackAction } from "./actions/spotify-next-track";
import { SpotifyPreviousTrackAction } from "./actions/spotify-previous-track";
import { SpotifyPlayPauseAction } from "./actions/spotify-play-pause";
import { SpotifyToggleLikeAction } from "./actions/spotify-toggle-like";
import { SpotifyToggleShuffleAction } from "./actions/spotify-toggle-shuffle";
import { SpotifyVolumeUpAction, SpotifyVolumeDownAction, SpotifyVolumeMuteAction, SpotifyVolumeSetAction } from "./actions/spotify-volume-control";
import { SpotifyStartPlaylistAction } from "./actions/spotify-start-playlist";
import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
import fs from 'fs';
import { SpotifySettings } from './types';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

let pythonProcess: ReturnType<typeof spawn> | null = null;

// // Handle global settings changes
streamDeck.settings.onDidReceiveSettings(async (ev: DidReceiveSettingsEvent<SpotifySettings>) => {
    const settings = ev.payload.settings;
    streamDeck.logger.info("Global settings changed: " + JSON.stringify(settings));
    streamDeck.settings.setGlobalSettings({
        ...settings.global,
    });

    startPythonBackend();
});

async function startPythonBackend() {

    // Restart Python process with new settings
    if (pythonProcess) {
        pythonProcess.kill();
    }

    const settings = await streamDeck.settings.getGlobalSettings();
    streamDeck.logger.info("Settings: " + JSON.stringify(settings));

    if (!settings.clientId || !settings.clientSecret) {
        streamDeck.logger.error("Client ID or Client Secret is missing");
        return;
    }

    // Write settings to .env file for Python backend
    const envContent = `
    SPOTIFY_CLIENT_ID=${settings.clientId || ''}
    SPOTIFY_CLIENT_SECRET=${settings.clientSecret || ''}
    SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
    `.trim();

    const envPath = path.join(__dirname, 'backend/.env');
    fs.writeFileSync(envPath, envContent);

    // Use Python from virtual environment
    const pythonPath = process.platform === 'win32'
        ? path.join(__dirname, 'backend/venv/Scripts/python.exe')
        : path.join(__dirname, 'backend/venv/bin/python');

    // Start the Python backend with venv Python
    pythonProcess = spawn(pythonPath, [path.join(__dirname, 'backend/backend.py')]);

    // Optional: Log Python process output and errors
    pythonProcess?.stdout?.on('data', (data) => {
        streamDeck.logger.info(`Python output: ${data}`);
    });

    pythonProcess?.stderr?.on('data', (data) => {
        streamDeck.logger.error(`Python error: ${data}`);
    });
}

streamDeck.logger.setLevel(LogLevel.INFO);
streamDeck.actions.registerAction(new SpotifyPlayerDial());
streamDeck.actions.registerAction(new SpotifyNextTrackAction());
streamDeck.actions.registerAction(new SpotifyPreviousTrackAction());
streamDeck.actions.registerAction(new SpotifyPlayPauseAction());
streamDeck.actions.registerAction(new SpotifyToggleLikeAction());
streamDeck.actions.registerAction(new SpotifyToggleShuffleAction());
streamDeck.actions.registerAction(new SpotifyVolumeUpAction());
streamDeck.actions.registerAction(new SpotifyVolumeDownAction());
streamDeck.actions.registerAction(new SpotifyVolumeMuteAction());
streamDeck.actions.registerAction(new SpotifyVolumeSetAction());
streamDeck.actions.registerAction(new SpotifyStartPlaylistAction());

// Start initial Python backend
// startPythonBackend();

streamDeck.connect();