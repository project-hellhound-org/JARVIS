# tests/test_geolocate_security.py
"""
Security validation tests for Image Geolocation endpoints & helpers:
- Path confinement to data/uploads/
- Directory traversal rejection (..)
- Symlink rejection
- Max 4 images restriction
- Max 10MB size restriction
- Pillow format verification (JPEG, PNG, WEBP only)
- UUID random filename generation
"""

import os
import io
import json
import base64
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

from modules.image_geolocator import (
    UPLOADS_DIR,
    MAX_IMAGE_SIZE_BYTES,
    ALLOWED_IMAGE_FORMATS,
    validate_safe_image_path,
    save_uploaded_image,
    ImageGeolocator
)
from frontend.desktop import JarvisAPI, setup_jarvis_bottle_routes
import bottle


def test_validate_safe_image_path_confinement():
    """Verify that path traversal and external paths are rejected."""
    # Empty or invalid input
    assert validate_safe_image_path("") is None
    assert validate_safe_image_path(None) is None
    assert validate_safe_image_path("/etc/passwd\0.jpg") is None

    # Path traversal attempts
    assert validate_safe_image_path("../../etc/passwd") is None
    assert validate_safe_image_path(str(UPLOADS_DIR / ".." / ".." / "etc" / "passwd")) is None

    # Outside UPLOADS_DIR
    assert validate_safe_image_path("/tmp/arbitrary_file.jpg") is None


def test_validate_safe_image_path_symlink_rejection(tmp_path):
    """Verify symlinks are strictly rejected even if inside uploads dir."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    real_outside_file = tmp_path / "secret.txt"
    real_outside_file.write_text("sensitive data")

    symlink_in_uploads = UPLOADS_DIR / "symlink_test.jpg"
    try:
        symlink_in_uploads.symlink_to(real_outside_file)
        assert validate_safe_image_path(str(symlink_in_uploads)) is None
    finally:
        if symlink_in_uploads.is_symlink() or symlink_in_uploads.exists():
            symlink_in_uploads.unlink(missing_ok=True)


def test_validate_safe_image_path_valid_file():
    """Verify valid file strictly inside UPLOADS_DIR is accepted."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    test_img = UPLOADS_DIR / "valid_test.png"
    img = Image.new("RGB", (10, 10), color="blue")
    img.save(test_img, "PNG")

    try:
        res = validate_safe_image_path(str(test_img))
        assert res is not None
        assert res.resolve() == test_img.resolve()
    finally:
        test_img.unlink(missing_ok=True)


def test_save_uploaded_image_size_limit():
    """Verify images over 10MB are rejected."""
    oversized = b"A" * (MAX_IMAGE_SIZE_BYTES + 1024)
    path, err = save_uploaded_image(oversized, "huge.jpg")
    assert path is None
    assert "exceeds 10MB limit" in err


def test_save_uploaded_image_format_validation():
    """Verify non-image and unsupported formats are rejected."""
    # Plain text / corrupted
    path, err = save_uploaded_image(b"not an image text content", "fake.jpg")
    assert path is None
    assert "Corrupted or invalid image" in err

    # Unsupported format (e.g., GIF or BMP)
    gif_buf = io.BytesIO()
    Image.new("RGB", (5, 5)).save(gif_buf, "GIF")
    path, err = save_uploaded_image(gif_buf.getvalue(), "anim.gif")
    assert path is None
    assert "Unsupported image format" in err


def test_save_uploaded_image_uuid_naming():
    """Verify valid images are saved with random UUID filenames."""
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color="red").save(buf, "JPEG")
    raw = buf.getvalue()

    path, err = save_uploaded_image(raw, "original_user_file.jpg")
    assert err is None
    assert path is not None
    assert path.exists()
    assert path.parent == UPLOADS_DIR
    assert "original_user_file" not in path.name
    # UUID hex is 32 characters + .jpg
    stem = path.stem
    assert len(stem) == 32
    path.unlink(missing_ok=True)


def test_upload_images_max_4_limit():
    """Verify upload endpoint / API rejects requests with > 4 images."""
    api = JarvisAPI()
    dummy_list = [{"data_url": "data:image/jpeg;base64,AAAA"} for _ in range(5)]
    res = api.upload_images_for_geolocation(dummy_list)
    assert res["success"] is False
    assert "Maximum 4 images allowed" in res["error"]


def test_bottle_copy_and_open_folder_traversal_rejection():
    """Verify Bottle routes reject traversal with HTTP 400."""
    app = bottle.Bottle()
    mock_api = MagicMock()
    setup_jarvis_bottle_routes(app, server_root_path="/tmp", api=mock_api)

    # Test copy_clipboard with traversal
    req_body = json.dumps({"image_path": "../../etc/shadow"}).encode("utf-8")
    env = {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/geolocate/copy_clipboard',
        'CONTENT_TYPE': 'application/json',
        'CONTENT_LENGTH': str(len(req_body)),
        'wsgi.input': io.BytesIO(req_body),
        'wsgi.errors': io.StringIO(),
    }
    resp = app(env, lambda s, h, e=None: None)
    res_json = json.loads(b"".join(resp).decode("utf-8"))
    assert res_json.get("success") is False
    assert "Invalid or unauthorized image path" in res_json.get("error", "")

    # Test open_folder with traversal
    req_body = json.dumps({"image_path": "/var/log/syslog"}).encode("utf-8")
    env = {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/geolocate/open_folder',
        'CONTENT_TYPE': 'application/json',
        'CONTENT_LENGTH': str(len(req_body)),
        'wsgi.input': io.BytesIO(req_body),
        'wsgi.errors': io.StringIO(),
    }
    resp = app(env, lambda s, h, e=None: None)
    res_json = json.loads(b"".join(resp).decode("utf-8"))
    assert res_json.get("success") is False
    assert "Invalid or unauthorized image path" in res_json.get("error", "")
