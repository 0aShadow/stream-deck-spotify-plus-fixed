import streamDeck, { LogLevel, DidReceiveGlobalSettingsEvent, DidReceiveSettingsEvent } from "@elgato/streamdeck";
import { SpotifyPlayerDial } from "./actions/spotify-player-dial";
import { SpotifyNextTrackAction } from "./actions/spotify-next-track";
import { SpotifyPreviousTrackAction } from "./actions/spotify-previous-track";
import { SpotifyPlayPauseAction } from "./actions/spotify-play-pause";
import { SpotifyToggleLikeAction } from "./actions/spotify-toggle-like";
import { SpotifyToggleShuffleAction } from "./actions/spotify-toggle-shuffle";
import { SpotifyVolumeUpAction, SpotifyVolumeDownAction, SpotifyVolumeMuteAction } from "./actions/spotify-volume-control";
import { SpotifyBaseAction } from "./actions/spotify-base-action";
import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
import fs from 'fs';
import { SpotifySettings } from './types';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

let pythonProcess: ReturnType<typeof spawn> | null = null;

// Handle global settings changes
let lastSettings: SpotifySettings | null = null;
streamDeck.settings.onDidReceiveSettings(async (ev: DidReceiveSettingsEvent<SpotifySettings>) => {
    const settings = ev.payload.settings;
    
    if (lastSettings && JSON.stringify(lastSettings.global) === JSON.stringify(settings.global)) {
        return;
    }

    lastSettings = settings;

    streamDeck.logger.info("Global settings changed: " + JSON.stringify(settings));
    streamDeck.settings.setGlobalSettings({
        ...settings.global,
    });

    // Restart global timer to apply new refresh rate
    SpotifyBaseAction.restartGlobalUpdate();

    startPythonBackend();
});

function getPlatformName(): string {
    switch (process.platform) {
        case 'win32':
            return 'win32';
        case 'darwin':
            return 'mac';
        case 'linux':
            return 'linux';
        default:
            return 'linux'; // Default to linux for unknown platforms
    }
}

function getExecutablePath(): string {
    const executableName = process.platform === 'win32' ? 'streamdeck-spotify-plus-plugin.exe' : 'streamdeck-spotify-plus-plugin';
    return path.join(__dirname, 'backend', executableName);
}

async function startPythonBackend() {
    streamDeck.logger.info("Starting Spotify backend");
    
    // Restart backend process with new settings
    if (pythonProcess) {
        pythonProcess.kill();
        pythonProcess = null;
    }

    const settings = await streamDeck.settings.getGlobalSettings();
    streamDeck.logger.info("Settings: " + JSON.stringify(settings));

    if (!settings.clientId || !settings.clientSecret) {
        streamDeck.logger.error("Client ID or Client Secret is missing");
        return;
    }

    // Write settings to .env file for backend
    const envContent = `
SPOTIFY_CLIENT_ID=${settings.clientId || ''}
SPOTIFY_CLIENT_SECRET=${settings.clientSecret || ''}
SPOTIFY_REDIRECT_URI=http://127.0.0.1:4202/callback
SPOTIFY_THIS_DEVICE=${settings.thisDevice || ''}
    `.trim();

    // Get the platform-specific executable path
    const executablePath = getExecutablePath();
    const platformName = getPlatformName();
    
    streamDeck.logger.info(`Platform detected: ${platformName}`);

    // Try executable first, fallback to Python if not available
    let backendDir: string;
    let command: string | null = null;
    let args: string[] = [];
    let useExecutable = false;

    if (fs.existsSync(executablePath)) {
        // Use executable
        streamDeck.logger.info("Using executable backend");
        backendDir = path.dirname(executablePath);
        command = executablePath;
        useExecutable = true;
        
        // Make sure executable has the right permissions on Unix systems
        if (process.platform !== 'win32') {
            try {
                fs.chmodSync(executablePath, 0o755);
            } catch (error) {
                streamDeck.logger.warn(`Could not set executable permissions: ${error}`);
            }
        }
    } else {
        // Fallback to Python
        streamDeck.logger.info("Executable not found, falling back to Python");
        backendDir = path.join(__dirname, 'backend');
        
        // Try different Python commands
        const pythonCommands = [
            process.platform === 'win32' 
                ? path.join(__dirname, 'backend/venv/Scripts/python.exe')
                : path.join(__dirname, 'backend/venv/bin/python'),
            'python3',
            'python'
        ];

        for (const pythonCmd of pythonCommands) {
            try {
                if (fs.existsSync(pythonCmd) || !path.isAbsolute(pythonCmd)) {
                    command = pythonCmd;
                    args = [path.join(__dirname, 'backend/backend.py')];
                    streamDeck.logger.info(`Using Python: ${pythonCmd}`);
                    break;
                }
            } catch (error) {
                continue;
            }
        }

        if (!command) {
            streamDeck.logger.error("Neither executable nor Python found. Please install Python or build the executable.");
            return;
        }
    }

    // Create backend directory if it doesn't exist
    if (!fs.existsSync(backendDir)) {
        fs.mkdirSync(backendDir, { recursive: true });
    }

    const envPath = path.join(backendDir, '.env');
    fs.writeFileSync(envPath, envContent);

    // Start the backend process
    try {
        pythonProcess = spawn(command!, args, {
            cwd: backendDir,
            env: {
                ...process.env,
                // Add any environment variables needed
            }
        });

        streamDeck.logger.info(`Backend process started with PID: ${pythonProcess.pid} (${useExecutable ? 'executable' : 'python'})`);

        // Handle process events
        pythonProcess.on('spawn', () => {
            streamDeck.logger.info("Backend process spawned successfully");
        });

        pythonProcess.on('error', (error) => {
            streamDeck.logger.error(`Failed to start backend process: ${error.message}`);
        });

        pythonProcess.on('close', (code, signal) => {
            streamDeck.logger.info(`Backend process closed with code ${code} and signal ${signal}`);
            pythonProcess = null;
        });

        // Optional: Log backend process output for debugging
        pythonProcess.stdout?.on('data', (data) => {
            streamDeck.logger.debug(`Backend output: ${data.toString().trim()}`);
        });

        pythonProcess.stderr?.on('data', (data) => {
            streamDeck.logger.error(`Backend error: ${data.toString().trim()}`);
        });

    } catch (error) {
        streamDeck.logger.error(`Error starting backend: ${error}`);
    }
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

// Start initial Python backend
startPythonBackend();

streamDeck.connect();