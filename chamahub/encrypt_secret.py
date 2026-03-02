#!/usr/bin/env python
"""
Helper script to encrypt your Stellar secret key
Run this to generate the encrypted version for your .env file
"""

from cryptography.fernet import Fernet
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

def encrypt_secret():
    """Encrypt the Stellar secret key using the encryption key"""
    
    # Get encryption key from .env
    encryption_key = os.getenv('STELLAR_ENCRYPTION_KEY')
    if not encryption_key:
        print("❌ STELLAR_ENCRYPTION_KEY not found in .env")
        return
    
    # Get the plain text secret
    secret_key = os.getenv('STELLAR_SECRET_KEY_PLAIN')
    if not secret_key:
        print("\n⚠️  STELLAR_SECRET_KEY_PLAIN not found in .env")
        print("Let's enter it manually:")
        secret_key = input("Enter your Stellar secret key: ").strip()
    
    try:
        # Create cipher
        cipher = Fernet(encryption_key.encode())
        
        # Encrypt the secret
        encrypted = cipher.encrypt(secret_key.encode())
        encrypted_str = encrypted.decode()
        
        print("\n" + "=" * 60)
        print("✅ SUCCESS! Add this to your .env file:")
        print("=" * 60)
        print(f"\nSTELLAR_SECRET_KEY={encrypted_str}\n")
        print("=" * 60)
        print("\n⚠️  Remove STELLAR_SECRET_KEY_PLAIN from .env after adding this!")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Encryption failed: {e}")

if __name__ == "__main__":
    encrypt_secret()
