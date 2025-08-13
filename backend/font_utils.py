"""Font utilities for Unicode support across the application."""

import os
import platform
import logging
from PIL import ImageFont

logger = logging.getLogger(__name__)

font_found = False
last_font_path = None

def get_unicode_font(size):
    """Get a font that supports Unicode characters including Japanese, Chinese, etc."""
    global font_found, last_font_path
    if font_found:
        try:
            return ImageFont.truetype(last_font_path, size)
        except (OSError, IOError):
            logger.error(f"Failed to load font {last_font_path}: {str(e)}")
            pass
            
    system = platform.system()
    
    # List of fonts to try in order of preference
    font_paths = []
    
    if system == "Windows":
        font_paths = [
            "C:/Windows/Fonts/yugothm.ttc",     # Yu Gothic Medium - supports Japanese
            "C:/Windows/Fonts/NotoSans-Regular.ttf",  # Noto Sans if installed
            "C:/Windows/Fonts/meiryo.ttc",      # Meiryo - supports Japanese  
            "C:/Windows/Fonts/msgothic.ttc",    # MS Gothic - supports Japanese
            "C:/Windows/Fonts/arial.ttf",       # Fallback to arial
        ]
    elif system == "Darwin":  # macOS
        font_paths = [
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/PingFang.ttc", 
            "/Library/Fonts/Arial Unicode MS.ttf",
            "/System/Library/Fonts/Arial.ttf",
        ]
    elif system == "Linux":
        font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/arial.ttf",
        ]
    
    # Try each font path
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                logger.debug(f"Loading font: {font_path}")
                font_found = True   
                last_font_path = font_path
                return ImageFont.truetype(font_path, size)
        except (OSError, IOError) as e:
            logger.error(f"Failed to load font {font_path}: {str(e)}")
            continue
    
    # If no specific font works, try system default fonts by name
    font_names = ["Arial Unicode MS", "Noto Sans", "DejaVu Sans", "Liberation Sans"]
    for font_name in font_names:
        try:
            logger.debug(f"Trying font by name: {font_name}")
            font_found = True
            last_font_path = font_name
            return ImageFont.truetype(font_name, size)
        except (OSError, IOError):
            continue
    
    # Final fallback to PIL default font
    logger.warning("Using PIL default font - Unicode characters may not display correctly")
    return ImageFont.load_default()
