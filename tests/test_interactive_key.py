"""
Unit tests for taurus_supervisor/interactive_key.py

Test contents:
1. derive_fernet_key - Derive Fernet key from master key
2. get_fernet_from_user_input - Get Fernet instance from user input
3. decrypt_with_interactive_key - Decrypt with interactive key
4. auto_decrypt_interactive - Auto decrypt config values
"""
import os
import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet

from taurus_supervisor.interactive_key import (
    derive_fernet_key,
    get_fernet_from_user_input,
    decrypt_with_interactive_key,
    auto_decrypt_interactive,
    FERNET_PREFIX,
)


def _encrypt_with_key(plaintext: str, master_key: str) -> str:
    key_b64 = derive_fernet_key(master_key)
    fernet = Fernet(key_b64)
    encrypted = fernet.encrypt(plaintext.encode("utf-8"))
    return f"{FERNET_PREFIX}{encrypted.decode('utf-8')}"


class TestDeriveFernetKey:
    def test_returns_bytes(self):
        result = derive_fernet_key("test-key")
        assert isinstance(result, bytes)

    def test_deterministic(self):
        key1 = derive_fernet_key("same-key")
        key2 = derive_fernet_key("same-key")
        assert key1 == key2

    def test_different_keys_produce_different_results(self):
        key1 = derive_fernet_key("key-a")
        key2 = derive_fernet_key("key-b")
        assert key1 != key2

    def test_valid_fernet_key_length(self):
        result = derive_fernet_key("any-key")
        assert len(result) == 44

    def test_can_create_fernet_instance(self):
        key = derive_fernet_key("test")
        fernet = Fernet(key)
        assert fernet is not None

    def test_empty_string_key(self):
        result = derive_fernet_key("")
        assert isinstance(result, bytes)
        assert len(result) == 44


class TestGetFernetFromUserInput:
    def test_valid_fernet_key_string(self):
        fernet_key = Fernet.generate_key().decode()
        result = get_fernet_from_user_input(fernet_key)
        assert isinstance(result, Fernet)

    def test_arbitrary_key_derived(self):
        result = get_fernet_from_user_input("my-secret-passphrase")
        assert isinstance(result, Fernet)

    def test_derived_key_can_encrypt_decrypt(self):
        fernet = get_fernet_from_user_input("passphrase")
        plaintext = b"hello world"
        encrypted = fernet.encrypt(plaintext)
        assert fernet.decrypt(encrypted) == plaintext

    def test_same_key_produces_same_fernet(self):
        f1 = get_fernet_from_user_input("same")
        f2 = get_fernet_from_user_input("same")
        token = f1.encrypt(b"test")
        assert f2.decrypt(token) == b"test"


class TestDecryptWithInteractiveKey:
    def test_decrypt_with_env_key(self):
        master_key = "env-master-key"
        encrypted = _encrypt_with_key("secret-value", master_key)

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": master_key}):
            result = decrypt_with_interactive_key(encrypted)
            assert result == "secret-value"

    def test_decrypt_empty_string(self):
        result = decrypt_with_interactive_key("")
        assert result == ""

    def test_decrypt_non_encrypted_value(self):
        result = decrypt_with_interactive_key("plain-text")
        assert result == "plain-text"

    def test_decrypt_wrong_key_raises(self):
        encrypted = _encrypt_with_key("secret", "correct-key")

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": "wrong-key"}):
            with pytest.raises(ValueError, match="Decryption failed"):
                decrypt_with_interactive_key(encrypted)

    def test_decrypt_with_derived_env_key(self):
        master_key = "my-passphrase"
        key_b64 = derive_fernet_key(master_key)
        fernet = Fernet(key_b64)
        encrypted = fernet.encrypt(b"test-data")
        encrypted_str = f"{FERNET_PREFIX}{encrypted.decode('utf-8')}"

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": master_key}):
            result = decrypt_with_interactive_key(encrypted_str)
            assert result == "test-data"


class TestAutoDecryptInteractive:
    def test_non_encrypted_value_returned_as_is(self):
        result = auto_decrypt_interactive("plain-value")
        assert result == "plain-value"

    def test_empty_value_returned_as_is(self):
        result = auto_decrypt_interactive("")
        assert result == ""

    def test_none_value_returned_as_is(self):
        result = auto_decrypt_interactive(None)
        assert result is None

    def test_encrypted_value_decrypted(self):
        master_key = "auto-decrypt-key"
        encrypted = _encrypt_with_key("decrypted-value", master_key)

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": master_key}):
            result = auto_decrypt_interactive(encrypted)
            assert result == "decrypted-value"

    def test_encryption_disabled_returns_original(self):
        master_key = "disabled-key"
        encrypted = _encrypt_with_key("secret", master_key)

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_ENABLED": "false", "CONFIG_ENCRYPTION_KEY": master_key}):
            result = auto_decrypt_interactive(encrypted)
            assert result == encrypted

    def test_decryption_failure_returns_original(self):
        encrypted = f"{FERNET_PREFIX}invalid-encrypted-data-!!!"

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": "some-key"}):
            result = auto_decrypt_interactive(encrypted)
            assert result == encrypted