"""Backend server for Spotify integration with Stream Deck."""

import time
import traceback
import logging
import threading
from threading import Lock
from io import BytesIO
import os
import socket
import sys
from dotenv import load_dotenv

import debugpy

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from PIL import Image, ImageDraw, ImageFont
import requests
from flask import Flask, send_file, request, jsonify
import platform

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from single_dial import SingleDialImageHandler
from font_utils import get_unicode_font

# Constants
PORT = 8491
DISABLE_FLASK_LOGS = True
REFRESH_RATE_PLAYING = 15
REFRESH_RATE_PAUSED = 60

# Configure logging for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/backend.log', mode='w'),  # Reset file each time
        # logging.StreamHandler()  # Keep console output
    ]
)

logger = logging.getLogger(__name__)

# Set external library log levels
spotipy_logger = logging.getLogger("spotipy")
spotipy_logger.setLevel(logging.WARNING)

pil_logger = logging.getLogger("PIL")
pil_logger.setLevel(logging.WARNING)


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
        # Add image cache
        self.album_art_cache = {}
        self.current_image_url = None

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
        # Choose the appropriate PNG file based on liked status
        icon_filename = "spotify-liked.png" if is_liked else "spotify-like.png"
        icon_path = os.path.join(os.path.dirname(__file__), icon_filename)
        
        try:
            # Load and resize the PNG image
            heart_image = Image.open(icon_path)
            heart_image = heart_image.resize((20, 20), Image.Resampling.LANCZOS)
            
            # Paste the image with transparency support
            if heart_image.mode == 'RGBA':
                background.paste(heart_image, (360, 65), heart_image)
            else:
                background.paste(heart_image, (360, 65))
        except FileNotFoundError:
            logger.warning(f"Heart icon file not found: {icon_path}")
        except Exception as e:
            logger.error(f"Error loading heart icon: {str(e)}")

    def save_images(self, background):
        """Save the full, left and right images."""
        with self.image_lock:
            logger.debug("Image lock acquired, saving images")
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
            logger.debug("Images saved successfully")

    def _add_album_art(self, background, track_data):
        """Add album art to the background image with caching."""
        image_url = track_data["image_url"]
        logger.debug(f"Adding album art from URL: {image_url[:50]}...")

        # Use cached image if URL hasn't changed
        if image_url == self.current_image_url and image_url in self.album_art_cache:
            album_art = self.album_art_cache[image_url]
        else:
            # Download and cache new image
            logger.debug("Downloading new album art")
            response = requests.get(image_url, timeout=10)
            logger.debug(f"Album art downloaded, status: {response.status_code}")
            album_art = Image.open(BytesIO(response.content))
            album_art = album_art.resize((100, 100))
            # Update cache
            self.album_art_cache = {image_url: album_art}  # Only keep latest image
            self.current_image_url = image_url

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
        title_font = get_unicode_font(20)
        artist_font = get_unicode_font(16)

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

    def create_login_message_image(self):
        """Create an image showing login message."""
        background = Image.new("RGB", (400, 100), "black")
        draw = ImageDraw.Draw(background)

        font = get_unicode_font(24)

        message = "Please Login to Spotify"
        # Get text size for centering
        text_bbox = draw.textbbox((0, 0), message, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        # Calculate center position
        x = (400 - text_width) // 2
        y = (100 - text_height) // 2

        draw.text((x, y), message, fill="white", font=font)
        self.save_images(background)

    def create_error_message_image(self, error_message):
        """Create an image showing error message."""
        background = Image.new("RGB", (400, 100), "black")
        draw = ImageDraw.Draw(background)

        title_font = get_unicode_font(20)
        desc_font = get_unicode_font(16)

        # Draw title
        title = "Error"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        x = (400 - title_width) // 2
        draw.text((x, 20), title, fill="#FF0000", font=title_font)

        # Draw error message
        # Wrap text if too long
        words = error_message.split()
        lines = []
        current_line = []
        for word in words:
            current_line.append(word)
            test_line = " ".join(current_line)
            if desc_font.getlength(test_line) > 360:  # Leave some margin
                if len(current_line) > 1:
                    current_line.pop()
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    lines.append(test_line)
                    current_line = []
        if current_line:
            lines.append(" ".join(current_line))

        # Draw each line
        y = 50
        for line in lines[:2]:  # Limit to 2 lines
            bbox = draw.textbbox((0, 0), line, font=desc_font)
            width = bbox[2] - bbox[0]
            x = (400 - width) // 2
            draw.text((x, y), line, fill="white", font=desc_font)
            y += 20

        self.save_images(background)


class TrackState:
    """Class to hold track-related state."""

    def __init__(self):
        self.current_id = None
        self.current_liked = False
        self.last_info = None
        self.is_playing = False
        self.shuffle_state = False  # Added shuffle state
        # Add track change timing attributes
        self.last_track_change_time = 0
        self.last_track_change_direction = None


class VolumeState:
    """Class to hold volume-related state."""

    def __init__(self):
        self.last_update = 0
        self.current = None
        self.last_rotate_time = 0
        self.update_delay = 0.1
        self.refresh_delay = 10.0
        self.last_unmuted_volume = 50  # Store the last unmuted volume


class SeekState:
    """Class to hold seek-related state."""

    def __init__(self):
        self.last_update = 0
        self.current_position_ms = None
        self.track_duration_ms = None
        self.last_seek_time = 0
        self.update_delay = 0.3  # Wait 300ms before API call to allow multiple rapid seeks
        self.refresh_delay = 5.0  # Refresh position from API every 5 seconds
        self.pending_seek_ms = 0  # Accumulated seek amount


class SpotifyTrackInfo:
    """Handles Spotify track information and control."""

    def __init__(self):
        # Configure retry strategy
        retry_strategy = Retry(
            total=0,
            status_forcelist=[],
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
        self.single_dial = SingleDialImageHandler(self, self.image_handler)
        self.track = TrackState()
        self.volume = VolumeState()
        self.seek = SeekState()

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

        font = get_unicode_font(20)

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

            # Add album art and track info
            self.image_handler._add_album_art(background, current_track_info)
            self._add_track_info(draw, current_track_info)

            # Add progress bar
            current_progress = self._get_progress(override_progress)
            self.image_handler.create_progress_bar(draw, current_progress)

            # Check if track changed and update liked status if needed
            track_id = current_track_info["track_id"]
            if track_id != self.track.current_id:
                self.track.current_id = track_id
                self.track.current_liked = self.sp.current_user_saved_tracks_contains(
                    [track_id]
                )[0]

            # Add heart icon with cached liked status
            self.image_handler.add_heart_icon(background, self.track.current_liked)

            # Save images
            self.image_handler.save_images(background)
            return True

        except (requests.RequestException, IOError, ValueError) as e:
            logger.error(f"Error creating images: {str(e)}")
            return False

    def _add_track_info(self, draw, track_data):
        """Add track name and artist information."""
        title_font = get_unicode_font(20)
        artist_font = get_unicode_font(16)

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
            logger.debug(f"Using override progress: {override_progress}")
            return override_progress

        logger.debug("Getting current playback progress from API")
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
        logger.debug("Getting current track info from Spotify API")
        try:
            current_track = self.sp.current_user_playing_track()
            logger.debug("Spotify API call for current track completed")

            if current_track is not None and current_track["item"] is not None:
                # Update playing state
                self.track.is_playing = current_track["is_playing"]
                logger.debug(f"Track found: {current_track['item']['name']}, playing: {self.track.is_playing}")
                track_data = {
                    "track_name": current_track["item"]["name"],
                    "image_url": current_track["item"]["album"]["images"][0]["url"],
                    "artists": ", ".join(
                        [artist["name"] for artist in current_track["item"]["artists"]]
                    ),
                    "is_playing": self.track.is_playing,
                    "progress_ms": current_track["progress_ms"],
                    "duration_ms": current_track["item"]["duration_ms"],
                    "track_id": current_track["item"]["id"],
                }
                return track_data

            # Reset playing state when no track
            self.track.is_playing = False
            logger.debug("No track currently playing")
            return {"no_track": True}  # Changed from "error" to "no_track"

        except (
            spotipy.SpotifyException,
            requests.RequestException,
            KeyError,
            IndexError,
        ) as e:
            self.track.is_playing = False
            if hasattr(e, 'http_status') and e.http_status == 429:  # Too Many Requests
                retry_after = int(e.headers.get("Retry-After", 1))
                formatted_time = self._format_retry_time(retry_after)
                error_msg = f"Rate limited. Retry after {formatted_time}"
                logger.warning(error_msg)
                self.create_rate_limit_image(retry_after)
                return {"error": error_msg}
            elif hasattr(e, 'http_status') and e.http_status == 401:  # Unauthorized
                return {"auth_error": f"Authentication failed: {str(e)}"}
            return {"error": f"Error occurred: {str(e)}"}

    def create_no_track_image(self):
        """Create an image showing no track is playing with pause layout."""
        background = Image.new("RGB", (400, 100), "black")
        draw = ImageDraw.Draw(background)

        # Create a dark album art area with pause icon
        album_area = Image.new("RGB", (100, 100), "#1a1a1a")
        
        # Add pause overlay (same as the existing one)
        self._add_pause_overlay_no_track(album_area)
        background.paste(album_area, (0, 0))

        title_font = get_unicode_font(20)
        artist_font = get_unicode_font(16)

        # Add placeholder text
        draw.text((120, 15), "No track playing", fill="white", font=title_font)
        draw.text((120, 45), "Start Spotify to play music", fill="#B3B3B3", font=artist_font)

        # Draw progress bar background (empty)
        draw.rounded_rectangle([120, 75, 340, 80], radius=1, fill="#404040")

        # Add heart icon (not liked state)
        self.image_handler.add_heart_icon(background, False)

        # Save images
        self.image_handler.save_images(background)

    def _add_pause_overlay_no_track(self, background):
        """Add pause overlay to empty album art area."""
        overlay = Image.new("RGBA", (100, 100), (0, 0, 0, 0))  # Transparent background
        draw_overlay = ImageDraw.Draw(overlay)

        bar_width = 12
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

    def list_devices(self):
        """Get and print all available Spotify devices."""
        try:
            devices = self.sp.devices()
            if devices and devices["devices"]:
                logger.info("\nAvailable Spotify devices:")
                for device in devices["devices"]:
                    active = "* " if device["is_active"] else "  "
                    logger.info(
                        f"{active}{device['name']} ({device['type']}) - ID: {device['id']}"
                    )
                return devices["devices"]
            logger.info("No available devices found")
            return []
        except (spotipy.SpotifyException, requests.RequestException, KeyError) as e:
            logger.error(f"Error getting devices: {str(e)}")
            return []

    def _try_activate_device(self):
        """Try to activate an available device when no active device is found."""
        try:
            logger.debug("No active device found, trying to activate one...")
            devices = self.sp.devices()
            
            if not devices or not devices.get("devices"):
                logger.warning("No devices available to activate")
                return None
                
            available_devices = devices["devices"]
            logger.debug(f"Found {len(available_devices)} available devices")
            
            # Check if there's already an active device
            active_device = next((d for d in available_devices if d["is_active"]), None)
            if active_device:
                logger.debug(f"Active device found: {active_device['name']}")
                return active_device["id"]
            
            # Try to use the preferred device from environment variable
            preferred_device_id = os.getenv("SPOTIFY_THIS_DEVICE")
            if preferred_device_id:
                preferred_device = next((d for d in available_devices if d["id"] == preferred_device_id), None)
                if preferred_device:
                    logger.debug(f"Using preferred device: {preferred_device['name']}")
                    return preferred_device["id"]
            
            # Otherwise, use the first available device (prefer computer/smartphone over others)
            priority_types = ["Computer", "Smartphone", "Tablet"]
            for device_type in priority_types:
                device = next((d for d in available_devices if d["type"] == device_type), None)
                if device:
                    logger.debug(f"Selected {device_type} device: {device['name']}")
                    return device["id"]
            
            # If no priority device found, use the first one
            if available_devices:
                device = available_devices[0]
                logger.debug(f"Using first available device: {device['name']}")
                return device["id"]
                
            return None
            
        except Exception as e:
            logger.error(f"Error trying to activate device: {str(e)}")
            return None

    def _handle_like_toggle(self):
        """Handle toggling track like status."""
        current_track_info = self.track.last_info
        if not current_track_info or "track_id" not in current_track_info:
            return {"status": "error", "message": "No track currently playing"}, 400

        try:
            if self.track.current_liked:
                self.sp.current_user_saved_tracks_delete([self.track.current_id])
            else:
                self.sp.current_user_saved_tracks_add([self.track.current_id])

            # Update cached status
            self.track.current_liked = not self.track.current_liked

            # Immédiatement recréer les images avec le nouveau statut
            self.create_status_images(current_track_info)
            self.single_dial.create_single_dial_image(current_track_info)

            return {
                "status": "success",
                "message": "Unliked track"
                if not self.track.current_liked
                else "Liked track",
            }, 200
        except (spotipy.SpotifyException, requests.RequestException) as e:
            logger.error(f"Error toggling like status: {str(e)}")
            return {"status": "error", "message": str(e)}, 500

    def update_button_states(self):
        """Update all button states based on current playback."""
        logger.debug("Updating button states")
        try:
            current_playback = self.sp.current_playback()
            logger.debug("Got current playback for button states")
            if not current_playback:
                return False

            is_playing = current_playback.get("is_playing", False)
            is_shuffle = current_playback.get("shuffle_state", False)
            is_muted = current_playback["device"]["volume_percent"] == 0

            # Get current track's like status
            track_id = current_playback["item"]["id"]
            is_liked = self.sp.current_user_saved_tracks_contains([track_id])[0]

            # Update the existing state objects
            self.track.is_playing = is_playing
            self.track.current_liked = is_liked
            self.track.shuffle_state = is_shuffle
            
            logger.debug("Button states updated successfully")
            return True
        except Exception as e:
            logger.error(f"Error updating button states: {str(e)}")
            return False

    def handle_player_action(self, action_type):
        """Handle player actions."""
        try:
            if action_type == "next":
                self.sp.next_track()
            elif action_type == "previous":
                self.sp.previous_track()
            elif action_type == "play":
                current_playback = self.sp.current_playback()
                if current_playback and current_playback["is_playing"]:
                    self.sp.pause_playback()
                else:
                    self.sp.start_playback()
            elif action_type == "like":
                current_track = self.sp.current_user_playing_track()
                if current_track:
                    track_id = current_track["item"]["id"]
                    if self.sp.current_user_saved_tracks_contains([track_id])[0]:
                        self.sp.current_user_saved_tracks_delete([track_id])
                    else:
                        self.sp.current_user_saved_tracks_add([track_id])
            elif action_type == "shuffle":
                if not self._check_premium():
                    return {"error": "Shuffle control requires Spotify Premium"}, 403
                current_playback = self.sp.current_playback()
                if current_playback:
                    new_state = not current_playback["shuffle_state"]
                    self.sp.shuffle(new_state)
            elif action_type == "volumeup":
                current_volume = self.sp.current_playback()["device"]["volume_percent"]
                new_volume = min(100, current_volume + 10)
                self.sp.volume(new_volume)
            elif action_type == "volumedown":
                current_volume = self.sp.current_playback()["device"]["volume_percent"]
                new_volume = max(0, current_volume - 10)
                self.sp.volume(new_volume)
            elif action_type == "volumemute":
                current_volume = self.sp.current_playback()["device"]["volume_percent"]
                if current_volume > 0:
                    self.volume.last_unmuted_volume = current_volume
                    self.sp.volume(0)
                else:
                    self.sp.volume(self.volume.last_unmuted_volume)
            elif action_type == "volumeset":
                volume_level = request.json.get("volume", 50)
                self.sp.volume(volume_level)
            elif action_type == "playlist":
                playlist_uri = request.json.get("playlist_uri")
                if playlist_uri:
                    self.sp.start_playback(context_uri=playlist_uri)

            # Wait for Spotify to update state
            time.sleep(0.5)

            # Get current states
            current_playback = self.sp.current_playback()
            current_track = self.sp.current_user_playing_track()

            button_states = {
                "is_playing": current_playback["is_playing"]
                if current_playback
                else False,
                "is_liked": self.sp.current_user_saved_tracks_contains(
                    [current_track["item"]["id"]]
                )[0]
                if current_track and current_track["item"]
                else False,
                "is_shuffle": current_playback["shuffle_state"]
                if current_playback
                else False,
                "is_muted": current_playback["device"]["volume_percent"] == 0
                if current_playback and current_playback["device"]
                else False,
            }

            return {"success": True, "states": button_states}

        except Exception as e:
            return {"error": str(e)}, 500


# Create SpotifyTrackInfo instance before Flask routes
spotify_info = SpotifyTrackInfo()

@app.route("/left", methods=["GET"])
def serve_left():
    """Serve the left image for Stream Deck display."""
    # logger.debug("Request for left image received")
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.image_handler.left_image:
                img_copy = BytesIO(spotify_info.image_handler.left_image.getvalue())
                return send_file(img_copy, mimetype="image/bmp")
        return "Image not found", 404
    except IOError as e:
        logger.error(f"Error serving left image: {str(e)}")
        return str(e), 500


@app.route("/right", methods=["GET"])
def serve_right():
    """Serve the right image for Stream Deck display."""
    # logger.debug("Request for right image received")
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.image_handler.right_image:
                img_copy = BytesIO(spotify_info.image_handler.right_image.getvalue())
                return send_file(img_copy, mimetype="image/bmp")
        return "Image not found", 404
    except IOError as e:
        logger.error(f"Error serving right image: {str(e)}")
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
        logger.error(f"Error serving full image: {str(e)}")
        return str(e), 500


@app.route("/single", methods=["GET"])
def serve_single():
    """Serve the single dial image."""
    # logger.debug("Request for single dial image received")
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.single_dial.single_image:
                img_copy = BytesIO(spotify_info.single_dial.single_image.getvalue())
                return send_file(img_copy, mimetype="image/jpeg")
        return "Image not found", 404
    except IOError as e:
        logger.error(f"Error serving single dial image: {str(e)}")
        return str(e), 500


def check_port_available(port):
    """Check if a port is available for use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', port))
        return True
    except socket.error:
        return False


def run_flask():
    """Run the Flask server."""
    app.run(host="127.0.0.1", port=PORT, debug=False)


def _refresh_track_info():
    """Refresh track information and update display."""
    logger.debug("Starting track info refresh")
    # Small delay to let Spotify update the state
    time.sleep(0.1)
    logger.debug("Post-action delay completed")

    # Update track information
    logger.debug("Getting track info for refresh")
    track_info = spotify_info.get_current_track_info()
    logger.debug("Track info retrieved for refresh")
    if "error" not in track_info and "no_track" not in track_info:
        spotify_info.track.last_info = track_info

        # Update shuffle state from current playback
        try:
            current_playback = spotify_info.sp.current_playback()
            if current_playback:
                spotify_info.track.shuffle_state = current_playback.get(
                    "shuffle_state", False
                )
        except:
            pass  # Keep existing shuffle state if update fails

        logger.debug("Creating status images after refresh")
        spotify_info.create_status_images(track_info)
        spotify_info.single_dial.create_single_dial_image(track_info)
        logger.debug("Images created successfully after refresh")
        return True
    elif "no_track" in track_info:
        # Handle no track case
        logger.debug("Creating no-track images")
        spotify_info.create_no_track_image()
        spotify_info.single_dial.create_single_dial_no_track_image()
        logger.debug("No-track images created")
        return True
    return False


def _check_premium():
    """Check if the user has a Spotify Premium account."""
    try:
        user = spotify_info.sp.current_user()
        return user["product"] == "premium"
    except:
        return False


def _handle_seek(seconds, ticks=1):
    """Handle seek forward/backward with optional ticks multiplier and buffering."""
    try:
        now = time.time()
        total_seek_ms = seconds * ticks * 1000

        # Refresh position from API if too much time has passed since last seek
        if (
            now - spotify_info.seek.last_seek_time > spotify_info.seek.refresh_delay
        ):
            current_playback = spotify_info.sp.current_playback()
            if not current_playback or not current_playback.get("is_playing"):
                return {"status": "error", "message": "No active playback"}, 400
            spotify_info.seek.current_position_ms = current_playback.get("progress_ms", 0)
            spotify_info.seek.track_duration_ms = current_playback["item"]["duration_ms"]
            spotify_info.seek.pending_seek_ms = 0  # Reset pending seeks after refresh
        # Initialize position if not set
        elif spotify_info.seek.current_position_ms is None:
            current_playback = spotify_info.sp.current_playback()
            if not current_playback or not current_playback.get("is_playing"):
                return {"status": "error", "message": "No active playback"}, 400
            spotify_info.seek.current_position_ms = current_playback.get("progress_ms", 0)
            spotify_info.seek.track_duration_ms = current_playback["item"]["duration_ms"]

        # Add to pending seek amount
        spotify_info.seek.pending_seek_ms += total_seek_ms
        
        # Calculate theoretical new position
        theoretical_position = spotify_info.seek.current_position_ms + spotify_info.seek.pending_seek_ms
        
        # Apply limits
        if theoretical_position >= spotify_info.seek.track_duration_ms - 1000:
            spotify_info.seek.pending_seek_ms = spotify_info.seek.track_duration_ms - 1000 - spotify_info.seek.current_position_ms
        elif theoretical_position < 0:
            spotify_info.seek.pending_seek_ms = -spotify_info.seek.current_position_ms

        spotify_info.seek.last_seek_time = now

        # Only make API call if enough time has passed
        if (
            now - spotify_info.seek.last_update >= spotify_info.seek.update_delay
        ):
            new_position_ms = spotify_info.seek.current_position_ms + spotify_info.seek.pending_seek_ms
            new_position_ms = max(0, min(new_position_ms, spotify_info.seek.track_duration_ms - 1000))
            
            spotify_info.sp.seek_track(new_position_ms)
            spotify_info.seek.last_update = now
            spotify_info.seek.current_position_ms = new_position_ms
            spotify_info.seek.pending_seek_ms = 0  # Reset pending after API call

        # Create appropriate message
        total_seconds = abs(seconds * ticks)
        direction = "forward" if seconds > 0 else "backward"
        
        if ticks > 1:
            message = f"Seeking {direction} {total_seconds} seconds ({abs(seconds)}s x {ticks})"
        else:
            message = f"Seeking {direction} {total_seconds} seconds"
            
        return {"status": "success", "message": message}, 200
        
    except spotipy.SpotifyException as e:
        logger.error(f"Error seeking: {str(e)}")
        return {"status": "error", "message": str(e)}, 500


@app.route("/player", methods=["POST"])
def handle_player_action():
    """Handle all player actions."""
    logger.debug("Player action request received")
    try:
        data = request.get_json()
        logger.debug(f"Player action data: {data}")
        if not data or "action" not in data:
            return jsonify({"status": "error", "message": "Invalid action data"}), 400

        action = data["action"]
        value = data.get("value")

        if action == "next":
            logger.debug("Executing next track action")
            spotify_info.sp.next_track()
            logger.debug("Next track API call completed")
            message = "Skipped to next track"
        elif action == "previous":
            logger.debug("Executing previous track action")
            spotify_info.sp.previous_track()
            logger.debug("Previous track API call completed")
            message = "Returned to previous track"
        elif action == "playpause":
            logger.debug("Executing play/pause action")
            current_playback = spotify_info.sp.current_playback()
            logger.debug("Got current playback for play/pause")
            current_playing_state = current_playback and current_playback.get(
                "is_playing"
            )

            if current_playing_state:
                logger.debug("Pausing playback")
                spotify_info.sp.pause_playback()
                logger.debug("Pause API call completed")
                message = "Paused playback"
            else:
                # Use specific device if not playing
                this_device_id = os.getenv("SPOTIFY_THIS_DEVICE")
                if this_device_id:
                    try:
                        logger.debug(f"Starting playback on device: {this_device_id}")
                        spotify_info.sp.start_playback(device_id=this_device_id)
                        logger.debug("Start playback API call completed")
                        message = "Started playback on specified device"
                    except spotipy.SpotifyException as e:
                        logger.debug(f"Device-specific playback failed: {str(e)}")
                        # Check if it's a "no active device" error
                        if hasattr(e, 'http_status') and e.http_status == 404 and "No active device found" in str(e):
                            logger.debug("No active device error detected, trying to activate a device")
                            device_id = spotify_info._try_activate_device()
                            if device_id:
                                try:
                                    spotify_info.sp.start_playback(device_id=device_id)
                                    message = "Started playback on activated device"
                    except spotipy.SpotifyException:
                                    # If still fails, try without device_id
                                    spotify_info.sp.start_playback()
                                    message = "Started playback"
                            else:
                                logger.warning("No devices available to activate")
                                raise e
                        else:
                        spotify_info.sp.start_playback()
                        logger.debug("Default start playback API call completed")
                        message = "Started playback"
                else:
                    try:
                    logger.debug("Starting playback (no specific device)")
                    spotify_info.sp.start_playback()
                    logger.debug("Start playback API call completed")
                    message = "Started playback"
                    except spotipy.SpotifyException as e:
                        # Check if it's a "no active device" error
                        if hasattr(e, 'http_status') and e.http_status == 404 and "No active device found" in str(e):
                            logger.debug("No active device error detected, trying to activate a device")
                            device_id = spotify_info._try_activate_device()
                            if device_id:
                                spotify_info.sp.start_playback(device_id=device_id)
                                message = "Started playback on activated device"
                            else:
                                logger.warning("No devices available to activate")
                                raise e
                        else:
                            raise e
        elif action == "togglelike":
            response = spotify_info._handle_like_toggle()
            return jsonify(response[0]), response[1]
        elif action == "toggleshuffle":
            logger.debug("Processing shuffle toggle action")
            # Vérifier si l'utilisateur a un compte Premium
            if not _check_premium():
                return jsonify(
                    {
                        "status": "error",
                        "message": "Shuffle control requires Spotify Premium",
                    }
                ), 403

            try:
                logger.debug("Getting current playback for shuffle toggle")
                current_playback = spotify_info.sp.current_playback()
                if not current_playback:
                    return jsonify(
                        {"status": "error", "message": "No active playback"}
                    ), 400

                current_shuffle = current_playback.get("shuffle_state", False)
                logger.debug(f"Toggling shuffle from {current_shuffle} to {not current_shuffle}")
                spotify_info.sp.shuffle(not current_shuffle)
                logger.debug("Shuffle toggle API call completed")
                message = "Shuffle " + ("disabled" if current_shuffle else "enabled")
            except spotipy.SpotifyException as e:
                if e.http_status == 403:
                    return jsonify(
                        {
                            "status": "error",
                            "message": "This device doesn't support shuffle control",
                        }
                    ), 403
                raise
        elif action == "volumeup":
            try:
                now = time.time()
                logger.debug("Processing volume up action")

                # Refresh volume from API if too much time has passed since last action
                if (
                    now - spotify_info.volume.last_rotate_time
                    > spotify_info.volume.refresh_delay
                ):
                    logger.debug("Refreshing volume from API for volume up")
                    current_playback = spotify_info.sp.current_playback()
                    logger.debug("Volume up refresh API call completed")
                    if not current_playback:
                        return jsonify(
                            {"status": "error", "message": "No active playback"}
                        ), 400
                    spotify_info.volume.current = current_playback["device"][
                        "volume_percent"
                    ]
                # Initialize current_volume if not set
                elif spotify_info.volume.current is None:
                    current_playback = spotify_info.sp.current_playback()
                    if not current_playback:
                        return jsonify(
                            {"status": "error", "message": "No active playback"}
                        ), 400
                    spotify_info.volume.current = current_playback["device"][
                        "volume_percent"
                    ]

                # Calculate new volume (+10)
                new_volume = min(100, spotify_info.volume.current + 10)
                spotify_info.volume.current = new_volume
                spotify_info.volume.last_rotate_time = now

                # Update last_unmuted_volume if volume > 0
                if new_volume > 0:
                    spotify_info.volume.last_unmuted_volume = new_volume

                # Only make API call if enough time has passed
                if (
                    now - spotify_info.volume.last_update
                    >= spotify_info.volume.update_delay
                ):
                    logger.debug(f"Setting volume up to {new_volume}%")
                    spotify_info.sp.volume(new_volume)
                    logger.debug("Volume up API call completed")
                    spotify_info.volume.last_update = now

                message = f"Volume increased to {new_volume}%"

            except spotipy.SpotifyException as e:
                logger.error(f"Error increasing volume: {str(e)}")
                return jsonify({"status": "error", "message": str(e)}), 500

        elif action == "volumedown":
            try:
                now = time.time()
                logger.debug("Processing volume down action")

                # Refresh volume from API if too much time has passed since last action
                if (
                    now - spotify_info.volume.last_rotate_time
                    > spotify_info.volume.refresh_delay
                ):
                    logger.debug("Refreshing volume from API for volume down")
                    current_playback = spotify_info.sp.current_playback()
                    logger.debug("Volume down refresh API call completed")
                    if not current_playback:
                        return jsonify(
                            {"status": "error", "message": "No active playback"}
                        ), 400
                    spotify_info.volume.current = current_playback["device"][
                        "volume_percent"
                    ]
                # Initialize current_volume if not set
                elif spotify_info.volume.current is None:
                    current_playback = spotify_info.sp.current_playback()
                    if not current_playback:
                        return jsonify(
                            {"status": "error", "message": "No active playback"}
                        ), 400
                    spotify_info.volume.current = current_playback["device"][
                        "volume_percent"
                    ]

                # Calculate new volume (-10)
                new_volume = max(0, spotify_info.volume.current - 10)
                spotify_info.volume.current = new_volume
                spotify_info.volume.last_rotate_time = now

                # Update last_unmuted_volume if volume > 0
                if new_volume > 0:
                    spotify_info.volume.last_unmuted_volume = new_volume

                # Only make API call if enough time has passed
                if (
                    now - spotify_info.volume.last_update
                    >= spotify_info.volume.update_delay
                ):
                    logger.debug(f"Setting volume down to {new_volume}%")
                    spotify_info.sp.volume(new_volume)
                    logger.debug("Volume down API call completed")
                    spotify_info.volume.last_update = now

                message = f"Volume decreased to {new_volume}%"

            except spotipy.SpotifyException as e:
                logger.error(f"Error decreasing volume: {str(e)}")
                return jsonify({"status": "error", "message": str(e)}), 500
        elif action == "volumemute":
            logger.debug("Processing volume mute action")
            current_playback = spotify_info.sp.current_playback()
            logger.debug("Got current playback for mute action")
            if not current_playback:
                return jsonify(
                    {"status": "error", "message": "No active playback"}
                ), 400

            current_volume = current_playback["device"]["volume_percent"]

            if current_volume > 0:
                # Si le volume n'est pas à 0, on sauvegarde le volume actuel et on mute
                logger.debug(f"Muting volume (was {current_volume}%)")
                spotify_info.volume.last_unmuted_volume = current_volume
                spotify_info.sp.volume(0)
                logger.debug("Mute API call completed")
                message = "Volume muted"
            else:
                # Si le volume est à 0, on restore le dernier volume
                restore_volume = spotify_info.volume.last_unmuted_volume
                logger.debug(f"Unmuting to volume {restore_volume}%")
                spotify_info.sp.volume(restore_volume)
                logger.debug("Unmute API call completed")
                message = f"Volume restored to {restore_volume}%"
        elif action == "volumeset":
            logger.debug(f"Processing volume set action to {value}%")
            if value is None:
                return jsonify(
                    {"status": "error", "message": "Volume value is required"}
                ), 400

            volume = max(0, min(100, value))
            logger.debug(f"Setting volume to {volume}%")
            spotify_info.sp.volume(volume)
            logger.debug("Volume set API call completed")
            if volume > 0:
                spotify_info.volume.last_unmuted_volume = volume
            message = f"Volume set to {volume}%"
        elif action == "startplaylist":
            playlist_uri = data.get("playlistUri")
            logger.debug(f"Processing start playlist action: {playlist_uri}")
            if not playlist_uri:
                return jsonify(
                    {"status": "error", "message": "Playlist URI is required"}
                ), 400

            logger.debug("Starting playlist playback")
            spotify_info.sp.start_playback(context_uri=playlist_uri)
            logger.debug("Start playlist API call completed")
            message = "Started playlist"
        elif action == "fastforward":
            ticks = data.get("ticks", 1)
            response = _handle_seek(5, ticks)
            if response[1] != 200:
                return jsonify(response[0]), response[1]
            message = response[0]["message"]
        elif action == "rewind":
            ticks = data.get("ticks", 1)
            response = _handle_seek(-5, ticks)
            if response[1] != 200:
                return jsonify(response[0]), response[1]
            message = response[0]["message"]
        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        # Refresh track information after any action
        logger.debug("Refreshing track info after player action")
        if not _refresh_track_info():
            logger.warning("Failed to refresh track info after player action")
            return jsonify(
                {"status": "error", "message": "Failed to refresh track info"}
            ), 500

        return jsonify({"status": "success", "message": message}), 200

    except spotipy.SpotifyException as e:
        logger.error(f"Spotify API error in player action {action}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        logger.error(f"Error handling player action {action}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


# Add new routes for button states
@app.route("/states", methods=["GET"])
def get_button_states():
    """Get current states of all buttons."""
    try:
        # Use cached states instead of making API calls
        button_states = {
            "is_playing": spotify_info.track.is_playing,
            "is_liked": spotify_info.track.current_liked,
            "is_shuffle": spotify_info.track.shuffle_state,  # Use cached shuffle state
            "is_muted": spotify_info.volume.current == 0
            if spotify_info.volume.current is not None
            else False,
        }

        return jsonify({"success": True, "states": button_states})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/devices", methods=["GET"])
def get_devices():
    """Get list of available Spotify devices."""
    try:
        devices_data = spotify_info.sp.devices()
        
        if not devices_data or not devices_data.get("devices"):
            return jsonify({
                "success": True, 
                "devices": [],
                "message": "No devices found"
            })
        
        # Format device information for JSON response
        formatted_devices = []
        for device in devices_data["devices"]:
            formatted_device = {
                "id": device["id"],
                "name": device["name"],
                "type": device["type"],
                "is_active": device["is_active"],
                "is_private_session": device["is_private_session"],
                "is_restricted": device["is_restricted"],
                "volume_percent": device["volume_percent"]
            }
            formatted_devices.append(formatted_device)
        
        # Find active device
        active_device = next(
            (device for device in formatted_devices if device["is_active"]),
            None
        )
        
        return jsonify({
            "success": True,
            "devices": formatted_devices,
            "active_device": active_device,
            "total_devices": len(formatted_devices)
        })
        
    except spotipy.SpotifyException as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Failed to get devices from Spotify API"
        }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Unexpected error occurred"
        }), 500


if __name__ == "__main__":
# debugpy.listen(5678)
    # logger.info(f"PID: {os.getpid()}")

    # Check if port is available before starting
    if not check_port_available(PORT):
        logger.error(f"Error: Port {PORT} is already in use. Please stop the existing service or change the port.")
        sys.exit(1)

    # Start Flask server in a separate thread
    logger.info(f"Starting Flask server on port {PORT}")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask thread started")

    IS_FIRST_RUN = True
    LAST_API_CALL = 0
    CURRENT_REFRESH_RATE = REFRESH_RATE_PLAYING
    NEEDS_LOGIN = True
    HAS_CREDENTIALS_ERROR = False

    # Create initial login message
    logger.info("Creating initial login images")
    spotify_info.image_handler.create_login_message_image()
    spotify_info.single_dial.create_single_dial_login_image()
    logger.info("Initial login images created")

    while True:
        current_time = time.time()
        logger.debug(f"Main loop iteration started at {current_time}")

        try:
            logger.debug("Entering try block")
            # Only try authentication if we don't have a credentials error
            if NEEDS_LOGIN and not HAS_CREDENTIALS_ERROR:
                logger.debug("Attempting Spotify authentication")
                spotify_info.sp.current_playback()
                logger.debug("Authentication API call completed")
                NEEDS_LOGIN = False
                logger.info("Successfully authenticated with Spotify")
            else:
                logger.debug(f"Skipping auth - NEEDS_LOGIN: {NEEDS_LOGIN}, HAS_CREDENTIALS_ERROR: {HAS_CREDENTIALS_ERROR}")

            # Only proceed with normal operation if logged in
            if not NEEDS_LOGIN and not HAS_CREDENTIALS_ERROR:
                logger.debug("Entering normal operation block")
                logger.debug(f"Time check: current={current_time:.2f}, last_api={LAST_API_CALL:.2f}, diff={current_time - LAST_API_CALL:.2f}, refresh_rate={CURRENT_REFRESH_RATE}")
                
                # Debug track end detection
                if (
                    spotify_info.track.is_playing
                    and spotify_info.image_handler.current_track_start_time
                ):
                    logger.debug(f"Track end check: start_time={spotify_info.image_handler.current_track_start_time}, duration={spotify_info.image_handler.current_track_duration}, elapsed={current_time - spotify_info.image_handler.current_track_start_time:.2f}")
                
                # Only check for track end if we have valid timing information
                if (
                    spotify_info.track.is_playing
                    and spotify_info.image_handler.current_track_start_time
                ):
                    if (
                        spotify_info.image_handler.current_track_start_time
                        and spotify_info.image_handler.current_track_duration
                        and current_time
                        - spotify_info.image_handler.current_track_start_time
                        >= spotify_info.image_handler.current_track_duration
                    ):
                        track_info = spotify_info.get_current_track_info()
                        LAST_API_CALL = current_time
                        if "error" not in track_info:
                            spotify_info.track.last_info = track_info
                            spotify_info.create_status_images(track_info)
                        continue

                # Only make API call if refresh delay has elapsed
                if current_time - LAST_API_CALL >= CURRENT_REFRESH_RATE or IS_FIRST_RUN:
                    logger.debug(f"Making API call - time since last: {current_time - LAST_API_CALL:.2f}s")
                    logger.debug("About to call get_current_track_info()")
                    current_track_info = spotify_info.get_current_track_info()
                    logger.debug("Main loop API call completed")
                    LAST_API_CALL = current_time

                    if "no_track" in current_track_info:
                        # No track currently playing - show pause layout
                        logger.debug("No track detected, creating no-track layout")
                        spotify_info.create_no_track_image()
                        spotify_info.single_dial.create_single_dial_no_track_image()
                        CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
                    elif "auth_error" in current_track_info:
                        # Authentication error - show login message
                        logger.error(f"\nAuthentication error: {current_track_info['auth_error']}")
                        spotify_info.image_handler.create_login_message_image()
                        spotify_info.single_dial.create_single_dial_login_image()
                        NEEDS_LOGIN = True
                        CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
                    elif "error" in current_track_info:
                        # Other errors - show error message
                        logger.error(f"\nError: {current_track_info['error']}")
                        spotify_info.image_handler.create_error_message_image(current_track_info['error'])
                        spotify_info.single_dial.create_single_dial_error_image(current_track_info['error'])
                        CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
                    else:
                        # Normal track playing - show full layout
                        logger.debug("Normal track detected, creating full layout")
                        spotify_info.track.last_info = current_track_info
                        spotify_info.create_status_images(current_track_info)
                        spotify_info.single_dial.create_single_dial_image(current_track_info)
                        CURRENT_REFRESH_RATE = (
                            REFRESH_RATE_PLAYING
                            if spotify_info.track.is_playing
                            else REFRESH_RATE_PAUSED
                        )

                # Update progress bar only if playback is active and no API call was made this iteration
                elif (
                    spotify_info.track.is_playing
                    and spotify_info.image_handler.current_track_start_time
                    and spotify_info.image_handler.current_track_duration
                    and spotify_info.track.last_info
                ):
                    elapsed_time = (
                        current_time
                        - spotify_info.image_handler.current_track_start_time
                    )
                    progress_ratio = min(
                        elapsed_time
                        / spotify_info.image_handler.current_track_duration,
                        1.0,
                    )
                    logger.debug(f"Updating progress bar, ratio: {progress_ratio:.3f}")
                    spotify_info.create_status_images(
                        spotify_info.track.last_info, override_progress=progress_ratio
                    )
                    spotify_info.single_dial.create_single_dial_image(
                        spotify_info.track.last_info, override_progress=progress_ratio
                    )
                else:
                    logger.debug(f"No progress update: is_playing={spotify_info.track.is_playing}, has_start_time={spotify_info.image_handler.current_track_start_time is not None}, has_duration={spotify_info.image_handler.current_track_duration is not None}, has_last_info={spotify_info.track.last_info is not None}")

                # Update button states at the refresh rate
                if current_time - LAST_API_CALL >= CURRENT_REFRESH_RATE or IS_FIRST_RUN:
                    logger.debug("Updating button states")
                    spotify_info.update_button_states()
                    logger.debug("Button states updated")
            else:
                logger.debug("Skipping normal operation - not authenticated or has credentials error")

            if IS_FIRST_RUN:
                logger.info("Status images updated")
                logger.info("Access the images at:")
                logger.info(f"http://127.0.0.1:{PORT}/left")
                logger.info(f"http://127.0.0.1:{PORT}/right")
                logger.info(f"http://127.0.0.1:{PORT}/single")
                IS_FIRST_RUN = False

        except spotipy.SpotifyOauthError as e:
            error_desc = str(e)
            if "invalid_client" in error_desc:
                logger.error("Error: Invalid Spotify credentials")
                spotify_info.image_handler.create_error_message_image(
                    "Invalid Spotify credentials"
                )
                spotify_info.single_dial.create_single_dial_error_image(
                    "Invalid Spotify credentials"
                )
                HAS_CREDENTIALS_ERROR = True
            else:
                logger.error(f"Authentication error: {error_desc}")
                spotify_info.image_handler.create_error_message_image(
                    "Authentication error"
                )
                spotify_info.single_dial.create_single_dial_error_image(
                    "Authentication error"
                )
                HAS_CREDENTIALS_ERROR = True

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify error: {str(e)}")
            spotify_info.image_handler.create_error_message_image(
                "Spotify error occurred"
            )
            spotify_info.single_dial.create_single_dial_error_image(
                "Spotify error occurred"
            )
            HAS_CREDENTIALS_ERROR = True

        except Exception as e:
            logger.error(f"Error in main loop: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Traceback: {traceback.format_exc()}")

        logger.debug("Main loop iteration completed, sleeping 1s")
        time.sleep(1)  # Always wait 1 second between iterations
