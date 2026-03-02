"""
Encryption utilities for ChamaHub
Handles encryption and decryption of sensitive data like Stellar secret keys
"""

from cryptography.fernet import Fernet
from django.conf import settings
import base64
import logging

logger = logging.getLogger(__name__)

def get_cipher():
    """
    Get a Fernet cipher instance using the encryption key from settings
    """
    encryption_key = getattr(settings, 'STELLAR_ENCRYPTION_KEY', None)
    
    if not encryption_key:
        logger.error("❌ STELLAR_ENCRYPTION_KEY not found in settings")
        raise ValueError("Encryption key not configured")
    
    # Ensure key is in bytes
    if isinstance(encryption_key, str):
        encryption_key = encryption_key.encode()
    
    return Fernet(encryption_key)

def encrypt_value(value):
    """
    Encrypt a value using the configured encryption key
    
    Args:
        value: String to encrypt
        
    Returns:
        Encrypted string (base64 encoded)
    """
    if not value:
        return None
    
    try:
        cipher = get_cipher()
        encrypted = cipher.encrypt(value.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise

def decrypt_value(encrypted_value):
    """
    Decrypt a value using the configured encryption key
    
    Args:
        encrypted_value: Encrypted string (base64 encoded)
        
    Returns:
        Decrypted string
    """
    if not encrypted_value:
        return None
    
    try:
        cipher = get_cipher()
        decrypted = cipher.decrypt(encrypted_value.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        raise

def encrypt_stellar_secret(secret):
    """
    Specifically for Stellar secret keys
    """
    return encrypt_value(secret)

def decrypt_stellar_secret(encrypted):
    """
    Specifically for Stellar secret keys
    """
    return decrypt_value(encrypted)

def test_encryption():
    """Test function to verify encryption works"""
    print("\n" + "=" * 60)
    print("🔐 TESTING ENCRYPTION")
    print("=" * 60)
    
    try:
        # Test data
        test_secret = "SAKGBFMBBQCOAVSVKBUS7P4BFZMN6G7BNGWQUT376BLQX5ZTVZFHDP7S"
        
        print(f"\n📝 Original: {test_secret[:10]}...")
        
        # Encrypt
        encrypted = encrypt_value(test_secret)
        print(f"🔒 Encrypted: {encrypted[:20]}...")
        
        # Decrypt
        decrypted = decrypt_value(encrypted)
        print(f"🔓 Decrypted: {decrypted[:10]}...")
        
        # Verify
        if decrypted == test_secret:
            print("✅ Encryption/Decryption successful!")
        else:
            print("❌ Encryption/Decryption failed - data mismatch")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
    
    print("\n" + "=" * 60)