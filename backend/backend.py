"""Backend server for Spotify integration with Stream Deck."""

import time
import logging
import threading
from threading import Lock
from io import BytesIO
import os
from dotenv import load_dotenv

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from PIL import Image, ImageDraw, ImageFont
import requests
from flask import Flask, send_file, request, jsonify
import cairosvg
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Constants
PORT = 8491
DISABLE_FLASK_LOGS = True
REFRESH_RATE_TRACK_END = 15
REFRESH_RATE_PLAYING = 15
REFRESH_RATE_PAUSED = 60

app = Flask(__name__)

# Disable Flask access logs if DISABLE_FLASK_LOGS is True
if DISABLE_FLASK_LOGS:
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

# Load environment variables
load_dotenv()


class SpotifyImageHandler:
    """Handles image generation and storage for Stream Deck display."""

    def __init__(self, spotify_client):
        self.left_image = None
        self.right_image = None
        self.full_image = None
        self.image_lock = Lock()
        # Initialize timing attributes
        self.current_track_start_time = None
        self.current_track_duration = None
        self.spotify_client = spotify_client

    def create_progress_bar(self, draw, current_progress):
        """Draw progress bar on the image."""
        # Draw background
        draw.rounded_rectangle([120, 75, 340, 80], radius=1, fill="#404040")

        # Draw progress
        if current_progress is not None:
            progress_width = int(220 * current_progress)
            draw.rounded_rectangle(
                [120, 75, 120 + progress_width, 80], radius=1, fill="#1DB954"
            )

    def add_heart_icon(self, background, is_liked):
        """Add heart icon to the image."""
        heart_color = "#1DB954" if is_liked else "#404040"

        # Load and colorize SVG
        with open(
            os.path.join(os.path.dirname(__file__), "spotify-like.svg"),
            "r",
            encoding="utf-8",
        ) as file:
            svg_content = file.read().replace(
                "path d=", f'path fill="{heart_color}" d='
            )
        # Convert SVG to PNG
        png_data = cairosvg.svg2png(
            bytestring=svg_content.encode("utf-8"),
            output_width=20,
            output_height=20,
        )
        heart_image = Image.open(BytesIO(png_data))
        background.paste(heart_image, (360, 65), heart_image)

    def save_images(self, background):
        """Save the full, left and right images."""
        with self.image_lock:
            self.full_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.full_image, format="JPEG", quality=100)
            self.full_image.seek(0)

            # Split and save left/right images
            left_half = background.crop((0, 0, 200, 100))
            right_half = background.crop((200, 0, 400, 100))

            self.left_image = BytesIO()
            self.right_image = BytesIO()

            left_half = left_half.convert("RGB")
            right_half = right_half.convert("RGB")

            left_half.save(self.left_image, format="JPEG", quality=100)
            right_half.save(self.right_image, format="JPEG", quality=100)
            self.left_image.seek(0)
            self.right_image.seek(0)

    def _add_album_art(self, background, track_data):
        """Add album art to the background image."""
        response = requests.get(track_data["image_url"], timeout=10)
        album_art = Image.open(BytesIO(response.content))
        album_art = album_art.resize((100, 100))
        background.paste(album_art, (0, 0))

        if not track_data.get("is_playing", True):
            self._add_pause_overlay(background)

    def _add_pause_overlay(self, background):
        """Add pause overlay to album art."""
        overlay = Image.new("RGBA", (100, 100), (0, 0, 0, 128))
        draw_overlay = ImageDraw.Draw(overlay)

        bar_width = 10
        bar_height = 30
        spacing = 10
        start_x = (100 - (2 * bar_width + spacing)) // 2
        start_y = (100 - bar_height) // 2

        for x in (start_x, start_x + bar_width + spacing):
            draw_overlay.rectangle(
                [x, start_y, x + bar_width, start_y + bar_height],
                fill="white",
            )

        background.paste(overlay, (0, 0), overlay)

    def _add_track_info(self, draw, track_data):
        """Add track name and artist information."""
        try:
            title_font = ImageFont.truetype("arial.ttf", 20)
            artist_font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            title_font = ImageFont.load_default()
            artist_font = ImageFont.load_default()

        # Add track name
        track_name = self._truncate_text(track_data["track_name"], title_font, 260)
        draw.text((120, 15), track_name, fill="white", font=title_font)

        # Add artists
        artists = self._truncate_text(track_data["artists"], artist_font, 260)
        draw.text((120, 45), artists, fill="#B3B3B3", font=artist_font)

    def _truncate_text(self, text, font, max_width):
        """Truncate text to fit within max_width."""
        if font.getlength(text) > max_width:
            while font.getlength(text + "...") > max_width:
                text = text[:-1]
            text += "..."
        return text

    def _get_progress(self, override_progress):
        """Get current playback progress."""
        if override_progress is not None:
            return override_progress

        current_playback = self.spotify_client.current_playback()
        if current_playback:
            current_progress_ms = current_playback["progress_ms"]
            total_ms = current_playback["item"]["duration_ms"]
            self.current_track_start_time = time.time() - (current_progress_ms / 1000)
            self.current_track_duration = total_ms / 1000
            return current_progress_ms / total_ms
        return None


