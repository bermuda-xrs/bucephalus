from __future__ import annotations

import base64
import binascii
from pathlib import Path

from ..paths import user_data_dir

MAX_BYTES = 8 * 2**20


class NotBase64(ValueError):
    pass


class TooLarge(ValueError):
    pass


class NotAnImage(ValueError):
    pass


def kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _chosen() -> Path:
    return user_data_dir() / "console" / "background"


def _read(p: Path) -> tuple[bytes, str] | None:
    try:
        if p.stat().st_size > MAX_BYTES:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    media = kind(data)
    return (data, media) if media else None


def current(ctx) -> tuple[bytes, str, str] | None:
    got = _read(_chosen())
    if got:
        return (*got, "chosen")
    configured = ctx.config.ui.background_image
    got = _read(ctx.config.resolve(configured)) if configured else None
    return (*got, "config") if got else None


def save(content_b64: str) -> dict:
    try:
        data = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise NotBase64("not base64") from None
    if len(data) > MAX_BYTES:
        raise TooLarge(f"larger than {MAX_BYTES // 2**20} MB")
    media = kind(data)
    if not media:
        raise NotAnImage("only PNG, JPEG and WebP images")
    p = _chosen()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".new")
    tmp.write_bytes(data)
    tmp.replace(p)
    return {"bytes": len(data), "type": media}


def clear() -> None:
    _chosen().unlink(missing_ok=True)
