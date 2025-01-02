import streamDeck, { action, DialUpEvent, SingletonAction, WillAppearEvent, JsonObject, DialDownEvent, DialRotateEvent, DidReceiveSettingsEvent, WillDisappearEvent, KeyDownEvent, KeyUpEvent, TouchTapEvent } from "@elgato/streamdeck";
import https from 'https';
import http from 'http';

@action({ UUID: "fr.dbenech.spotify-plus.spotify-player" })
export class SpotifyPlayerDial extends SingletonAction<SpotifySettings> {
    private refreshIntervals: Set<NodeJS.Timeout> = new Set();

    private clearAllIntervals(): void {
        this.refreshIntervals.forEach(interval => {
            clearInterval(interval);
        });
        this.refreshIntervals.clear();
    }

    private async downloadImage(url: string, retries = 3): Promise<string> {
        return new Promise((resolve, reject) => {
            // Determine which protocol to use based on URL
            const protocol = url.startsWith('https:') ? https : http;

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

            request.on('error', async (error) => {
                if (retries > 0) {
                    streamDeck.logger.info(`Retrying download, ${retries} attempts remaining`);
                    try {
                        const result = await this.downloadImage(url, retries - 1);
                        resolve(result);
                    } catch (retryError) {
                        reject(retryError);
                    }
                } else {
                    streamDeck.logger.error(`Failed to download image: ${error.message}`);
                    reject(error);
                }
            });

            request.on('timeout', () => {
                request.destroy();
                reject(new Error('Request timeout'));
            });
        });
    }

    override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<SpotifySettings>): Promise<void> {
        if (ev.action.isDial()) {
            // Clear all existing intervals
            this.clearAllIntervals();

            // Set up new interval with updated settings
            let refreshRate = ev.payload.settings.refreshRate || 5;
            const interval = setInterval(() => {
                this.updateImage(ev.action, ev.payload.settings.imgUrl);
            }, refreshRate * 1000);

            this.refreshIntervals.add(interval);
            await this.updateImage(ev.action, ev.payload.settings.imgUrl);
        }
    }

    private async updateImage(action: any, url: string) {
        try {
            const base64Image = await this.downloadImage(url);
            streamDeck.logger.info("Updating image: " + url);
            return action.setFeedback({
                "image": base64Image,
            });
        } catch (error) {
            streamDeck.logger.error(`Failed to set image: ${error}`);
        }
    }

    override async onWillAppear(ev: WillAppearEvent<SpotifySettings>): Promise<void> {
        streamDeck.logger.info("onWillAppear");
        let url = ev.payload.settings.imgUrl;
        let refreshRate = ev.payload.settings.refreshRate || 5;

        if (ev.action.isDial()) {
            ev.action.setFeedbackLayout("layout.json");
            await this.updateImage(ev.action, url);

            const interval = setInterval(() => {
                this.updateImage(ev.action, url);
            }, refreshRate * 1000);

            this.refreshIntervals.add(interval);
        }
    }

    override onWillDisappear(ev: WillDisappearEvent): void {
        this.clearAllIntervals();
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
        const url = ev.payload.settings.imgUrl;
        this.sendAction('tap', url)
            .then(() => this.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onTouchTap: ${error}`));
    }

    override onDialDown(ev: DialDownEvent<SpotifySettings>): void {
        streamDeck.logger.info("onDialDown triggered");
        const url = ev.payload.settings.imgUrl;
        this.sendAction('dialDown', url)
            .then(() => this.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onDialDown: ${error}`));
    }

    override onDialUp(ev: DialUpEvent<SpotifySettings>): void {
        streamDeck.logger.info("onDialUp triggered");
        const url = ev.payload.settings.imgUrl;
        this.sendAction('dialUp', url)
            .then(() => this.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onDialUp: ${error}`));
    }

    override onDialRotate(ev: DialRotateEvent<SpotifySettings>): void {
        streamDeck.logger.info(`onDialRotate triggered with ticks: ${ev.payload.ticks}`);
        const url = ev.payload.settings.imgUrl;
        this.sendAction('rotate', url, ev.payload.ticks)
            .then(() => this.updateImage(ev.action, url))
            .catch(error => streamDeck.logger.error(`Error in onDialRotate: ${error}`));
    }
}

type SpotifySettings = {
    imgUrl: string;
    refreshRate: number;
}
