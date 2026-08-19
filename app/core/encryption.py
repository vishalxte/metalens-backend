from __future__ import annotations

import base64
import json
import secrets
from functools import lru_cache
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.types import Text, TypeDecorator

from app.core.config import settings


FORMAT_VERSION = "v1"
CURRENT_KEY_VERSION = "1"
NONCE_BYTES = 12
AUTH_TAG_BYTES = 16


class EncryptionError(Exception):
    """Base exception for encryption/decryption failures."""


class EncryptionConfigurationError(EncryptionError):
    """Raised when an encryption key/version cannot be resolved safely."""


@lru_cache(maxsize=8)
def get_encryption_key(key_version: str = CURRENT_KEY_VERSION) -> bytes:
    """
    Centralized key lookup abstraction.

    Version 1 intentionally maps to APP_ENCRYPTION_KEY only. Future key
    rotation can add another version mapping here without changing any
    encrypt()/decrypt() call sites.
    """
    if key_version != CURRENT_KEY_VERSION:
        raise EncryptionConfigurationError(
            f"Unsupported encryption key version: {key_version}"
        )

    try:
        key = base64.b64decode(settings.APP_ENCRYPTION_KEY, validate=True)
    except Exception as exc:
        raise EncryptionConfigurationError(
            "APP_ENCRYPTION_KEY is not valid Base64"
        ) from exc

    if len(key) != 32:
        raise EncryptionConfigurationError(
            "APP_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )

    return key


@lru_cache(maxsize=8)
def _aesgcm(key_version: str = CURRENT_KEY_VERSION) -> AESGCM:
    return AESGCM(get_encryption_key(key_version))


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise EncryptionError(f"Invalid {label} encoding") from exc


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """
    Encrypt UTF-8 text with AES-256-GCM.

    Format:
        v1:<key_version>:<base64_nonce>:<base64_ciphertext_and_tag>

    A fresh cryptographically secure 96-bit nonce is generated for every
    encryption operation. Values that are already valid versioned
    ciphertexts are returned unchanged to protect against double
    encryption in migration/retry paths.
    """
    if plaintext is None:
        return None

    if not isinstance(plaintext, str):
        raise TypeError("encrypt() expects str or None")

    if plaintext.startswith(f"{FORMAT_VERSION}:"):
        # A reserved version prefix is never treated as ordinary plaintext.
        # Validate it before accepting it unchanged.
        decrypt(plaintext)
        return plaintext

    key_version = CURRENT_KEY_VERSION
    nonce = secrets.token_bytes(NONCE_BYTES)
    associated_data = f"{FORMAT_VERSION}:{key_version}".encode("ascii")

    ciphertext_and_tag = _aesgcm(key_version).encrypt(
        nonce,
        plaintext.encode("utf-8"),
        associated_data
    )

    return (
        f"{FORMAT_VERSION}:{key_version}:"
        f"{_b64encode(nonce)}:{_b64encode(ciphertext_and_tag)}"
    )


def decrypt(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt and authenticate one versioned AES-256-GCM ciphertext."""
    if ciphertext is None:
        return None

    if not isinstance(ciphertext, str):
        raise TypeError("decrypt() expects str or None")

    parts = ciphertext.split(":", 3)
    if len(parts) != 4 or parts[0] != FORMAT_VERSION:
        raise EncryptionError("Value is not a supported encrypted value")

    _, key_version, nonce_b64, ciphertext_b64 = parts

    nonce = _b64decode(nonce_b64, "nonce")
    ciphertext_and_tag = _b64decode(ciphertext_b64, "ciphertext")

    if len(nonce) != NONCE_BYTES:
        raise EncryptionError("Invalid encryption nonce length")

    if len(ciphertext_and_tag) < AUTH_TAG_BYTES:
        raise EncryptionError("Invalid encrypted payload")

    associated_data = f"{FORMAT_VERSION}:{key_version}".encode("ascii")

    try:
        plaintext = _aesgcm(key_version).decrypt(
            nonce,
            ciphertext_and_tag,
            associated_data
        )
    except InvalidTag as exc:
        raise EncryptionError(
            "Encrypted value failed authentication"
        ) from exc
    except Exception as exc:
        raise EncryptionError(
            "Encrypted value could not be decrypted"
        ) from exc

    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EncryptionError(
            "Decrypted value is not valid UTF-8"
        ) from exc


def decrypt_stored_value(value: Optional[str]) -> Optional[str]:
    """
    Read helper for the controlled migration window.

    Existing legacy plaintext is returned as-is. Once a value carries the
    reserved v1 prefix, authentication/decryption is mandatory and any
    tampering/failure raises EncryptionError instead of silently falling
    back to plaintext.
    """
    if value is None:
        return None

    if isinstance(value, str) and value.startswith(f"{FORMAT_VERSION}:"):
        return decrypt(value)

    return value


def is_encrypted(value: Optional[str]) -> bool:
    """Return True only for a valid, authenticated versioned ciphertext."""
    if not isinstance(value, str) or not value.startswith(f"{FORMAT_VERSION}:"):
        return False

    decrypt(value)
    return True


class EncryptedText(TypeDecorator[str]):
    """
    SQLAlchemy TEXT-backed field with transparent application-level
    AES-256-GCM encryption/decryption.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        return encrypt(value)

    def process_result_value(self, value: Optional[str], dialect) -> Optional[str]:
        return decrypt_stored_value(value)


class EncryptedJSON(TypeDecorator[Any]):
    """
    TEXT-backed encrypted JSON field.

    Python dict/list -> compact JSON -> AES-GCM -> TEXT
    TEXT ciphertext -> AES-GCM -> JSON -> Python dict/list
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Optional[str]:
        if value is None:
            return None

        if isinstance(value, str) and value.startswith(f"{FORMAT_VERSION}:"):
            decrypt(value)
            return value

        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":")
        )
        return encrypt(serialized)

    def process_result_value(self, value: Optional[str], dialect) -> Any:
        if value is None:
            return None

        plaintext = decrypt_stored_value(value)

        try:
            return json.loads(plaintext)
        except (TypeError, json.JSONDecodeError) as exc:
            raise EncryptionError(
                "Encrypted JSON value is not valid JSON"
            ) from exc