class SpotifyTrackInfo:
    """Handles Spotify track information and control."""

    def __init__(self):
        # Configure retry strategy
        retry_strategy = Retry(
            total=0,  # Disable automatic retries
            status_forcelist=[],  # Empty list to disable retries
        )

        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)

        scope = (
            "user-read-currently-playing user-read-playback-state "
            "user-library-read user-library-modify user-modify-playback-state"
        )
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=os.getenv("SPOTIFY_CLIENT_ID"),
                client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
                redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
                scope=scope,
            ),
            requests_session=session,
            requests_timeout=10,
        )
        self.image_handler = SpotifyImageHandler(self.sp)
        self.last_track_info = None
        self.is_playing = False
        self.last_volume_update = 0
        self.last_rotate_time = 0
        self.volume_update_delay = 0.1  # 100ms minimum between volume updates
        self.volume_refresh_delay = 10.0  # 10s before refreshing volume from API
        self.pending_volume_change = 0  # Pour accumuler les changements de volume
        self.current_volume = None  # Pour tracker le volume localement
        # Initialize track change attributes
        self.last_track_change_time = 0
        self.last_track_change_direction = None

        # Initialize empty images right away
        self.image_handler.left_image = BytesIO()
        self.image_handler.right_image = BytesIO()
        self.image_handler.full_image = BytesIO()

        # Create a default black image
        default_img = Image.new("RGB", (400, 100), "black")
        self.image_handler.save_images(default_img)

    def _format_retry_time(self, seconds):
        """Format seconds into readable time format (e.g., 2h 30m 15s)."""
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if hours > 0:
            parts.append(f"{int(hours)}h")
        if minutes > 0:
            parts.append(f"{int(minutes)}m")
        if seconds > 0 or not parts:  # include seconds if it's the only value
            parts.append(f"{int(seconds)}s")

        return " ".join(parts)

    def create_rate_limit_image(self, retry_after):
        """Create an image showing rate limit message."""
        background = Image.new("RGB", (400, 100), "black")
        draw = ImageDraw.Draw(background)

        try:
            font = ImageFont.truetype("arial.ttf", 20)
        except OSError:
            font = ImageFont.load_default()

        formatted_time = self._format_retry_time(retry_after)
        message = f"Too Many Requests\nRetry after: {formatted_time}"
        draw.text((20, 35), message, fill="white", font=font)

        self.image_handler.save_images(background)

    def create_status_images(self, current_track_info, override_progress=None):
        """Create status images for Stream Deck display."""
        try:
            # Create base image
            background = Image.new("RGB", (400, 100), "black")
            draw = ImageDraw.Draw(background)

            # Add album art
            self._add_album_art(background, current_track_info)

            # Add track info
            self._add_track_info(draw, current_track_info)

            # Add progress bar
            current_progress = self._get_progress(override_progress)
            self.image_handler.create_progress_bar(draw, current_progress)

            # Add heart icon
            is_liked = self.sp.current_user_saved_tracks_contains(
                [current_track_info["track_id"]]
            )[0]
            self.image_handler.add_heart_icon(background, is_liked)

            # Save images
            self.image_handler.save_images(background)
            return True

        except (requests.RequestException, IOError, ValueError) as e:
            print(f"Error creating images: {str(e)}")
            return False

    def _add_album_art(self, background, track_data):
        """Add album art to the background image."""
        response = requests.get(track_data["image_url"], timeout=10)
        album_art = Image.open(BytesIO(response.content))
        album_art = album_art.resize((100, 100))
        background.paste(album_art, (0, 0))

        if not track_data.get("is_playing", True):
            self._add_pause_overlay(background)

    def _add_pause_overlay(self, background):
        """Add pause overlay to album art."""
        overlay = Image.new("RGBA", (100, 100), (0, 0, 0, 128))
        draw_overlay = ImageDraw.Draw(overlay)

        bar_width = 10
        bar_height = 30
        spacing = 10
        start_x = (100 - (2 * bar_width + spacing)) // 2
        start_y = (100 - bar_height) // 2

        for x in (start_x, start_x + bar_width + spacing):
            draw_overlay.rectangle(
                [x, start_y, x + bar_width, start_y + bar_height],
                fill="white",
            )

        background.paste(overlay, (0, 0), overlay)

    def _add_track_info(self, draw, track_data):
        """Add track name and artist information."""
        try:
            title_font = ImageFont.truetype("arial.ttf", 20)
            artist_font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            title_font = ImageFont.load_default()
            artist_font = ImageFont.load_default()

        # Add track name
        track_name = self._truncate_text(track_data["track_name"], title_font, 260)
        draw.text((120, 15), track_name, fill="white", font=title_font)

        # Add artists
        artists = self._truncate_text(track_data["artists"], artist_font, 260)
        draw.text((120, 45), artists, fill="#B3B3B3", font=artist_font)

    def _truncate_text(self, text, font, max_width):
        """Truncate text to fit within max_width."""
        if font.getlength(text) > max_width:
            while font.getlength(text + "...") > max_width:
                text = text[:-1]
            text += "..."
        return text

    def _get_progress(self, override_progress):
        """Get current playback progress."""
        if override_progress is not None:
            return override_progress

        current_playback = self.sp.current_playback()
        if current_playback:
            current_progress_ms = current_playback["progress_ms"]
            total_ms = current_playback["item"]["duration_ms"]
            self.image_handler.current_track_start_time = time.time() - (
                current_progress_ms / 1000
            )
            self.image_handler.current_track_duration = total_ms / 1000
            return current_progress_ms / total_ms
        return None

    def get_current_track_info(self):
        """Get current track information from Spotify."""
        try:
            current_track = self.sp.current_user_playing_track()

            if current_track is not None and current_track["item"] is not None:
                # Update playing state
                self.is_playing = current_track["is_playing"]
                track_data = {
                    "track_name": current_track["item"]["name"],
                    "image_url": current_track["item"]["album"]["images"][0]["url"],
                    "artists": ", ".join(
                        [artist["name"] for artist in current_track["item"]["artists"]]
                    ),
                    "is_playing": self.is_playing,
                    "progress_ms": current_track["progress_ms"],
                    "duration_ms": current_track["item"]["duration_ms"],
                    "track_id": current_track["item"]["id"],
                }
                return track_data

            # Reset playing state when no track
            self.is_playing = False
            return {"error": "No track currently playing"}

        except (
            spotipy.SpotifyException,
            requests.RequestException,
            KeyError,
            IndexError,
        ) as e:
            self.is_playing = False
            if e.http_status == 429:  # Too Many Requests
                retry_after = int(e.headers.get("Retry-After", 1))
                formatted_time = self._format_retry_time(retry_after)
                error_msg = f"Rate limited. Retry after {formatted_time}"
                print(error_msg)
                self.create_rate_limit_image(retry_after)
                return {"error": error_msg}
            return {"error": f"Error occurred: {str(e)}"}

    def list_devices(self):
        """Get and print all available Spotify devices."""
        try:
            devices = self.sp.devices()
            if devices and devices["devices"]:
                print("\nAvailable Spotify devices:")
                for device in devices["devices"]:
                    active = "* " if device["is_active"] else "  "
                    print(
                        f"{active}{device['name']} ({device['type']}) - ID: {device['id']}"
                    )
                return devices["devices"]
            print("No available devices found")
            return []
        except (spotipy.SpotifyException, requests.RequestException, KeyError) as e:
            print(f"Error getting devices: {str(e)}")
            return []


