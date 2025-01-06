export interface SpotifySettings {
    global: {
        clientId?: string;
        clientSecret?: string;
        refreshRate?: number;
    };
    imgUrl?: string;
    volume?: number;
    playlistUri?: string;
    [key: string]: any; // Add index signature for JsonObject compatibility
}

export interface ButtonStates {
    is_playing: boolean;
    is_liked: boolean;
    is_shuffle: boolean;
    is_muted: boolean;
} 