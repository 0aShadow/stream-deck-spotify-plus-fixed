"""Single dial image generation for Spotify integration with Stream Deck."""

import time
import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import requests


class SingleDialImageHandler:
    """Handles single dial image generation for Stream Deck display."""

    def __init__(self, spotify_client, image_handler):
        self.spotify_client = spotify_client
        self.image_handler = image_handler
        self.single_image = None

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
            if track_id != self.spotify_client.track.current_id:
                self.spotify_client.track.current_id = track_id
                self.spotify_client.track.current_liked = self.spotify_client.sp.current_user_saved_tracks_contains(
                    [track_id]
                )[0]

            # Add heart icon
            self._add_heart_icon_single(background, self.spotify_client.track.current_liked)

            # Add pause overlay if not playing
            if not current_track_info.get("is_playing", True):
                self._add_pause_overlay_single(background)

            # Save single image
            with self.image_handler.image_lock:
                self.single_image = BytesIO()
                background = background.convert("RGB")
                background.save(self.single_image, format="JPEG", quality=100)
                self.single_image.seek(0)

            return True

        except (requests.RequestException, IOError, ValueError) as e:
            print(f"Error creating single dial image: {str(e)}")
            return False

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

    def _truncate_text(self, text, font, max_width):
        """Truncate text to fit within max_width."""
        if font.getlength(text) > max_width:
            while font.getlength(text + "...") > max_width:
                text = text[:-1]
            text += "..."
        return text

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
            self.single_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.single_image, format="JPEG", quality=100)
            self.single_image.seek(0)

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
            self.single_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.single_image, format="JPEG", quality=100)
            self.single_image.seek(0)

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
            self.single_image = BytesIO()
            background = background.convert("RGB")
            background.save(self.single_image, format="JPEG", quality=100)
            self.single_image.seek(0) 