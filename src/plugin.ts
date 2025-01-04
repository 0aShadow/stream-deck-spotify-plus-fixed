import streamDeck, { LogLevel, DidReceiveGlobalSettingsEvent, DidReceiveSettingsEvent } from "@elgato/streamdeck";
import { SpotifyPlayerDial } from "./actions/spotify-player-dial";
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

    //     // Write settings to .env file for Python backend
    //     const envContent = `
    // SPOTIFY_CLIENT_ID=${settings.clientId || ''}
    // SPOTIFY_CLIENT_SECRET=${settings.clientSecret || ''}
    // SPOTIFY_REDIRECT_URI=http://localhost:8491/callbacks
    // `.trim();

    //     const envPath = path.join(__dirname, 'backend/.env');
    //     fs.writeFileSync(envPath, envContent);

    //     // Restart Python process with new settings
    //     if (pythonProcess) {
    //         pythonProcess.kill();
    //     }

    //     startPythonBackend();
});

function startPythonBackend() {
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

// Start initial Python backend
startPythonBackend();

streamDeck.connect();