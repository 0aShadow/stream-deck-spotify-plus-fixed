import streamDeck, { LogLevel } from "@elgato/streamdeck";
import { SpotifyPlayerDial } from "./actions/spotify-player-dial";
import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Use Python from virtual environment
const pythonPath = process.platform === 'win32'
    ? path.join(__dirname, 'backend/venv/Scripts/python.exe')
    : path.join(__dirname, 'backend/venv/bin/python');

// Start the Python backend with venv Python
const pythonProcess = spawn(pythonPath, [path.join(__dirname, 'backend/backend.py')]);

// Optional: Log Python process output and errors
pythonProcess.stdout.on('data', (data) => {
    streamDeck.logger.info(`Python output: ${data}`);
});

pythonProcess.stderr.on('data', (data) => {
    streamDeck.logger.error(`Python error: ${data}`);
});

streamDeck.logger.setLevel(LogLevel.INFO);
streamDeck.actions.registerAction(new SpotifyPlayerDial());
streamDeck.connect();