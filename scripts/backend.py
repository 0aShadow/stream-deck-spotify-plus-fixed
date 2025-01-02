import spotipy
from spotipy.oauth2 import SpotifyOAuth
import time
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from flask import Flask, send_file, request, jsonify
import threading
from threading import Lock
import cairosvg
import logging

# Constants
PORT = 8491
DISABLE_FLASK_LOGS = True  # New constant to control Flask logging

app = Flask(__name__)

# Disable Flask access logs if DISABLE_FLASK_LOGS is True
if DISABLE_FLASK_LOGS:
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)


class SpotifyTrackInfo:
    def __init__(self):
        # Initialize Spotify client with necessary permissions
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id="",
                client_secret="",
                redirect_uri="http://localhost:8888/callback",
                scope="user-read-currently-playing user-read-playback-state user-library-read user-library-modify user-modify-playback-state",
            )
        )
        self.current_track_start_time = None
        self.current_track_duration = None
        self.last_track_info = None
        self.left_image = None
        self.right_image = None
        self.image_lock = Lock()  # Add lock for thread safety

    def create_status_images(self, track_info, override_progress=None):
        try:
            # Create a new image with a black background
            background = Image.new("RGB", (400, 100), "black")
            draw = ImageDraw.Draw(background)

            # Download and resize album art
            response = requests.get(track_info["image_url"])
            album_art = Image.open(BytesIO(response.content))
            album_art = album_art.resize((100, 100))

            # Paste album art on the left side
            background.paste(album_art, (0, 0))

            # Add pause overlay if track is not playing
            if not track_info.get("is_playing", True):
                # Create a semi-transparent dark overlay
                overlay = Image.new("RGBA", (100, 100), (0, 0, 0, 128))

                # Create pause icon
                draw_overlay = ImageDraw.Draw(overlay)
                # Draw two white rectangles for pause symbol
                bar_width = 10
                bar_height = 30
                spacing = 10
                start_x = (100 - (2 * bar_width + spacing)) // 2
                start_y = (100 - bar_height) // 2

                draw_overlay.rectangle(
                    [start_x, start_y, start_x + bar_width, start_y + bar_height],
                    fill="white",
                )
                draw_overlay.rectangle(
                    [
                        start_x + bar_width + spacing,
                        start_y,
                        start_x + 2 * bar_width + spacing,
                        start_y + bar_height,
                    ],
                    fill="white",
                )

                # Paste the overlay onto the background
                background.paste(overlay, (0, 0), overlay)

            # Load fonts
            try:
                title_font = ImageFont.truetype("arial.ttf", 20)
                artist_font = ImageFont.truetype("arial.ttf", 16)
            except:
                # Fallback to default font if arial is not available
                title_font = ImageFont.load_default()
                artist_font = ImageFont.load_default()

            # Add text with length checking and truncation
            # Track name (moved up from 20 to 15)
            track_name = track_info["track_name"]
            if (
                title_font.getlength(track_name) > 260
            ):  # Adjust value based on your needs
                while title_font.getlength(track_name + "...") > 260:
                    track_name = track_name[:-1]
                track_name += "..."

            draw.text((120, 15), track_name, fill="white", font=title_font)

            # Artists (moved up from 50 to 45)
            artists = track_info["artists"]
            if artist_font.getlength(artists) > 260:  # Adjust value based on your needs
                while artist_font.getlength(artists + "...") > 260:
                    artists = artists[:-1]
                artists += "..."

            draw.text((120, 45), artists, fill="#B3B3B3", font=artist_font)

            # Get current playback progress
            if override_progress is not None:
                progress_ratio = override_progress
            else:
                current_playback = self.sp.current_playback()
                if current_playback:
                    progress_ms = current_playback["progress_ms"]
                    total_ms = current_playback["item"]["duration_ms"]
                    progress_ratio = progress_ms / total_ms
                    # Store timing information
                    self.current_track_start_time = time.time() - (progress_ms / 1000)
                    self.current_track_duration = total_ms / 1000

            # Draw progress bar background with rounded corners (shorter width)
            draw.rounded_rectangle([120, 75, 340, 80], radius=1, fill="#404040")

            # Draw progress bar with rounded corners (adjust progress_width calculation)
            progress_width = int(220 * progress_ratio)
            draw.rounded_rectangle(
                [120, 75, 120 + progress_width, 80], radius=1, fill="#1DB954"
            )

            # Add heart icon
            is_liked = self.sp.current_user_saved_tracks_contains(
                [track_info["track_id"]]
            )[0]
            heart_color = "#1DB954" if is_liked else "#404040"

            # Load and colorize SVG
            with open("scripts/spotify-like.svg", "r") as file:
                svg_content = file.read()
                # Replace the color in SVG
                colored_svg = svg_content.replace(
                    "path d=", f'path fill="{heart_color}" d='
                )

            # Convert SVG to PNG
            png_data = cairosvg.svg2png(
                bytestring=colored_svg.encode("utf-8"),
                output_width=20,
                output_height=20,
            )
            heart_image = Image.open(BytesIO(png_data))

            # Paste the heart onto the main image
            background.paste(heart_image, (360, 65), heart_image)

            # Split the image into two parts
            left_half = background.crop((0, 0, 200, 100))
            right_half = background.crop((200, 0, 400, 100))

            # Instead of saving to files, store in memory
            with self.image_lock:  # Use lock when updating images
                self.left_image = BytesIO()
                self.right_image = BytesIO()

                # Convert to RGB mode to avoid BMP padding issues
                left_half = left_half.convert("RGB")
                right_half = right_half.convert("RGB")

                # Save as BMP with specific parameters
                left_half.save(self.left_image, format="JPEG", quality=100)
                right_half.save(self.right_image, format="JPEG", quality=100)
                self.left_image.seek(0)
                self.right_image.seek(0)
            return True

        except Exception as e:
            print(f"Error creating images: {str(e)}")
            return False

    def get_current_track_info(self):
        try:
            # Get the current playing track
            current_track = self.sp.current_user_playing_track()

            if current_track is not None and current_track["item"] is not None:
                # Add is_playing to the return dictionary
                return {
                    "track_name": current_track["item"]["name"],
                    "image_url": current_track["item"]["album"]["images"][0]["url"],
                    "artists": ", ".join(
                        [artist["name"] for artist in current_track["item"]["artists"]]
                    ),
                    "is_playing": current_track["is_playing"],
                    "progress_ms": current_track["progress_ms"],
                    "duration_ms": current_track["item"]["duration_ms"],
                    "track_id": current_track["item"]["id"],
                }

            return {"error": "No track currently playing"}

        except Exception as e:
            return {"error": f"Error occurred: {str(e)}"}

    def list_devices(self):
        """Get and print all available Spotify devices"""
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
            else:
                print("No available devices found")
                return []
        except Exception as e:
            print(f"Error getting devices: {str(e)}")
            return []


