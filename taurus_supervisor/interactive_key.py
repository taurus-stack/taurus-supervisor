"""
Interactive Key Input Tool

Prompts user to enter decryption key at startup. Key is not stored in environment variables or config files.
Provides higher level of security protection.
"""

import base64
import getpass
import hashlib
import logging
import os
import sys

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger('taurus-supervisor.interactive_key')

# Fernet key prefix identifier
FERNET_PREFIX = "fernet:"


def prompt_for_key(prompt: str = "Enter configuration decryption key: ") -> str:
    """
    Interactively prompt user to enter decryption key
    
    Args:
        prompt: Prompt message
        
    Returns:
        User-entered key
    """
    try:
        key = getpass.getpass(prompt)
        if not key:
            logger.error("Key cannot be empty")
            sys.exit(1)
        return key
    except (EOFError, KeyboardInterrupt):
        logger.info("User cancelled input")
        sys.exit(0)


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


def get_fernet_from_user_input(master_key: str) -> Fernet:
    """
    Get Fernet instance from user-inputted key
    
    Args:
        master_key: User-entered master key
        
    Returns:
        Fernet instance
    """
    try:
        return Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
    except Exception:
        return Fernet(derive_fernet_key(master_key))


def decrypt_with_interactive_key(encrypted_text: str) -> str:
    """
    Decrypt configuration value using interactively-entered key
    
    If key is not configured in environment variables, user will be prompted to enter it.
    
    Args:
        encrypted_text: Encrypted string
        
    Returns:
        Plaintext configuration value
    """
    if not encrypted_text:
        return encrypted_text
    
    if not encrypted_text.startswith(FERNET_PREFIX):
        return encrypted_text
    
    encrypted_text = encrypted_text[len(FERNET_PREFIX):]
    
    # Prefer key from environment variable
    master_key = os.environ.get('CONFIG_ENCRYPTION_KEY')
    
    # If no key in environment, prompt user to enter
    if not master_key:
        logger.info("CONFIG_ENCRYPTION_KEY not found in environment, please manually enter decryption key")
        master_key = prompt_for_key()
    
    try:
        fernet = get_fernet_from_user_input(master_key)
        decrypted = fernet.decrypt(encrypted_text.encode('utf-8'))
        return decrypted.decode('utf-8')
    except InvalidToken:
        logger.error("Decryption failed: master key mismatch or data corrupted")
        raise ValueError("Decryption failed: master key mismatch or data corrupted")


def auto_decrypt_interactive(value: str) -> str:
    """
    Auto-decrypt configuration value (supports interactive input)
    
    If value is not encrypted or decryption fails, returns original value.
    If key is not configured in environment variables, user will be prompted to enter it.
    
    Args:
        value: Configuration value
        
    Returns:
        Decrypted value or original value
    """
    if not value:
        return value
    
    # Check if encryption is enabled
    encryption_enabled = os.environ.get('CONFIG_ENCRYPTION_ENABLED', 'true').lower() == 'true'
    if not encryption_enabled:
        if value.startswith(FERNET_PREFIX):
            logger.debug("Encryption disabled, skipping decryption")
        return value
    
    if not value.startswith(FERNET_PREFIX):
        return value
    
    try:
        return decrypt_with_interactive_key(value)
    except Exception as e:
        logger.warning(f"Auto-decryption failed, returning original value: {e}")
        return value