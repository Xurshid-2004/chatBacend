from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

AVATAR_SIZE = 512
ALLOWED_FORMATS = {'JPEG', 'MPO', 'PNG', 'WEBP', 'GIF'}
MAX_PIXELS = 40_000_000


def process_avatar(uploaded):
    """
    Validate an uploaded avatar and return it as a 512x512 WebP file.

    Re-encoding applies the camera rotation, crops to a square and drops all
    metadata (EXIF, including GPS location) from the original photo.
    """
    if uploaded.size > settings.AVATAR_MAX_UPLOAD_SIZE:
        limit_mb = settings.AVATAR_MAX_UPLOAD_SIZE // (1024 * 1024)
        raise ValidationError(f'Avatar must be smaller than {limit_mb} MB.')

    try:
        uploaded.seek(0)
        with Image.open(uploaded) as image:
            if image.format not in ALLOWED_FORMATS:
                raise ValidationError('Avatar must be a JPEG, PNG, WebP or GIF image.')
            if image.width * image.height > MAX_PIXELS:
                raise ValidationError('Avatar resolution is too large.')

            image = ImageOps.exif_transpose(image)
            if image.mode in ('RGBA', 'LA', 'P'):
                rgba = image.convert('RGBA')
                image = Image.new('RGB', rgba.size, (255, 255, 255))
                image.paste(rgba, mask=rgba.getchannel('A'))
            else:
                image = image.convert('RGB')

            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format='WEBP', quality=85, method=6)
    except ValidationError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ValidationError('Upload a valid image file.') from exc

    return ContentFile(buffer.getvalue(), name='avatar.webp')