# Flask routes
@app.route("/left", methods=["POST"])
def handle_left_action():
    try:
        data = request.get_json()
        if data and "action" in data:
            action = data["action"]

            if action == "tap" or action == "dialDown":
                # D'abord vérifier l'état de lecture actuel
                current_playback = spotify_info.sp.current_playback()

                # Si la lecture est en cours, mettre en pause
                if current_playback and current_playback.get("is_playing"):
                    try:
                        spotify_info.sp.pause_playback()
                        return jsonify(
                            {"status": "success", "message": "Playback paused"}
                        ), 200
                    except Exception as e:
                        print(f"Error pausing: {str(e)}")

                # Si en pause ou pas de lecture, essayer d'abord un play simple
                else:
                    try:
                        spotify_info.sp.start_playback()
                        return jsonify(
                            {"status": "success", "message": "Playback started"}
                        ), 200
                    except Exception as first_error:
                        print(
                            f"Simple play failed, trying with device forcing: {str(first_error)}"
                        )

                        # Si le play simple échoue, essayer avec forçage d'appareil
                        try:
                            # Obtenir la liste des appareils disponibles
                            devices = spotify_info.sp.devices()
                            if not devices or not devices["devices"]:
                                return jsonify(
                                    {
                                        "status": "error",
                                        "message": "Aucun appareil Spotify trouvé",
                                    }
                                ), 400

                            # Trouver le premier appareil disponible
                            target_device = None
                            for device in devices["devices"]:
                                if device["is_active"]:
                                    target_device = device
                                    break

                            if not target_device:
                                target_device = devices["devices"][0]

                            # Forcer l'activation de l'appareil
                            spotify_info.sp.transfer_playback(
                                device_id=target_device["id"], force_play=True
                            )
                            time.sleep(2)  # Attendre que le transfert soit effectif

                            # Forcer la lecture avec l'appareil spécifié
                            spotify_info.sp.start_playback(
                                device_id=target_device["id"]
                            )

                            return jsonify(
                                {
                                    "status": "success",
                                    "message": f"Lecture forcée sur {target_device['name']}",
                                }
                            ), 200

                        except Exception as e:
                            return jsonify(
                                {
                                    "status": "error",
                                    "message": f"Erreur lors de la lecture forcée: {str(e)}",
                                }
                            ), 400

            return jsonify({"status": "error", "message": "No active playback"}), 400
        return jsonify({"status": "error", "message": "Invalid action data"}), 400
    except Exception as e:
        print(f"Error handling left action: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/right", methods=["POST"])
