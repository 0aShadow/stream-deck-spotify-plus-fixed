"""Backend server for Spotify integration with Stream Deck."""

import time
import logging
import threading
from threading import Lock
from io import BytesIO
import os
import socket
import sys
from dotenv import load_dotenv

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from PIL import Image, ImageDraw, ImageFont
import requests
from flask import Flask, send_file, request, jsonify
# import cairosvg
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Constants
PORT = 8491
DISABLE_FLASK_LOGS = True
REFRESH_RATE_TRACK_END = 15
REFRESH_RATE_PLAYING = 15
REFRESH_RATE_PAUSED = 60

# Configure logging for debugging
# logging.basicConfig(level=logging.DEBUG)

# logger = logging.getLogger("spotipy")
# logger.setLevel(logging.DEBUG)

# pil_logger = logging.getLogger("PIL")
# pil_logger.setLevel(logging.INFO)


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
        self.single_image = None
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
            print(f"Warning: Heart icon file not found: {icon_path}")
        except Exception as e:
            print(f"Error loading heart icon: {str(e)}")

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
        """Add album art to the background image with caching."""
        image_url = track_data["image_url"]

        # Use cached image if URL hasn't changed
        if image_url == self.current_image_url and image_url in self.album_art_cache:
            album_art = self.album_art_cache[image_url]
        else:
            # Download and cache new image
            response = requests.get(image_url, timeout=10)
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

    def create_login_message_image(self):
        """Create an image showing login message."""
        background = Image.new("RGB", (400, 100), "black")
        draw = ImageDraw.Draw(background)

        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except OSError:
            font = ImageFont.load_default()

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

        try:
            title_font = ImageFont.truetype("arial.ttf", 20)
            desc_font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            title_font = ImageFont.load_default()
            desc_font = ImageFont.load_default()

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
        self.track = TrackState()
        self.volume = VolumeState()

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
            print(f"Error creating images: {str(e)}")
            return False

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
                self.track.is_playing = current_track["is_playing"]
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
                print(error_msg)
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

        try:
            title_font = ImageFont.truetype("arial.ttf", 20)
            artist_font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            title_font = ImageFont.load_default()
            artist_font = ImageFont.load_default()

        # Add placeholder text
        draw.text((120, 15), "No track playing", fill="white", font=title_font)
        draw.text((120, 45), "Start Spotify to play music", fill="#B3B3B3", font=artist_font)

        # Draw progress bar background (empty)
        draw.rounded_rectangle([120, 75, 340, 80], radius=1, fill="#404040")

        # Add heart icon (not liked state)
        self.image_handler.add_heart_icon(background, False)

        # Save images
        self.image_handler.save_images(background)

    def create_single_dial_image(self, current_track_info, override_progress=None):
        """Create a single dial image with all track information."""
        try:
            # Create rectangular image for single dial (200x100)
            background = Image.new("RGB", (200, 100), "black")
            draw = ImageDraw.Draw(background)

            # Add album cover (50x50 in top right)
            self._add_single_album_cover(background, current_track_info)

            # Add track info and progress
            self._add_single_dial_track_info(draw, current_track_info, override_progress)

            # Check if track changed and update liked status if needed
            track_id = current_track_info["track_id"]
            if track_id != self.track.current_id:
                self.track.current_id = track_id
                self.track.current_liked = self.sp.current_user_saved_tracks_contains(
                    [track_id]
                )[0]

            # Add heart icon
            self._add_heart_icon_single(background, self.track.current_liked)

            # Add pause overlay if not playing
            if not current_track_info.get("is_playing", True):
                self._add_pause_overlay_single(background)

            # Save single image
            with self.image_handler.image_lock:
                self.image_handler.single_image = BytesIO()
                background = background.convert("RGB")
                background.save(self.image_handler.single_image, format="JPEG", quality=100)
                self.image_handler.single_image.seek(0)

            return True

        except (requests.RequestException, IOError, ValueError) as e:
            print(f"Error creating single dial image: {str(e)}")
            return False

    def _add_single_dial_track_info(self, draw, track_data, override_progress):
        """Add track info optimized for single dial display."""
        try:
            title_font = ImageFont.truetype("arial.ttf", 16)
            artist_font = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            title_font = ImageFont.load_default()
            artist_font = ImageFont.load_default()

        # Track name (split into 2 lines if needed, avoid cover area)
        track_name = track_data["track_name"]
        self._draw_track_name_multiline(draw, track_name, title_font, 140)

        # Artists (after track name, full width as it's below cover)
        artists = self._truncate_text(track_data["artists"], artist_font, 180)
        draw.text((10, 55), artists, fill="#B3B3B3", font=artist_font)

        # Progress bar (bottom)
        current_progress = self._get_progress_single(override_progress, track_data)
        self._create_single_progress_bar(draw, current_progress)

    def _add_single_album_cover(self, background, track_data):
        """Add album cover to the top right corner (50x50)."""
        try:
            # Check if image_url exists in track_data
            if "image_url" not in track_data:
                print("No image_url in track_data")
                raise ValueError("No image_url available")
            
            image_url = track_data["image_url"]
            print(f"Loading album cover from: {image_url}")

            # Use cached image if URL hasn't changed
            if image_url == self.image_handler.current_image_url and image_url in self.image_handler.album_art_cache:
                album_art = self.image_handler.album_art_cache[image_url]
                print("Using cached album art")
            else:
                # Download and cache new image
                print("Downloading new album art...")
                response = requests.get(image_url, timeout=10)
                response.raise_for_status()  # Raise exception for bad status codes
                album_art = Image.open(BytesIO(response.content))
                # Update cache
                self.image_handler.album_art_cache = {image_url: album_art}  # Only keep latest image
                self.image_handler.current_image_url = image_url
                print("Successfully downloaded and cached album art")

            # Resize to 50x50 and place in top right
            album_art = album_art.resize((50, 50), Image.Resampling.LANCZOS)
            background.paste(album_art, (150, 0))
            print("Album art successfully pasted to image")

        except Exception as e:
            print(f"Error loading album cover for single dial: {str(e)}")
            print(f"Track data keys: {list(track_data.keys()) if track_data else 'No track_data'}")
            # Create placeholder if image fails
            placeholder = Image.new("RGB", (50, 50), "#1a1a1a")
            background.paste(placeholder, (150, 0))

    def _draw_track_name_multiline(self, draw, track_name, font, max_width):
        """Draw track name on multiple lines if needed."""
        # Check if the text fits on one line
        if font.getlength(track_name) <= max_width:
            draw.text((10, 8), track_name, fill="white", font=font)
            return
        
        # Split into words for wrapping
        words = track_name.split()
        lines = []
        current_line = []
        
        for word in words:
            current_line.append(word)
            test_line = " ".join(current_line)
            
            if font.getlength(test_line) > max_width:
                if len(current_line) > 1:
                    # Remove the word that made it too long
                    current_line.pop()
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    # Single word is too long, truncate it
                    lines.append(self._truncate_text(word, font, max_width))
                    current_line = []
        
        # Add remaining words
        if current_line:
            lines.append(" ".join(current_line))
        
        # Draw the lines (maximum 2 lines)
        for i, line in enumerate(lines[:2]):
            draw.text((10, 8 + i * 20), line, fill="white", font=font)

    def _create_single_progress_bar(self, draw, current_progress):
        """Create a progress bar for single dial."""
        # Background bar (moved to bottom)
        draw.rounded_rectangle([10, 80, 175, 83], radius=1, fill="#404040")
        
        # Progress bar
        if current_progress is not None:
            progress_width = int(165 * current_progress)
            draw.rounded_rectangle([10, 80, 10 + progress_width, 83], radius=1, fill="#1DB954")

    def _get_progress_single(self, override_progress, track_data):
        """Get progress for single dial."""
        if override_progress is not None:
            return override_progress
        
        if "progress_ms" in track_data and "duration_ms" in track_data:
            if track_data["duration_ms"] > 0:
                return track_data["progress_ms"] / track_data["duration_ms"]
        
        return None

    def _add_heart_icon_single(self, background, is_liked):
        """Add heart icon for single dial (next to progress bar)."""
        icon_filename = "spotify-liked.png" if is_liked else "spotify-like.png"
        icon_path = os.path.join(os.path.dirname(__file__), icon_filename)
        
        try:
            heart_image = Image.open(icon_path)
            heart_image = heart_image.resize((14, 14), Image.Resampling.LANCZOS)
            
            if heart_image.mode == 'RGBA':
                background.paste(heart_image, (180, 75), heart_image)
            else:
                background.paste(heart_image, (180, 75))
        except FileNotFoundError:
            print(f"Warning: Heart icon file not found: {icon_path}")
        except Exception as e:
            print(f"Error loading heart icon: {str(e)}")

    def _add_pause_overlay_single(self, background):
        """Add pause overlay for single dial."""
        overlay = Image.new("RGBA", (200, 100), (0, 0, 0, 64))
        draw_overlay = ImageDraw.Draw(overlay)

        # Pause bars centered in the left area (avoiding cover)
        bar_width = 6
        bar_height = 20
        spacing = 6
        start_x = (140 - (2 * bar_width + spacing)) // 2 + 10  # Center in text area
        start_y = (100 - bar_height) // 2

        for x in (start_x, start_x + bar_width + spacing):
            draw_overlay.rectangle(
                [x, start_y, x + bar_width, start_y + bar_height],
                fill="white",
            )

        background.paste(overlay, (0, 0), overlay)

    def _format_time(self, ms):
        """Format milliseconds to mm:ss format."""
        if ms is None:
            return "0:00"
        
        seconds = ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"

    def create_single_dial_login_image(self):
        """Create single dial login message."""
        background = Image.new("RGB", (200, 100), "black")
        draw = ImageDraw.Draw(background)

        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            font = ImageFont.load_default()

        # Multi-line text to fit better
        draw.text((10, 20), "Please Login", fill="white", font=font)
        draw.text((10, 45), "to Spotify", fill="white", font=font)

        with self.image_handler.image_lock:
            self.image_handler.single_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.image_handler.single_image, format="JPEG", quality=100)
            self.image_handler.single_image.seek(0)

    def create_single_dial_error_image(self, error_message):
        """Create single dial error message."""
        background = Image.new("RGB", (200, 100), "black")
        draw = ImageDraw.Draw(background)

        try:
            title_font = ImageFont.truetype("arial.ttf", 16)
            desc_font = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            title_font = ImageFont.load_default()
            desc_font = ImageFont.load_default()

        # Title
        draw.text((10, 20), "Error", fill="#FF0000", font=title_font)

        # Error message (truncated if needed, align left to avoid cover area)
        if len(error_message) > 20:
            error_message = error_message[:17] + "..."
        
        draw.text((10, 50), error_message, fill="white", font=desc_font)

        with self.image_handler.image_lock:
            self.image_handler.single_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.image_handler.single_image, format="JPEG", quality=100)
            self.image_handler.single_image.seek(0)

    def create_single_dial_no_track_image(self):
        """Create single dial no track message."""
        background = Image.new("RGB", (200, 100), "black")
        draw = ImageDraw.Draw(background)

        try:
            font = ImageFont.truetype("arial.ttf", 16)
            small_font = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        # Add placeholder cover (dark area)
        placeholder = Image.new("RGB", (50, 50), "#1a1a1a")
        background.paste(placeholder, (150, 0))

        # Main message (avoid cover area)
        main_text = "No track"
        draw.text((10, 15), main_text, fill="white", font=font)
        
        # Second line
        second_text = "playing"
        draw.text((10, 35), second_text, fill="white", font=font)

        # Sub message
        sub_text = "Start Spotify"
        draw.text((10, 60), sub_text, fill="#B3B3B3", font=small_font)

        # Add empty progress bar background
        draw.rounded_rectangle([10, 80, 175, 83], radius=1, fill="#404040")

        # Add pause overlay
        self._add_pause_overlay_single(background)

        # Add heart icon (not liked)
        self._add_heart_icon_single(background, False)

        with self.image_handler.image_lock:
            self.image_handler.single_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.image_handler.single_image, format="JPEG", quality=100)
            self.image_handler.single_image.seek(0)

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
            self.create_single_dial_image(current_track_info)

            return {
                "status": "succ/ess",
                "message": "Unliked track"
                if not self.track.current_liked
                else "Liked track",
            }, 200
        except (spotipy.SpotifyException, requests.RequestException) as e:
            print(f"Error toggling like status: {str(e)}")
            return {"status": "error", "message": str(e)}, 500

    def update_button_states(self):
        """Update all button states based on current playback."""
        try:
            current_playback = self.sp.current_playback()
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
            
            return True
        except Exception as e:
            print(f"Error updating button states: {str(e)}")
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
        if spotify_info.track.last_info:
            spotify_info.track.last_info["is_playing"] = not current_playing_state
            spotify_info.create_status_images(spotify_info.track.last_info)
            spotify_info.create_single_dial_image(spotify_info.track.last_info)
        spotify_info.track.is_playing = not current_playing_state

        # Send immediate response
        response = {"status": "success", "message": "Playback toggled"}
        threading.Thread(
            target=_async_playback_toggle_with_refresh,
            args=(current_playing_state,),
            daemon=True,
        ).start()

        return jsonify(response), 200

    except (ValueError, TypeError) as e:
        print(f"Error handling left action: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except spotipy.SpotifyException as e:
        print(f"Spotify API error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _async_playback_toggle_with_refresh(current_playing_state):
    """Handle the actual API call asynchronously and refresh track info."""
    try:
        if current_playing_state:
            spotify_info.sp.pause_playback()
        else:
            # Use specific device if not playing
            this_device_id = os.getenv("SPOTIFY_THIS_DEVICE")
            if this_device_id:
                try:
                    spotify_info.sp.start_playback(device_id=this_device_id)
                except spotipy.SpotifyException:
                    spotify_info.sp.start_playback()
            else:
                spotify_info.sp.start_playback()

        # Refresh track information
        _refresh_track_info()
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

        # Refresh track information after action (except for like toggle which handles its own refresh)
        if data["action"] not in ("tap", "dialDown"):
            if not _refresh_track_info():
                return jsonify(
                    {"status": "error", "message": "Failed to refresh track info"}
                ), 500

        return jsonify(response[0]), response[1]

    except (spotipy.SpotifyException, requests.RequestException) as e:
        print(f"Error handling right action: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _process_right_action(data):
    """Process right button action."""
    action = data["action"]

    if action in ("tap", "dialDown"):
        return spotify_info._handle_like_toggle()

    if action == "rotate":
        try:
            print(f"Rotate action received: {data}")
            now = time.time()
            value = data.get("value", 0) * 3

            # Refresh volume from API if too much time has passed since last rotation
            if (
                now - spotify_info.volume.last_rotate_time
                > spotify_info.volume.refresh_delay
            ):
                current_playback = spotify_info.sp.current_playback()
                if not current_playback:
                    return {"status": "error", "message": "No active playback"}, 400
                spotify_info.volume.current = current_playback["device"][
                    "volume_percent"
                ]
            # Initialize current_volume if not set
            elif spotify_info.volume.current is None:
                current_playback = spotify_info.sp.current_playback()
                if not current_playback:
                    return {"status": "error", "message": "No active playback"}, 400
                spotify_info.volume.current = current_playback["device"][
                    "volume_percent"
                ]

            # Calculate new volume
            new_volume = max(0, min(100, spotify_info.volume.current + value))
            spotify_info.volume.current = new_volume
            spotify_info.volume.last_rotate_time = now

            # Only make API call if enough time has passed
            if (
                now - spotify_info.volume.last_update
                >= spotify_info.volume.update_delay
            ):
                spotify_info.sp.volume(new_volume)
                spotify_info.volume.last_update = now

            return {"status": "success", "message": f"Volume set to {new_volume}%"}, 200

        except spotipy.SpotifyException as e:
            print(f"Error adjusting volume: {str(e)}")
            return {"status": "error", "message": str(e)}, 500

    return {"status": "error", "message": "Invalid action"}, 400


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


@app.route("/single", methods=["GET"])
def serve_single():
    """Serve the single dial image."""
    try:
        with spotify_info.image_handler.image_lock:
            if spotify_info.image_handler.single_image:
                img_copy = BytesIO(spotify_info.image_handler.single_image.getvalue())
                return send_file(img_copy, mimetype="image/jpeg")
        return "Image not found", 404
    except IOError as e:
        print(f"Error serving single dial image: {str(e)}")
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
        if now - spotify_info.track.last_track_change_time < 0.5:
            return {"status": "ignored", "message": "Action ignored (too soon)"}, 200

        # If direction changed or more than 2 seconds passed, allow action
        if (
            spotify_info.track.last_track_change_direction is None
            or now - spotify_info.track.last_track_change_time > 2.0
            or (value > 0) != (spotify_info.track.last_track_change_direction > 0)
        ):
            spotify_info.track.last_track_change_time = now
            spotify_info.track.last_track_change_direction = value

            if value > 0:
                spotify_info.sp.next_track()
                print("Skipped to next track")
                message = "Skipped to next track"
            else:
                spotify_info.sp.previous_track()
                print("Returned to previous track")
                message = "Returned to previous track"

            # Refresh track information
            if not _refresh_track_info():
                return {
                    "status": "error",
                    "message": "Failed to refresh track info",
                }, 500

            return {"status": "success", "message": message}, 200

        return {"status": "ignored", "message": "Action ignored"}, 200

    except spotipy.SpotifyException as e:
        print(f"Error changing track: {str(e)}")
        return {"status": "error", "message": str(e)}, 500


def _refresh_track_info():
    """Refresh track information and update display."""
    # Small delay to let Spotify update the state
    time.sleep(0.1)

    # Update track information
    track_info = spotify_info.get_current_track_info()
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

        spotify_info.create_status_images(track_info)
        spotify_info.create_single_dial_image(track_info)
        return True
    elif "no_track" in track_info:
        # Handle no track case
        spotify_info.create_no_track_image()
        spotify_info.create_single_dial_no_track_image()
        return True
    return False


def _check_premium():
    """Check if the user has a Spotify Premium account."""
    try:
        user = spotify_info.sp.current_user()
        return user["product"] == "premium"
    except:
        return False


@app.route("/player", methods=["POST"])
def handle_player_action():
    """Handle all player actions."""
    try:
        data = request.get_json()
        if not data or "action" not in data:
            return jsonify({"status": "error", "message": "Invalid action data"}), 400

        action = data["action"]
        value = data.get("value")

        if action == "next":
            spotify_info.sp.next_track()
            message = "Skipped to next track"
        elif action == "previous":
            spotify_info.sp.previous_track()
            message = "Returned to previous track"
        elif action == "playpause":
            current_playback = spotify_info.sp.current_playback()
            current_playing_state = current_playback and current_playback.get(
                "is_playing"
            )

            if current_playing_state:
                spotify_info.sp.pause_playback()
                message = "Paused playback"
            else:
                # Use specific device if not playing
                this_device_id = os.getenv("SPOTIFY_THIS_DEVICE")
                if this_device_id:
                    try:
                        spotify_info.sp.start_playback(device_id=this_device_id)
                        message = "Started playback on specified device"
                    except spotipy.SpotifyException:
                        spotify_info.sp.start_playback()
                        message = "Started playback"
                else:
                    spotify_info.sp.start_playback()
                    message = "Started playback"
        elif action == "togglelike":
            response = spotify_info._handle_like_toggle()
            return jsonify(response[0]), response[1]
        elif action == "toggleshuffle":
            # Vérifier si l'utilisateur a un compte Premium
            if not _check_premium():
                return jsonify(
                    {
                        "status": "error",
                        "message": "Shuffle control requires Spotify Premium",
                    }
                ), 403

            try:
                current_playback = spotify_info.sp.current_playback()
                if not current_playback:
                    return jsonify(
                        {"status": "error", "message": "No active playback"}
                    ), 400

                current_shuffle = current_playback.get("shuffle_state", False)
                spotify_info.sp.shuffle(not current_shuffle)
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
            current_playback = spotify_info.sp.current_playback()
            if not current_playback:
                return jsonify(
                    {"status": "error", "message": "No active playback"}
                ), 400

            current_volume = current_playback["device"]["volume_percent"]
            new_volume = min(100, current_volume + 10)
            spotify_info.sp.volume(new_volume)
            spotify_info.volume.last_unmuted_volume = new_volume
            message = f"Volume increased to {new_volume}%"
        elif action == "volumedown":
            current_playback = spotify_info.sp.current_playback()
            if not current_playback:
                return jsonify(
                    {"status": "error", "message": "No active playback"}
                ), 400

            current_volume = current_playback["device"]["volume_percent"]
            new_volume = max(0, current_volume - 10)
            spotify_info.sp.volume(new_volume)
            spotify_info.volume.last_unmuted_volume = (
                new_volume
                if new_volume > 0
                else spotify_info.volume.last_unmuted_volume
            )
            message = f"Volume decreased to {new_volume}%"
        elif action == "volumemute":
            current_playback = spotify_info.sp.current_playback()
            if not current_playback:
                return jsonify(
                    {"status": "error", "message": "No active playback"}
                ), 400

            current_volume = current_playback["device"]["volume_percent"]

            if current_volume > 0:
                # Si le volume n'est pas à 0, on sauvegarde le volume actuel et on mute
                spotify_info.volume.last_unmuted_volume = current_volume
                spotify_info.sp.volume(0)
                message = "Volume muted"
            else:
                # Si le volume est à 0, on restore le dernier volume
                restore_volume = spotify_info.volume.last_unmuted_volume
                spotify_info.sp.volume(restore_volume)
                message = f"Volume restored to {restore_volume}%"
        elif action == "volumeset":
            if value is None:
                return jsonify(
                    {"status": "error", "message": "Volume value is required"}
                ), 400

            volume = max(0, min(100, value))
            spotify_info.sp.volume(volume)
            if volume > 0:
                spotify_info.volume.last_unmuted_volume = volume
            message = f"Volume set to {volume}%"
        elif action == "startplaylist":
            playlist_uri = data.get("playlistUri")
            if not playlist_uri:
                return jsonify(
                    {"status": "error", "message": "Playlist URI is required"}
                ), 400

            spotify_info.sp.start_playback(context_uri=playlist_uri)
            message = "Started playlist"
        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        # Refresh track information after any action
        if not _refresh_track_info():
            return jsonify(
                {"status": "error", "message": "Failed to refresh track info"}
            ), 500

        return jsonify({"status": "success", "message": message}), 200

    except spotipy.SpotifyException as e:
        print(f"Spotify API error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        print(f"Error handling player action: {str(e)}")
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
    # Check if port is available before starting
    if not check_port_available(PORT):
        print(f"Error: Port {PORT} is already in use. Please stop the existing service or change the port.")
        sys.exit(1)

    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    IS_FIRST_RUN = True
    LAST_API_CALL = 0
    CURRENT_REFRESH_RATE = REFRESH_RATE_PLAYING
    NEEDS_LOGIN = True
    HAS_CREDENTIALS_ERROR = False

    # Create initial login message
    spotify_info.image_handler.create_login_message_image()
    spotify_info.create_single_dial_login_image()

    while True:
        current_time = time.time()

        try:
            # Only try authentication if we don't have a credentials error
            if NEEDS_LOGIN and not HAS_CREDENTIALS_ERROR:
                spotify_info.sp.current_playback()
                NEEDS_LOGIN = False
                print("Successfully authenticated with Spotify")

            # Only proceed with normal operation if logged in
            if not NEEDS_LOGIN and not HAS_CREDENTIALS_ERROR:
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
                    current_track_info = spotify_info.get_current_track_info()
                    LAST_API_CALL = current_time

                    if "no_track" in current_track_info:
                        # No track currently playing - show pause layout
                        spotify_info.create_no_track_image()
                        spotify_info.create_single_dial_no_track_image()
                        CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
                    elif "auth_error" in current_track_info:
                        # Authentication error - show login message
                        print(f"\nAuthentication error: {current_track_info['auth_error']}")
                        spotify_info.image_handler.create_login_message_image()
                        spotify_info.create_single_dial_login_image()
                        NEEDS_LOGIN = True
                        CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
                    elif "error" in current_track_info:
                        # Other errors - show error message
                        print(f"\nError: {current_track_info['error']}")
                        spotify_info.image_handler.create_error_message_image(current_track_info['error'])
                        spotify_info.create_single_dial_error_image(current_track_info['error'])
                        CURRENT_REFRESH_RATE = REFRESH_RATE_PAUSED
                    else:
                        # Normal track playing - show full layout
                        spotify_info.track.last_info = current_track_info
                        spotify_info.create_status_images(current_track_info)
                        spotify_info.create_single_dial_image(current_track_info)
                        CURRENT_REFRESH_RATE = (
                            REFRESH_RATE_PLAYING
                            if spotify_info.track.is_playing
                            else REFRESH_RATE_PAUSED
                        )

                # Update progress bar only if playback is active
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
                    spotify_info.create_status_images(
                        spotify_info.track.last_info, override_progress=progress_ratio
                    )
                    spotify_info.create_single_dial_image(
                        spotify_info.track.last_info, override_progress=progress_ratio
                    )

                # Update button states at the refresh rate
                if current_time - LAST_API_CALL >= CURRENT_REFRESH_RATE or IS_FIRST_RUN:
                    spotify_info.update_button_states()

            if IS_FIRST_RUN:
                print("Status images updated")
                print("Access the images at:")
                print(f"http://127.0.0.1:{PORT}/left")
                print(f"http://127.0.0.1:{PORT}/right")
                print(f"http://127.0.0.1:{PORT}/single")
                IS_FIRST_RUN = False

        except spotipy.SpotifyOauthError as e:
            error_desc = str(e)
            if "invalid_client" in error_desc:
                print("Error: Invalid Spotify credentials")
                spotify_info.image_handler.create_error_message_image(
                    "Invalid Spotify credentials"
                )
                spotify_info.create_single_dial_error_image(
                    "Invalid Spotify credentials"
                )
                HAS_CREDENTIALS_ERROR = True
            else:
                print(f"Authentication error: {error_desc}")
                spotify_info.image_handler.create_error_message_image(
                    "Authentication error"
                )
                spotify_info.create_single_dial_error_image(
                    "Authentication error"
                )
                HAS_CREDENTIALS_ERROR = True

        except spotipy.SpotifyException as e:
            print(f"Spotify error: {str(e)}")
            spotify_info.image_handler.create_error_message_image(
                "Spotify error occurred"
            )
            spotify_info.create_single_dial_error_image(
                "Spotify error occurred"
            )
            HAS_CREDENTIALS_ERROR = True

        except Exception as e:
            print(f"Error in main loop: {str(e)}")

        time.sleep(1)  # Always wait 1 second between iterations
