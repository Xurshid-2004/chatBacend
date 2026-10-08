"""
Validation and processing of uploaded chat media.

Nothing the client says about a file is trusted: images are decoded and
re-encoded, audio/video containers are recognised from their first bytes.
"""

import base64
import os
from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_FORMATS = {'JPEG', 'MPO', 'PNG', 'WEBP', 'GIF'}
MAX_IMAGE_PIXELS = 50_000_000
IMAGE_MAX_SIDE = 2048
THUMBNAIL_MAX_SIDE = 720
PREVIEW_MAX_SIDE = 24

VIDEO_CONTAINERS = {
    'mp4': ('video/mp4', '.mp4'),
    'quicktime': ('video/quicktime', '.mov'),
    'webm': ('video/webm', '.webm'),
}
AUDIO_CONTAINERS = {
    'mp4': ('audio/mp4', '.m4a'),
    'webm': ('audio/webm', '.webm'),
    'ogg': ('audio/ogg', '.ogg'),
    'mpeg': ('audio/mpeg', '.mp3'),
    'aac': ('audio/aac', '.aac'),
    'wav': ('audio/wav', '.wav'),
    'flac': ('audio/flac', '.flac'),
}
_QUICKTIME_ATOMS = {b'moov', b'mdat', b'wide', b'free', b'skip'}
_FORBIDDEN_NAME_CHARS = set('<>:"/\\|?*')


@dataclass
class ProcessedMedia:
    content: object  # a Django File to store
    name: str  # name with the right extension (only the extension is used on disk)
    mime_type: str
    width: int | None = None
    height: int | None = None
    preview: str = ''


def clean_filename(name, default='file'):
    """A display/download name without paths or characters that break headers or filesystems."""
    name = os.path.basename((name or '').replace('\\', '/'))
    name = ''.join(ch for ch in name if ch.isprintable() and ch not in _FORBIDDEN_NAME_CHARS)
    name = name.strip(' .')
    if len(name) > 200:
        stem, extension = os.path.splitext(name)
        name = stem[: 200 - len(extension)] + extension
    return name or default


def with_extension(name, extension):
    return os.path.splitext(name)[0] + extension


def _make_preview(image):
    small = image.convert('RGB')
    small.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    buffer = BytesIO()
    small.save(buffer, format='WEBP', quality=40)
    return 'data:image/webp;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def _normalise_mode(image):
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        return image.convert('RGBA')
    return image.convert('RGB')


def process_image(uploaded, max_side=IMAGE_MAX_SIDE):
    """
    Decode and re-encode an image as WebP (max `max_side` px): applies the camera
    rotation and drops metadata such as GPS location. Animated GIF/WebP files
    are kept as they are so the animation survives.
    """
    try:
        uploaded.seek(0)
        with Image.open(uploaded) as image:
            image_format = image.format
            if image_format not in IMAGE_FORMATS:
                raise ValidationError('Unsupported image format. Use JPEG, PNG, WebP or GIF.')
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValidationError('Image resolution is too large.')

            if getattr(image, 'is_animated', False) and image_format in ('GIF', 'WEBP'):
                extension = '.gif' if image_format == 'GIF' else '.webp'
                preview = _make_preview(image)
                uploaded.seek(0)
                return ProcessedMedia(
                    content=uploaded,
                    name=f'image{extension}',
                    mime_type=f'image/{image_format.lower()}',
                    width=image.width,
                    height=image.height,
                    preview=preview,
                )

            image = _normalise_mode(ImageOps.exif_transpose(image))
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format='WEBP', quality=82, method=4)
            return ProcessedMedia(
                content=ContentFile(buffer.getvalue()),
                name='image.webp',
                mime_type='image/webp',
                width=image.width,
                height=image.height,
                preview=_make_preview(image),
            )
    except ValidationError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise ValidationError('Upload a valid image file.') from exc


def sniff_container(head):
    """Recognise common audio/video containers from the first bytes of a file."""
    if len(head) >= 12 and head[4:8] == b'ftyp':
        return 'quicktime' if head[8:12] == b'qt  ' else 'mp4'
    if len(head) >= 8 and head[4:8] in _QUICKTIME_ATOMS:
        return 'quicktime'
    if head.startswith(b'\x1a\x45\xdf\xa3'):  # EBML: WebM / Matroska
        return 'webm'
    if head.startswith(b'OggS'):
        return 'ogg'
    if head.startswith(b'RIFF') and head[8:12] == b'WAVE':
        return 'wav'
    if head.startswith(b'fLaC'):
        return 'flac'
    if head.startswith(b'ID3'):
        return 'mpeg'
    if len(head) >= 2 and head[0] == 0xFF:
        if head[1] & 0xF6 == 0xF0:  # ADTS frame (AAC)
            return 'aac'
        if head[1] & 0xE0 == 0xE0 and (head[1] >> 1) & 0x03:  # MPEG audio frame (MP3)
            return 'mpeg'
    return None


def detect_media(uploaded, kind):
    """Return (mime_type, extension) for a 'video' or 'audio' upload, or raise ValidationError."""
    uploaded.seek(0)
    head = uploaded.read(64)
    uploaded.seek(0)
    container = sniff_container(head)
    if kind == 'video':
        if container not in VIDEO_CONTAINERS:
            raise ValidationError('Unsupported video format. Use MP4, MOV or WebM.')
        return VIDEO_CONTAINERS[container]
    if container not in AUDIO_CONTAINERS:
        raise ValidationError('Unsupported audio format.')
    return AUDIO_CONTAINERS[container]
