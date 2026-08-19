import base64

import pytest

from app.core.encryption import EncryptionError, encrypt, decrypt, EncryptedJSON, EncryptedText


def test_round_trip_unicode_and_empty():
    for value in ("", "hello", "मराठी मजकूर 🔐", "x" * 10000):
        ciphertext = encrypt(value)
        assert ciphertext.startswith("v1:1:")
        assert ciphertext != value
        assert decrypt(ciphertext) == value


def test_nonce_is_random():
    first = encrypt("same")
    second = encrypt("same")
    assert first != second
    assert decrypt(first) == decrypt(second) == "same"


def test_tamper_is_rejected():
    ciphertext = encrypt("secret")
    parts = ciphertext.split(":")
    payload = bytearray(base64.b64decode(parts[3]))
    payload[-1] ^= 1
    parts[3] = base64.b64encode(bytes(payload)).decode("ascii")
    with pytest.raises(EncryptionError):
        decrypt(":".join(parts))


def test_type_decorators_round_trip():
    text_type = EncryptedText()
    json_type = EncryptedJSON()
    text_value = text_type.process_bind_param("hello", None)
    assert "hello" not in text_value
    assert text_type.process_result_value(text_value, None) == "hello"
    payload = [{"filename": "a.pdf", "excerpt": "secret"}]
    json_value = json_type.process_bind_param(payload, None)
    assert "a.pdf" not in json_value
    assert json_type.process_result_value(json_value, None) == payload


def test_plaintext_legacy_value_is_read_during_migration_window():
    from app.core.encryption import decrypt_stored_value
    assert decrypt_stored_value("legacy plaintext") == "legacy plaintext"


def test_invalid_versioned_value_is_not_treated_as_plaintext():
    from app.core.encryption import decrypt_stored_value
    with pytest.raises(EncryptionError):
        decrypt_stored_value("v1:broken")
