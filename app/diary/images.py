"""Photo intake: validate, honour camera rotation, shrink, and strip all metadata (EXIF, including GPS)."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE_PX = 1024  # enough detail for leaf symptoms; small for slow rural networks and model input
Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse decompression bombs


class PhotoError(ValueError):
    """The upload is not a usable photo."""


def prepare_photo(raw: bytes) -> tuple[bytes, int, int]:
    """Return (jpeg_bytes, width, height) ready to store and send to the vision model."""
    if not raw:
        raise PhotoError("empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PhotoError("photo is larger than 10 MB")
    try:
        with Image.open(BytesIO(raw)) as source:
            img = ImageOps.exif_transpose(source)  # apply the phone's rotation before metadata is dropped
            img = img.convert("RGB")
            img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
            out = BytesIO()
            img.save(out, "JPEG", quality=82, optimize=True)  # re-encoding writes no EXIF: location data is gone
            return out.getvalue(), img.width, img.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PhotoError("unsupported or damaged image (use a JPEG, PNG or WebP photo)") from exc
