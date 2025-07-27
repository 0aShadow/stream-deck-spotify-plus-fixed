import streamDeck, { action, SingletonAction, KeyDownEvent, KeyUpEvent, DidReceiveSettingsEvent, WillAppearEvent, WillDisappearEvent } from "@elgato/streamdeck";
import https from 'https';
import http from 'http';
import { SpotifySettings, ButtonStates } from '../types';
import { SpotifyPlayerDial } from "./spotify-player-dial";

export abstract class SpotifyBaseAction extends SingletonAction<SpotifySettings> {
    private static instances: SpotifyBaseAction[] = [];
    private static updateInterval: NodeJS.Timeout | null = null;
    private static readonly STATES_URL = 'http://127.0.0.1:8491/states';
    private action: any;

    constructor() {
        super();
    }

    protected async sendAction(actionType: string, url: string = 'http://127.0.0.1:8491/player', value?: number, additionalData?: any): Promise<void> {
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'User-Agent': 'StreamDeck-Plugin'
                },
                body: JSON.stringify({
                    action: actionType,
                    value: value,
                    timestamp: new Date().toISOString(),
                    ...additionalData
                })
            });

            const data = await response.text();
            streamDeck.logger.info(`POST response for ${actionType}: ${data}`);
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            streamDeck.logger.error(`POST request failed for ${actionType}: ${errorMessage}`);
            throw error;
        }
    }

    override onKeyDown(ev: KeyDownEvent<SpotifySettings>): void {
        streamDeck.logger.info("Key down triggered");
        this.handleAction()
            .catch(error => streamDeck.logger.error(`Error in onKeyDown: ${error}`))
            .then(() => {
                SpotifyBaseAction.updateAllButtonStates();
                SpotifyPlayerDial.updateAllDials();
            });
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

    static async updateAllButtonStates(): Promise<void> {
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

    static async startGlobalUpdate(): Promise<void> {
        if (!SpotifyBaseAction.updateInterval) {
            // Initial update
            await SpotifyBaseAction.updateAllButtonStates();
            await SpotifyPlayerDial.updateAllDials();

            const settings = await streamDeck.settings.getGlobalSettings();
            const refreshRate = Number(settings.refreshRate) * 1000 || 5000;
            streamDeck.logger.info(`Starting global update with refresh rate: ${refreshRate}`);
            SpotifyBaseAction.updateInterval = setInterval(
                async () => {
                    streamDeck.logger.debug("Timer triggered - updating all buttons and dials");
                    await SpotifyBaseAction.updateAllButtonStates();
                    await SpotifyPlayerDial.updateAllDials();
                },
                refreshRate
            );
        } else {
            streamDeck.logger.debug(`Global timer already started`);
        }
    }

    private static stopGlobalUpdate(): void {
        if (SpotifyBaseAction.updateInterval) {
            clearInterval(SpotifyBaseAction.updateInterval);
            SpotifyBaseAction.updateInterval = null;
        }
    }
} 