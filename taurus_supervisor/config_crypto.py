"""
Configuration Decryption Utility

Used to decrypt encrypted configurations (such as signing keys) distributed from Taurus backend.
Uses Fernet symmetric encryption algorithm, consistent with backend taurus/config_crypto.py.
"""

import base64
import hashlib
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger('taurus-supervisor.config_crypto')

# Fernet key prefix identifier (consistent with backend)
FERNET_PREFIX = "fernet:"


def derive_fernet_key(master_key: str) -> bytes:
    """
    Derive Fernet key from master key
    
    Args:
        master_key: Master key string of arbitrary length
        
    Returns:
        Fernet key (URL-safe base64 encoded)
    """
    key_bytes = hashlib.sha256(master_key.encode('utf-8')).digest()
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    return key_b64


def get_fernet(master_key: Optional[str] = None) -> Fernet:
    """
    Get Fernet instance
    
    Args:
        master_key: Master key, if None will read from environment variable
        
    Returns:
        Fernet instance
    """
    if master_key is None:
        master_key = os.environ.get('CONFIG_ENCRYPTION_KEY')
        if not master_key:
            raise ValueError(
                "CONFIG_ENCRYPTION_KEY not configured, please set master key in environment variable"
            )
    
    try:
        return Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
    except Exception:
        return Fernet(derive_fernet_key(master_key))


def decrypt_value(encrypted_text: str, master_key: Optional[str] = None) -> str:
    """
    Decrypt configuration value
    
    Args:
        encrypted_text: Encrypted string (with or without fernet: prefix)
        master_key: Master key (optional)
        
    Returns:
        Plaintext configuration value
    """
    if not encrypted_text:
        return encrypted_text
    
    if encrypted_text.startswith(FERNET_PREFIX):
        encrypted_text = encrypted_text[len(FERNET_PREFIX):]
    else:
        return encrypted_text
    
    try:
        fernet = get_fernet(master_key)
        decrypted = fernet.decrypt(encrypted_text.encode('utf-8'))
        return decrypted.decode('utf-8')
    except InvalidToken:
        logger.error("Decryption failed: master key mismatch or data corrupted")
        raise ValueError("Decryption failed: master key mismatch or data corrupted")


def auto_decrypt(value: str, master_key: Optional[str] = None) -> str:
    """
    Auto-decrypt configuration value (if encrypted)
    
    If value is not encrypted or decryption fails, returns original value.
    
    If CONFIG_ENCRYPTION_ENABLED=False, returns original value directly (skips decryption).
    
    Args:
        value: Configuration value
        master_key: Master key (optional)
        
    Returns:
        Decrypted value or original value
    """
    if not value:
        return value
    
    # Check if encryption is enabled (can be set to False in development)
    encryption_enabled = os.environ.get('CONFIG_ENCRYPTION_ENABLED', 'true').lower() == 'true'
    if not encryption_enabled:
        # Development: skip encryption, return original value directly
        if value.startswith(FERNET_PREFIX):
            logger.debug("Encryption disabled, skipping decryption")
        return value
    
    if not value.startswith(FERNET_PREFIX):
        return value
    
    try:
        return decrypt_value(value, master_key)
    except Exception as e:
        logger.warning(f"Auto-decryption failed, returning original value: {e}")
        return value