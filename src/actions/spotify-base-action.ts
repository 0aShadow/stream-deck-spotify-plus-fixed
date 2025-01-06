import streamDeck, { action, SingletonAction, KeyDownEvent, KeyUpEvent, DidReceiveSettingsEvent, WillAppearEvent, WillDisappearEvent } from "@elgato/streamdeck";
import https from 'https';
import http from 'http';
import { SpotifySettings, ButtonStates } from '../types';

export abstract class SpotifyBaseAction extends SingletonAction<SpotifySettings> {
    private static instances: SpotifyBaseAction[] = [];
    private static updateInterval: NodeJS.Timeout | null = null;
    private static readonly STATES_URL = 'http://localhost:8491/states';
    private action: any;

    constructor() {
        super();
    }

    protected async sendAction(actionType: string, url: string = 'http://localhost:8491/player', value?: number, additionalData?: any): Promise<void> {
        const protocol = url.startsWith('https:') ? https : http;

        const postData = JSON.stringify({
            action: actionType,
            value: value,
            timestamp: new Date().toISOString(),
            ...additionalData
        });

        const options = {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(postData),
                'User-Agent': 'StreamDeck-Plugin'
            }
        };

        return new Promise((resolve, reject) => {
            const request = protocol.request(url, options, (response) => {
                let data = '';
                response.on('data', (chunk) => {
                    data += chunk;
                });

                response.on('end', () => {
                    streamDeck.logger.info(`POST response for ${actionType}: ${data}`);
                    resolve();
                });
            });

            request.on('error', (error) => {
                streamDeck.logger.error(`POST request failed for ${actionType}: ${error.message}`);
                reject(error);
            });

            request.write(postData);
            request.end();
        });
    }

    override onKeyDown(ev: KeyDownEvent<SpotifySettings>): void {
        streamDeck.logger.info("Key down triggered");
        this.handleAction()
            .catch(error => streamDeck.logger.error(`Error in onKeyDown: ${error}`));
    }

    override onWillAppear(ev: WillAppearEvent<SpotifySettings>): void {
        this.action = ev.action;

        // Vérification si l'instance existe déjà
        const existingInstance = SpotifyBaseAction.instances.find(instance => instance === this);
        if (!existingInstance) {
            SpotifyBaseAction.instances.push(this);
            streamDeck.logger.info(`Added instance. Total instances: ${SpotifyBaseAction.instances.length}`);

            // Ne démarrer la mise à jour que si c'est la première instance
            if (SpotifyBaseAction.instances.length === 1) {
                SpotifyBaseAction.startGlobalUpdate();
            }
        }
    }

    override onWillDisappear(ev: WillDisappearEvent<SpotifySettings>): void {
        // Remove instance from the static list
        const index = SpotifyBaseAction.instances.indexOf(this);
        if (index > -1) {
            SpotifyBaseAction.instances.splice(index, 1);
        }

        // If no instances left, stop global update
        if (SpotifyBaseAction.instances.length === 0) {
            SpotifyBaseAction.stopGlobalUpdate();
        }
    }

    protected abstract handleAction(): Promise<void>;
    protected abstract updateImage(action: any, states: ButtonStates): void;

    protected static async updateAllButtonStates(): Promise<void> {
        try {
            const response = await fetch(SpotifyBaseAction.STATES_URL);
            if (!response.ok) throw new Error('Failed to fetch button states');

            const res = await response.json();
            const states = res.states as ButtonStates;

            // Update all instances
            SpotifyBaseAction.instances.forEach(instance => {
                if (instance.action) {
                    instance.updateImage(instance.action, states);
                }
            });
        } catch (error) {
            streamDeck.logger.error(`Error updating button states: ${error}`);
        }
    }

    private static async startGlobalUpdate(): Promise<void> {
        if (!SpotifyBaseAction.updateInterval) {
            // Initial update
            await SpotifyBaseAction.updateAllButtonStates();

            const settings = await streamDeck.settings.getGlobalSettings();
            const refreshRate = Number(settings.refreshRate) * 1000 || 5000;
            streamDeck.logger.info(`Starting global update with refresh rate: ${refreshRate}`);
            SpotifyBaseAction.updateInterval = setInterval(
                () => SpotifyBaseAction.updateAllButtonStates(),
                refreshRate
            );
        }
    }

    private static stopGlobalUpdate(): void {
        if (SpotifyBaseAction.updateInterval) {
            clearInterval(SpotifyBaseAction.updateInterval);
            SpotifyBaseAction.updateInterval = null;
        }
    }
} 