# Spotify Plus Stream Deck Plugin

This is a plugin to display the current Spotify player status on the Stream Deck + touchbar.

## Ilustration

![illustration](docs/streamdeck+.jpg)

![pause](docs/pause.jpg)

## Features

- Display the current Spotify player status on the Stream Deck + touchbar using 2 dial slots.
- Touch or click on the left dial to play/pause the current song.
- Rotate the left dial to go to the previous/next song.
- Touch or click on the right dial to like/dislike the current song.
- Rotate the right dial to change the spotify player volume (volume via api).

## Installation


To install the plugin, you need to have the Stream Deck software installed.

### Dependencies Installation

You need to have Node.js, pnpm, and Python **3.12** installed on your system.
```
winget install OpenJS.NodeJS.LTS pnpm.pnpm Python.Python.3.12
```

Next, close and reopen your terminal to ensure the `pnpm` command is available.

```
node --version
# should be v22.17.1 or higher
pnpm --version
# should be 10.13.1 or higher
```

Run the following command to setup pnpm
```
pnpm setup
```

Next, close and reopen your terminal.

### Plugin Installation

Next, you need to run the following commands:

```
pnpm add -g @elgato/cli
git clone https://github.com/xmow49/stream-deck-spotify-plus
cd stream-deck-spotify-plus

pnpm install
streamdeck link fr.dbenech.spotify-plus.sdPlugin
streamdeck restart fr.dbenech.spotify-plus 
```

Next, restart the Stream Deck software.

### Spotify API Setup

To use the Spotify API, you need to create a Spotify application and get your client ID and client secret.
1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/applications).
2. Click on "Create an App".
3. Fill in the required fields and click "Create".
4. Set the redirect URI to `http://localhost:4202/callback`.
5. Copy the client ID and client secret into the Stream Deck dial settings.

## Usage

![tuto](docs/tuto.png)

1. Add 2 spotify player dial next to each other on your Stream Deck.
2. Select the Dial position in the dropdown menu (left or right)
3. Enter your Spotify client ID and client secret in the settings.
4. (optional) Change the player refresh rate in the settings (Its only the screen refresh rate, not the Spotify api backend refresh rate)