# Create SpotifyTrackInfo instance before Flask routes
spotify_info = SpotifyTrackInfo()


# Flask routes
@app.route("/left", methods=["POST"])
def handle_left_action():
    """Handle left button actions (play/pause, next/previous track)."""
    try:
        data = request.get_json()
        if not data or "action" not in data:
            return jsonify({"status": "error", "message": "Invalid action data"}), 400

        # Handle rotation for next/previous track
        if data["action"] == "rotate":
            return _handle_track_change(data)

        # Handle existing play/pause logic for tap and dialDown
        if data["action"] not in ("tap", "dialDown"):
            return jsonify({"status": "error", "message": "No active playback"}), 400

        current_playback = spotify_info.sp.current_playback()
        current_playing_state = current_playback and current_playback.get("is_playing")

        # Update UI immediately
        if spotify_info.last_track_info:
            spotify_info.last_track_info["is_playing"] = not current_playing_state
            spotify_info.create_status_images(spotify_info.last_track_info)
        spotify_info.is_playing = not current_playing_state
        # Send immediate response
        response = {"status": "success", "message": "Playback toggled"}
        threading.Thread(
            target=_async_playback_toggle, args=(current_playing_state,), daemon=True
        ).start()

        return jsonify(response), 200

    except (ValueError, TypeError) as e:
        print(f"Error handling left action: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except spotipy.SpotifyException as e:
        print(f"Spotify API error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _async_playback_toggle(current_playing_state):
    """Handle the actual API call asynchronously."""
    try:
        if current_playing_state:
            spotify_info.sp.pause_playback()
        else:
            try:
                spotify_info.sp.start_playback()
            except spotipy.SpotifyException:
                # Try forced playback if simple play fails
                _handle_forced_playback(None)
    except spotipy.SpotifyException as e:
        print(f"Error in async playback toggle: {str(e)}")


@app.route("/right", methods=["POST"])
def handle_right_action():
    """Handle right button actions (like/unlike, etc)."""
    try:
        data = request.get_json()
        if not data or "action" not in data:
            return jsonify({"status": "error", "message": "Invalid action data"}), 400

        response = _process_right_action(data)
        return jsonify(response[0]), response[1]

    except (spotipy.SpotifyException, requests.RequestException) as e:
        print(f"Error handling right action: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _process_right_action(data):
    """Process right button action."""
    action = data["action"]

    if action in ("tap", "dialDown"):
        return _handle_like_toggle()

    if action == "rotate":
        try:
            print(f"Rotate action received: {data}")
            now = time.time()
            value = data.get("value", 0) * 3

            # Refresh volume from API if too much time has passed since last rotation
            if now - spotify_info.last_rotate_time > spotify_info.volume_refresh_delay:
                current_playback = spotify_info.sp.current_playback()
                if not current_playback:
                    return {"status": "error", "message": "No active playback"}, 400
                spotify_info.current_volume = current_playback["device"][
                    "volume_percent"
                ]
            # Initialize current_volume if not set
            elif spotify_info.current_volume is None:
                current_playback = spotify_info.sp.current_playback()
                if not current_playback:
                    return {"status": "error", "message": "No active playback"}, 400
                spotify_info.current_volume = current_playback["device"][
                    "volume_percent"
                ]

            # Calculate new volume
            new_volume = max(0, min(100, spotify_info.current_volume + value))
            spotify_info.current_volume = new_volume
            spotify_info.last_rotate_time = now

            # Only make API call if enough time has passed
            if (
                now - spotify_info.last_volume_update
                >= spotify_info.volume_update_delay
            ):
                spotify_info.sp.volume(new_volume)
                spotify_info.last_volume_update = now

            return {"status": "success", "message": f"Volume set to {new_volume}%"}, 200

        except spotipy.SpotifyException as e:
            print(f"Error adjusting volume: {str(e)}")
            return {"status": "error", "message": str(e)}, 500

    return {"status": "error", "message": "Invalid action"}, 400


def _handle_like_toggle():
    """Handle toggling track like status."""
    current_track_info = spotify_info.last_track_info
    if not current_track_info or "track_id" not in current_track_info:
        return {"status": "error", "message": "No track currently playing"}, 400

    track_id = current_track_info["track_id"]
    is_liked = spotify_info.sp.current_user_saved_tracks_contains([track_id])[0]

    if is_liked:
        spotify_info.sp.current_user_saved_tracks_delete([track_id])
    else:
        spotify_info.sp.current_user_saved_tracks_add([track_id])

    spotify_info.create_status_images(current_track_info)
    return {
        "status": "success",
        "message": "Unliked track" if is_liked else "Liked track",
    }, 200


@app.route("/left", methods=["GET"])
def serve_left():
    """Serve the left image for Stream Deck display."""
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.image_handler.left_image:
                img_copy = BytesIO(spotify_info.image_handler.left_image.getvalue())
                return send_file(img_copy, mimetype="image/bmp")
        return "Image not found", 404
    except IOError as e:
        print(f"Error serving left image: {str(e)}")
        return str(e), 500


@app.route("/right", methods=["GET"])
def serve_right():
    """Serve the right image for Stream Deck display."""
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.image_handler.right_image:
                img_copy = BytesIO(spotify_info.image_handler.right_image.getvalue())
                return send_file(img_copy, mimetype="image/bmp")
        return "Image not found", 404
    except IOError as e:
        print(f"Error serving right image: {str(e)}")
        return str(e), 500


@app.route("/all", methods=["GET"])
def serve_all():
    """Serve the complete image."""
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.image_handler.full_image:
                img_copy = BytesIO(spotify_info.image_handler.full_image.getvalue())
                return send_file(img_copy, mimetype="image/jpeg")
        return "Image not found", 404
    except IOError as e:
        print(f"Error serving full image: {str(e)}")
        return str(e), 500


def run_flask():
    """Run the Flask server."""
    app.run(host="localhost", port=PORT)


def _handle_forced_playback(initial_error):
    """Handle playback with device forcing when simple play fails."""
    try:
        devices = spotify_info.sp.devices()
        if not devices or not devices["devices"]:
            return {
                "status": "error",
                "message": f"No available devices: {str(initial_error)}",
            }, 400

        # Try to find an active device
        active_device = next(
            (device for device in devices["devices"] if device["is_active"]),
            devices["devices"][0],
        )

        # Try to start playback on the selected device
        spotify_info.sp.start_playback(device_id=active_device["id"])
        return {"status": "success", "message": "Playback started"}, 200

    except (spotipy.SpotifyException, requests.RequestException, KeyError) as e:
        return {
            "status": "error",
            "message": f"Error during forced playback: {str(e)}",
        }, 400


def _handle_track_change(data):
    """Handle next/previous track based on rotation direction."""
    print(f"Track change action received: {data}")
    try:
        now = time.time()
        value = data.get("value", 0)

        # If less than 500ms has passed since last action, ignore
        if now - spotify_info.last_track_change_time < 0.5:
            return {"status": "ignored", "message": "Action ignored (too soon)"}, 200

        # If direction changed or more than 2 seconds passed, allow action
        if (
            spotify_info.last_track_change_direction is None
            or now - spotify_info.last_track_change_time > 2.0
            or (value > 0) != (spotify_info.last_track_change_direction > 0)
        ):
            spotify_info.last_track_change_time = now
            spotify_info.last_track_change_direction = value

            if value > 0:
                spotify_info.sp.next_track()
                print("Skipped to next track")
                message = "Skipped to next track"
            else:
                spotify_info.sp.previous_track()
                print("Returned to previous track")
                message = "Returned to previous track"

            # Petit délai pour laisser Spotify mettre à jour l'état
            time.sleep(0.1)

            # Mise à jour des informations de la piste
            track_info = spotify_info.get_current_track_info()
            if "error" not in track_info:
                print(f"Updated to: {track_info['track_name']}")
                spotify_info.last_track_info = track_info
                spotify_info.create_status_images(track_info)

            return {"status": "success", "message": message}, 200

        return {"status": "ignored", "message": "Action ignored"}, 200

    except spotipy.SpotifyException as e:
        print(f"Error changing track: {str(e)}")
        return {"status": "error", "message": str(e)}, 500


if __name__ == "__main__":
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    IS_FIRST_RUN = True
    LAST_API_CALL = 0

    while True:
        current_time = time.time()

        # Check if track has ended
        if (
            spotify_info.image_handler.current_track_start_time
            and spotify_info.image_handler.current_track_duration
            and current_time - spotify_info.image_handler.current_track_start_time
            >= spotify_info.image_handler.current_track_duration
        ):
            # Force refresh when track ends
            track_info = spotify_info.get_current_track_info()
            LAST_API_CALL = current_time
            if "error" not in track_info:
                print(f"\nTrack ended, refreshing: {track_info['track_name']}")
                spotify_info.last_track_info = track_info
                spotify_info.create_status_images(track_info)
            continue

        # Determine refresh rate based on playback state
        if spotify_info.last_track_info and "error" not in spotify_info.last_track_info:
            progress_ms = spotify_info.last_track_info.get("progress_ms", 0)
            duration_ms = spotify_info.last_track_info.get("duration_ms", 0)
            is_playing = spotify_info.last_track_info.get("is_playing", False)

            # Near end of track (last 10 seconds)
            if is_playing and duration_ms - progress_ms <= 10000:
                CURRENT_REFRESH_RATE = REFRESH_RATE_TRACK_END
            # Normal playback
            elif is_playing:
                CURRENT_REFRESH_RATE = REFRESH_RATE_PLAYING
            # Paused or not playing
            else:
                CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
        else:
            # Default refresh rate when no track info
            CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED

        # API call based on refresh rate
        if current_time - LAST_API_CALL >= CURRENT_REFRESH_RATE or IS_FIRST_RUN:
            track_info = spotify_info.get_current_track_info()
            LAST_API_CALL = current_time

            if "error" not in track_info:
                print(f"\nCurrent track: {track_info['track_name']}")
                print(f"Artists: {track_info['artists']}")
                print(f"Album art URL: {track_info['image_url']}")
                spotify_info.last_track_info = track_info
                spotify_info.create_status_images(track_info)
            else:
                print(f"\n{track_info['error']}")

        # Update progress bar every second if we have track timing information
        elif (
            spotify_info.image_handler.current_track_start_time
            and spotify_info.image_handler.current_track_duration
            and spotify_info.last_track_info
            and spotify_info.is_playing
        ):
            elapsed_time = (
                current_time - spotify_info.image_handler.current_track_start_time
            )
            progress_ratio = min(
                elapsed_time / spotify_info.image_handler.current_track_duration, 1.0
            )
            spotify_info.create_status_images(
                spotify_info.last_track_info, override_progress=progress_ratio
            )

        if IS_FIRST_RUN:
            print("Status images updated")
            print("Access the images at:")
            print(f"http://localhost:{PORT}/left")
            print(f"http://localhost:{PORT}/right")
            IS_FIRST_RUN = False
        time.sleep(1)
