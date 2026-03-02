#!/usr/bin/env python
"""
Complete test script for Stellar integration
Run this to verify everything is working
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chamahub.settings')
django.setup()

from core.services.stellar import test_stellar_service
from core.utils.encryption import test_encryption

def print_header(text):
    """Print a nice header"""
    print("\n" + "🎯" * 30)
    print(f"🎯 {text}")
    print("🎯" * 30 + "\n")

if __name__ == "__main__":
    print_header("CHAMAHUB STELLAR INTEGRATION TEST")
    
    # Test encryption first
    test_encryption()
    
    # Test Stellar service
    test_stellar_service()
    
    print_header("ALL TESTS COMPLETE")
    
    # Summary
    print("\n📋 Summary:")
    print("  • If you saw ✅ messages, everything is working!")
    print("  • If you saw ❌ messages, check your .env file")
    print("  • Make sure STELLAR_ENABLED=True in your .env")
    print("  • Make sure your Stellar account is funded\n")