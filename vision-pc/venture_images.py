"""Small, authenticated display previews; originals are retained unchanged."""
import io
from pathlib import Path
import threading

from flask import send_file
from PIL import Image, ImageOps, UnidentifiedImageError

PREVIEW_SLOTS = threading.BoundedSemaphore(2)


def preview(file, size, Error):
    if size not in ('thumb', 'full'):
        raise Error('Unknown image preview size.', 400)
    path = Path(file['path'])
    if path.stat().st_size > 16 * 1024 * 1024:
        raise Error('This image is too large to preview. Download the original.', 413)
    if not PREVIEW_SLOTS.acquire(blocking=False):
        raise Error('Image previews are busy. Try again shortly.', 503)
    try:
        with Image.open(path) as source:
            if source.width * source.height > 32_000_000:
                raise Error('This image is too large to preview. Download the original.', 413)
            source.seek(0)
            picture = ImageOps.exif_transpose(source).convert('RGB')
            if size == 'thumb':
                picture = ImageOps.fit(picture, (320, 320), method=Image.Resampling.LANCZOS)
            else:
                picture.thumbnail((4096, 4096))
            buffer = io.BytesIO()
            picture.save(buffer, 'JPEG', quality=85 if size == 'thumb' else 92)
            if buffer.tell() > 8 * 1024 * 1024:
                raise Error('This preview is too large. Download the original.', 413)
            buffer.seek(0)
        response = send_file(buffer, mimetype='image/jpeg', max_age=0)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise Error('This file cannot be previewed as an image. Download the original.', 415)
    finally:
        PREVIEW_SLOTS.release()