def handle_right_action():
    try:
        data = request.get_json()
        if data and "action" in data:
            action = data["action"]
            print(f"Received action for right image: {data}")

            if action == "tap":
                # Toggle like status
                track_info = spotify_info.last_track_info
                if track_info and "track_id" in track_info:
                    track_id = track_info["track_id"]
                    is_liked = spotify_info.sp.current_user_saved_tracks_contains(
                        [track_id]
                    )[0]

                    if is_liked:
                        spotify_info.sp.current_user_saved_tracks_delete([track_id])
                    else:
                        spotify_info.sp.current_user_saved_tracks_add([track_id])

                    spotify_info.create_status_images(track_info)
                    return jsonify(
                        {
                            "status": "success",
                            "message": "Unliked track" if is_liked else "Liked track",
                        }
                    ), 200

            elif action == "dialDown":
                # TODO: Handle dial down event
                # Could be used to enter a specific mode (e.g., playlist selection)
                return jsonify(
                    {"status": "success", "message": "Dial down received"}
                ), 200

            elif action == "dialUp":
                # TODO: Handle dial up event
                # Could be used to exit the mode entered with dial down
                return jsonify(
                    {"status": "success", "message": "Dial up received"}
                ), 200

            elif action == "rotate":
                # TODO: Handle rotate event
                # Could be used to scroll through playlists or adjust some other setting
                return jsonify({"status": "success", "message": "Rotate received"}), 200

            return jsonify(
                {"status": "error", "message": "No track currently playing"}
            ), 400
        return jsonify({"status": "error", "message": "Invalid action data"}), 400
    except Exception as e:
        print(f"Error handling right action: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/left", methods=["GET"])
def serve_left():
    try:
        with spotify_info.image_lock:
            if spotify_info.left_image:
                img_copy = BytesIO(spotify_info.left_image.getvalue())
                return send_file(img_copy, mimetype="image/bmp")
        return "Image not found", 404
    except Exception as e:
        print(f"Error serving left image: {str(e)}")
        return str(e), 500


@app.route("/right", methods=["GET"])
def serve_right():
    try:
        with spotify_info.image_lock:
            if spotify_info.right_image:
                img_copy = BytesIO(spotify_info.right_image.getvalue())
                return send_file(img_copy, mimetype="image/bmp")
        return "Image not found", 404
    except Exception as e:
        print(f"Error serving right image: {str(e)}")
        return str(e), 500


def run_flask():
    app.run(host="localhost", port=PORT)


if __name__ == "__main__":
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    spotify_info = SpotifyTrackInfo()
    first_run = True
    last_api_call = 0

    while True:
        current_time = time.time()

        # Check if track has ended
        if (
            spotify_info.current_track_start_time
            and spotify_info.current_track_duration
            and current_time - spotify_info.current_track_start_time
            >= spotify_info.current_track_duration
        ):
            # Force refresh when track ends
            track_info = spotify_info.get_current_track_info()
            last_api_call = current_time
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
                refresh_rate = 5
            # Normal playback
            elif is_playing:
                refresh_rate = 10
            # Paused or not playing
            else:
                refresh_rate = 30
        else:
            # Default refresh rate when no track info
            refresh_rate = 30

        # API call based on refresh rate
        if current_time - last_api_call >= refresh_rate or first_run:
            track_info = spotify_info.get_current_track_info()
            last_api_call = current_time

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
            spotify_info.current_track_start_time
            and spotify_info.current_track_duration
            and spotify_info.last_track_info
        ):
            elapsed_time = current_time - spotify_info.current_track_start_time
            progress_ratio = min(
                elapsed_time / spotify_info.current_track_duration, 1.0
            )
            spotify_info.create_status_images(
                spotify_info.last_track_info, override_progress=progress_ratio
            )

        if first_run:
            print("Status images updated")
            print("Access the images at:")
            print(f"http://localhost:{PORT}/left")
            print(f"http://localhost:{PORT}/right")
            first_run = False

        time.sleep(1)  # Update every second instead of every 10 seconds
