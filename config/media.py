"""
Serving stored files (avatars, chat attachments) safely and efficiently.

- Streams with an *async* iterator under ASGI: there Django would otherwise read
  a synchronous FileResponse completely into memory before sending it.
- Supports single byte ranges (seeking in video/audio), ETag / 304 and HEAD.
- Hardened headers: nosniff + a sandboxing CSP, so an uploaded HTML/SVG file
  can never run script on our origin.
- With MEDIA_ACCEL_REDIRECT_PREFIX set, nginx sends the bytes (X-Accel-Redirect).
"""

import asyncio
import os
import re

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import default_storage
from django.core.handlers.asgi import ASGIRequest
from django.http import Http404, HttpResponse, HttpResponseNotModified, StreamingHttpResponse
from django.utils.http import content_disposition_header

CHUNK_SIZE = 256 * 1024
_RANGE_RE = re.compile(r'^bytes=(\d*)-(\d*)$')


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header, size):
    """Return (start, end) for a single `bytes=` range, or None to send the whole file."""
    match = _RANGE_RE.match(header.strip()) if header else None
    if not match:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # suffix range: the last N bytes
        length = int(last)
        if length == 0:
            raise RangeNotSatisfiable
        return max(size - length, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


async def _read_chunks_async(path, start, length):
    handle = await asyncio.to_thread(open, path, 'rb')
    try:
        await asyncio.to_thread(handle.seek, start)
        remaining = length
        while remaining > 0:
            chunk = await asyncio.to_thread(handle.read, min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


def _read_chunks_sync(path, start, length):
    with open(path, 'rb') as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _read_chunks(request, path, start, length):
    # Each server streams best with its own kind of iterator.
    raw_request = getattr(request, '_request', request)  # unwrap a DRF Request
    if isinstance(raw_request, ASGIRequest):
        return _read_chunks_async(path, start, length)
    return _read_chunks_sync(path, start, length)


def serve_file(
    request,
    name,
    *,
    content_type,
    filename=None,
    as_attachment=False,
    cache_control='private, max-age=31536000, immutable',
):
    """Build a response for the stored file `name` (files never change once saved)."""
    if not name:
        raise Http404('File not found.')
    try:
        path = default_storage.path(name)
        size = os.path.getsize(path)
    except (OSError, SuspiciousFileOperation) as exc:
        raise Http404('File not found.') from exc

    etag = f'"{os.path.basename(path)}-{size}"'
    headers = {
        'Cache-Control': cache_control,
        'ETag': etag,
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox",
        'Cross-Origin-Resource-Policy': 'same-origin',
    }
    if filename or as_attachment:
        headers['Content-Disposition'] = content_disposition_header(as_attachment, filename or 'file')

    if request.headers.get('If-None-Match') == etag:
        response = HttpResponseNotModified()
        for name, value in headers.items():
            response[name] = value
        return response

    accel_prefix = getattr(settings, 'MEDIA_ACCEL_REDIRECT_PREFIX', '')
    if accel_prefix:
        response = HttpResponse(content_type=content_type, headers=headers)
        response['X-Accel-Redirect'] = accel_prefix.rstrip('/') + '/' + name
        return response

    headers['Accept-Ranges'] = 'bytes'
    try:
        byte_range = parse_range(request.headers.get('Range'), size)
    except RangeNotSatisfiable:
        response = HttpResponse(status=416, headers=headers)
        response['Content-Range'] = f'bytes */{size}'
        return response

    start, end = byte_range or (0, size - 1)
    length = end - start + 1 if size else 0
    if request.method == 'HEAD':
        response = HttpResponse(content_type=content_type, headers=headers)
    else:
        response = StreamingHttpResponse(
            _read_chunks(request, path, start, length), content_type=content_type, headers=headers
        )
    if byte_range:
        response.status_code = 206
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    response['Content-Length'] = str(length)
    return response
