"""
Unit tests for taurus_supervisor/config_crypto.py

Test contents:
1. derive_fernet_key - Derive Fernet key from master key
2. get_fernet - Get Fernet instance
3. decrypt_value - Decrypt config value
4. auto_decrypt - Auto decrypt
"""
import os
import pytest

from taurus_supervisor.config_crypto import (
    derive_fernet_key,
    get_fernet,
    decrypt_value,
    auto_decrypt,
    FERNET_PREFIX,
)


def _encrypt_with_backend(value: str, master_key: str) -> str:
    from cryptography.fernet import Fernet
    import base64
    import hashlib

    key_bytes = hashlib.sha256(master_key.encode("utf-8")).digest()
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    fernet = Fernet(key_b64)
    encrypted = fernet.encrypt(value.encode("utf-8"))
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


class TestGetFernet:
    def test_with_explicit_master_key(self):
        f = get_fernet("test-master-key")
        assert f is not None

    def test_with_valid_fernet_key(self):
        from cryptography.fernet import Fernet
        valid_key = Fernet.generate_key().decode()
        f = get_fernet(valid_key)
        assert f is not None

    def test_without_master_key_and_env_raises(self):
        os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
        with pytest.raises(ValueError, match="CONFIG_ENCRYPTION_KEY"):
            get_fernet(None)

    def test_from_env_variable(self):
        os.environ["CONFIG_ENCRYPTION_KEY"] = "env-test-key"
        try:
            f = get_fernet()
            assert f is not None
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_KEY", None)


class TestDecryptValue:
    def test_decrypt_encrypted_value(self):
        encrypted = _encrypt_with_backend("my-secret", "test-key")
        result = decrypt_value(encrypted, master_key="test-key")
        assert result == "my-secret"

    def test_decrypt_with_wrong_key_raises(self):
        encrypted = _encrypt_with_backend("secret", "correct-key")
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt_value(encrypted, master_key="wrong-key")

    def test_decrypt_empty_string(self):
        assert decrypt_value("", master_key="key") == ""

    def test_decrypt_non_encrypted_returns_as_is(self):
        result = decrypt_value("plain-text", master_key="key")
        assert result == "plain-text"

    def test_decrypt_unicode(self):
        encrypted = _encrypt_with_backend("chinese-password-测试", "test-key")
        result = decrypt_value(encrypted, master_key="test-key")
        assert result == "chinese-password-测试"


class TestAutoDecrypt:
    def test_decrypts_encrypted_value(self):
        encrypted = _encrypt_with_backend("secret-value", "test-key")
        os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "true"
        try:
            result = auto_decrypt(encrypted, master_key="test-key")
            assert result == "secret-value"
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)

    def test_returns_plain_value_unchanged(self):
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "true"
        try:
            result = auto_decrypt("plain-text", master_key="key")
            assert result == "plain-text"
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)

    def test_disabled_returns_original(self):
        encrypted = _encrypt_with_backend("secret", "test-key")
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "false"
        try:
            result = auto_decrypt(encrypted, master_key="test-key")
            assert result == encrypted
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)

    def test_empty_value(self):
        assert auto_decrypt("", master_key="key") == ""

    def test_none_value(self):
        assert auto_decrypt(None, master_key="key") is None

    def test_failed_decryption_returns_original(self):
        encrypted = _encrypt_with_backend("secret", "correct-key")
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "true"
        os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
        try:
            result = auto_decrypt(encrypted, master_key="wrong-key")
            assert result == encrypted
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)