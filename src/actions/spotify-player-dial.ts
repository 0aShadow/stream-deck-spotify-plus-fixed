import streamDeck, { action, DialUpEvent, SingletonAction, WillAppearEvent, JsonObject, DialDownEvent, DialRotateEvent, DidReceiveSettingsEvent, WillDisappearEvent, KeyDownEvent, KeyUpEvent, TouchTapEvent } from "@elgato/streamdeck";
import https from 'https';
import http from 'http';
import { SpotifySettings } from '../types';
import { SpotifyBaseAction } from "./spotify-base-action";

@action({ UUID: "fr.dbenech.spotify-plus.spotify-player" })
export class SpotifyPlayerDial extends SingletonAction<SpotifySettings> {
    private static dialActions: Map<string, { action: any, url: string }> = new Map();

    private static async updateImage(action: any, url: string) {
        try {
            const protocol = url.startsWith('https:') ? https : http;

            const base64Image = await new Promise<string>((resolve, reject) => {
                const request = protocol.get(url, {
                    timeout: 5000, // 5 second timeout
                    headers: {
                        'User-Agent': 'StreamDeck-Plugin'
                    }
                }, (response) => {
                    if (response.statusCode !== 200) {
                        reject(new Error(`HTTP Error: ${response.statusCode}`));
                        return;
                    }

                    const chunks: Buffer[] = [];

                    response.on('data', (chunk: Buffer) => {
                        chunks.push(chunk);
                    });

                    response.on('end', () => {
                        try {
                            const buffer = Buffer.concat(chunks);
                            const base64Image = `data:image/jpeg;base64,${buffer.toString('base64')}`;
                            resolve(base64Image);
                        } catch (error) {
                            reject(error);
                        }
                    });
                });

                request.on('error', (error) => {
                    streamDeck.logger.error(`Failed to download image url:${url} : ${error}`);
                    reject(error);
                });

                request.on('timeout', () => {
                    request.destroy();
                    reject(new Error('Request timeout'));
                });
            });

            streamDeck.logger.debug("Updating image: " + url);
            return action.setFeedback({
                "image": base64Image,
            });
        } catch (error) {
            streamDeck.logger.error(`Failed to set image: ${error}`);
        }
    }

    override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<SpotifySettings>): Promise<void> {
        if (ev.action.isDial()) {
            let str = ev.payload.settings;
            streamDeck.logger.info("Settings set: " + JSON.stringify(str));
            streamDeck.settings.setGlobalSettings(str.global);

            const url = ev.payload.settings.imgUrl || '';
            const actionId = ev.action.id;
            
            // Update the stored URL for this action
            const existingEntry = SpotifyPlayerDial.dialActions.get(actionId);
            if (existingEntry) {
                existingEntry.url = url;
                await SpotifyPlayerDial.updateImage(ev.action, url);
            }
        }
    }

    override async onWillAppear(ev: WillAppearEvent<SpotifySettings>): Promise<void> {
        const url = String(ev.payload.settings.imgUrl || '');
        const actionId = ev.action.id;

        if (ev.action.isDial()) {
            // Store this action and its URL
            SpotifyPlayerDial.dialActions.set(actionId, { action: ev.action, url: url });
            streamDeck.logger.debug(`Adding dial instance with ID: ${actionId}, total active: ${SpotifyPlayerDial.dialActions.size}`);
            SpotifyBaseAction.startGlobalUpdate();

            ev.action.setFeedbackLayout("layout.json");
            await SpotifyPlayerDial.updateImage(ev.action, url);

        }
    }

    override onWillDisappear(ev: WillDisappearEvent): void {
        const actionId = ev.action.id;

        // Remove this action from our map
        SpotifyPlayerDial.dialActions.delete(actionId);
        streamDeck.logger.debug(`Removing dial instance with ID: ${actionId}, remaining active: ${SpotifyPlayerDial.dialActions.size}`);
    }

    private async sendAction(actionType: string, url: string, value?: number): Promise<void> {
        const protocol = url.startsWith('https:') ? https : http;

        const postData = JSON.stringify({
            action: actionType,
            value: value,
            timestamp: new Date().toISOString()
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

    override onTouchTap(ev: TouchTapEvent<SpotifySettings>): void {
        streamDeck.logger.info("onTouchTap triggered");
        const url = ev.payload.settings.imgUrl || '';
        this.sendAction('tap', url)
            .then(() => SpotifyPlayerDial.updateAllDials())
            .then(() => SpotifyBaseAction.updateAllButtonStates())
            .catch(error => streamDeck.logger.error(`Error in onTouchTap: ${error}`));
    }

    override onDialDown(ev: DialDownEvent<SpotifySettings>): void {
        streamDeck.logger.info("onDialDown triggered");
        const url = ev.payload.settings.imgUrl || '';
        this.sendAction('dialDown', url)
            .then(() => SpotifyPlayerDial.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onDialDown: ${error}`));
    }

    override onDialUp(ev: DialUpEvent<SpotifySettings>): void {
        streamDeck.logger.info("onDialUp triggered");
        const url = ev.payload.settings.imgUrl || '';
        this.sendAction('dialUp', url)
            .then(() => SpotifyPlayerDial.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onDialUp: ${error}`));
    }

    override onDialRotate(ev: DialRotateEvent<SpotifySettings>): void {
        streamDeck.logger.info(`onDialRotate triggered with ticks: ${ev.payload.ticks}`);
        const url = ev.payload.settings.imgUrl || '';
        this.sendAction('rotate', url, ev.payload.ticks)
            .then(() => SpotifyPlayerDial.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onDialRotate url:${url}, error:${error}`));
    }

    static async updateAllDials(): Promise<void> {
        streamDeck.logger.debug("Updating all dials: " + SpotifyPlayerDial.dialActions.size);
        
        // Update all dial actions
        SpotifyPlayerDial.dialActions.forEach(({ action, url }) => {
            if (action && url) {
                SpotifyPlayerDial.updateImage(action, url);
            }
        });
    }
}
