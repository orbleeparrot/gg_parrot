"""Validated account photos; public views only expose versioned image URLs."""
from __future__ import annotations

from contextlib import nullcontext
from io import BytesIO
import secrets
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import text as sql_text
from sqlmodel import Session, select

from .db import User, UserAvatar, get_session

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
AVATAR_SIZE = 256


def normalize_image(data: bytes) -> bytes:
    """Decode raster content, center-crop, and encode without source metadata."""
    if not data:
        raise ValueError("프로필 사진을 선택해 주세요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("프로필 사진은 2MB 이하만 올릴 수 있어요.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("JPG, PNG, WebP 사진만 올릴 수 있어요.")
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    raise ValueError("프로필 사진은 1,600만 화소 이하로 올려 주세요.")
                original.verify()
            with Image.open(BytesIO(data)) as original:
                oriented = ImageOps.exif_transpose(original)
                cropped = ImageOps.fit(
                    oriented.convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
                # A fresh canvas drops EXIF, ICC, comments, and animation frames.
                clean = Image.new("RGBA", cropped.size)
                clean.paste(cropped)
                output = BytesIO()
                clean.save(output, format="WEBP", quality=88, method=4)
                return output.getvalue()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("프로필 사진의 해상도가 너무 커요.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError):
        raise ValueError("사진을 읽을 수 없어요. JPG, PNG, WebP 파일을 확인해 주세요.") from None


def public_url(user_id: int | None, version: str | None) -> str | None:
    return f"/api/avatars/{user_id}?v={version}" if user_id is not None and version else None


def avatar_url(user_id: int | None, db: Session | None = None) -> str | None:
    if user_id is None:
        return None
    scope = nullcontext(db) if db is not None else get_session()
    with scope as session:
        version = session.exec(select(UserAvatar.version).where(UserAvatar.user_id == user_id)).first()
        return public_url(user_id, version)


def set_avatar(user_id: int, image_data: bytes | None) -> None:
    """Serialize replacements/deletions by the authenticated owner's row."""
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.exec(sql_text("BEGIN IMMEDIATE"))
        user = db.exec(select(User).where(User.id == user_id).with_for_update()).first()
        if user is None or user.is_deleted:
            raise LookupError("계정을 찾을 수 없어요.")
        set_avatar_in_session(db, user_id, image_data)
        db.commit()


def set_avatar_in_session(db: Session, user_id: int, image_data: bytes | None) -> None:
    """Stage a photo change in the caller's locked account transaction."""
    current = db.get(UserAvatar, user_id)
    if image_data is None:
        if current is not None:
            db.delete(current)
    else:
        version = secrets.token_hex(16)
        if current is None:
            current = UserAvatar(user_id=user_id, version=version, image_data=image_data)
        else:
            current.version = version
            current.image_data = image_data
        db.add(current)


def get_avatar(user_id: int, version: str | None = None) -> UserAvatar | None:
    with get_session() as db:
        query = select(UserAvatar).where(UserAvatar.user_id == user_id)
        if version is not None:
            query = query.where(UserAvatar.version == version)
        return db.exec(query).first()
