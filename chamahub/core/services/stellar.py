"""
Stellar Blockchain Service for ChamaHub
This service handles all interactions with the Stellar blockchain
"""

# ✅ Removed: Memo — no longer imported directly in v13
from stellar_sdk import Server, Keypair, TransactionBuilder, Network, Asset
from stellar_sdk.exceptions import NotFoundError, BadResponseError, BadRequestError
from django.conf import settings
from cryptography.fernet import Fernet
import logging
import time

logger = logging.getLogger(__name__)


class StellarService:
    """
    Main service class for interacting with Stellar blockchain
    Uses a single chama account to record all transactions
    """

    def __init__(self):
        self.enabled = getattr(settings, 'STELLAR_ENABLED', False)

        if not self.enabled:
            logger.info("⚠️ Stellar integration is disabled in settings")
            self.is_available = False
            return

        self.public_key = getattr(settings, 'STELLAR_PUBLIC_KEY', '')
        self.encrypted_secret = getattr(settings, 'STELLAR_SECRET_KEY', '')
        self.encryption_key = getattr(settings, 'STELLAR_ENCRYPTION_KEY', '')
        self.network = getattr(settings, 'STELLAR_NETWORK', 'TESTNET')
        self.horizon_url = getattr(
            settings, 'STELLAR_HORIZON_URL', 'https://horizon-testnet.stellar.org'
        )

        if not self.public_key:
            logger.error("❌ STELLAR_PUBLIC_KEY not set in settings")
            self.is_available = False
            return

        if not self.encrypted_secret:
            logger.error("❌ STELLAR_SECRET_KEY not set in settings")
            self.is_available = False
            return

        if not self.encryption_key:
            logger.error("❌ STELLAR_ENCRYPTION_KEY not set in settings")
            self.is_available = False
            return

        if self.network == 'PUBLIC':
            self.network_passphrase = Network.PUBLIC_NETWORK_PASSPHRASE
            logger.info("🌐 Using Stellar MAINNET")
        else:
            self.network_passphrase = Network.TESTNET_NETWORK_PASSPHRASE
            logger.info("🌐 Using Stellar TESTNET")

        self.server = Server(self.horizon_url)
        self.keypair = self._load_keypair()

        if self.keypair:
            self.is_available = True
            logger.info(f"✅ Stellar service initialized with account: {self.public_key[:10]}...")
        else:
            self.is_available = False

    def _load_keypair(self):
        try:
            cipher = Fernet(self.encryption_key.encode())
            decrypted_secret = cipher.decrypt(self.encrypted_secret.encode()).decode()
            keypair = Keypair.from_secret(decrypted_secret)

            if keypair.public_key != self.public_key:
                logger.error("❌ Decrypted secret key doesn't match public key!")
                return None

            logger.info("🔐 Successfully decrypted Stellar secret key")
            return keypair

        except Exception as e:
            logger.error(f"❌ Failed to decrypt Stellar secret key: {e}")
            return None

    def check_connection(self):
        """Test connection to Stellar network"""
        if not self.is_available:
            return False, "Stellar service not available"

        try:
            # ✅ v13: fetch_base_fee() is the reliable way to test connectivity
            self.server.fetch_base_fee()
            return True, "Connected to Stellar network"
        except Exception as e:
            return False, f"Connection failed: {e}"

    def get_account_info(self):
        """Get account details from Stellar"""
        if not self.is_available:
            logger.warning("Cannot get account info: Stellar not available")
            return None

        try:
            account = self.server.accounts().account_id(self.public_key).call()

            balances = []
            for balance in account['balances']:
                if balance['asset_type'] == 'native':
                    balances.append({
                        'type': 'XLM',
                        'balance': float(balance['balance'])
                    })
                else:
                    balances.append({
                        'type': balance['asset_code'],
                        'issuer': balance['asset_issuer'],
                        'balance': float(balance['balance'])
                    })

            result = {
                'id': account['id'],
                'sequence': account['sequence'],
                'balances': balances,
                'subentry_count': account.get('subentry_count', 0),
                'last_modified': account.get('last_modified_ledger') or account.get('last_modified')
            }

            logger.info(f"✅ Retrieved account info for {self.public_key[:10]}...")
            return result

        except NotFoundError:
            logger.error(f"❌ Account {self.public_key[:10]}... not found on Stellar")
            logger.error(f"   Fund it via: https://friendbot.stellar.org?addr={self.public_key}")
            return None
        except Exception as e:
            logger.error(f"❌ Error getting account info: {e}")
            return None

    def get_xlm_balance(self):
        """Get just the XLM balance as a float"""
        info = self.get_account_info()
        if info:
            for balance in info['balances']:
                if balance['type'] == 'XLM':
                    return balance['balance']
        return 0.0

    def record_transaction(self, transaction_type, transaction_id, amount, member_name):
        """
        Record a transaction on the Stellar blockchain

        Args:
            transaction_type: 'CONTRIB', 'LOAN', 'REPAY', 'WITHDRAW'
            transaction_id: ID in your database
            amount: Amount in KSh
            member_name: Username or name of member

        Returns:
            transaction_hash if successful, None if failed
        """
        if not self.is_available or not self.keypair:
            logger.warning("Stellar not available or keypair not loaded")
            return None

        try:
            memo_text = f"{transaction_type}:{transaction_id}:{int(amount)}"

            # Memo max length is 28 bytes — truncate if needed
            if len(memo_text.encode('utf-8')) > 28:
                memo_text = memo_text[:28]

            account = self.server.load_account(self.public_key)

            # ✅ v13: use .add_text_memo() on the builder — Memo class no longer imported
            # ✅ v13: .set_timeout() is required, otherwise build() raises an error
            tx = (
                TransactionBuilder(
                    source_account=account,
                    network_passphrase=self.network_passphrase,
                    base_fee=100
                )
                .append_payment_op(
                    destination=self.public_key,
                    asset=Asset.native(),
                    amount="0.0000001"
                )
                .add_text_memo(memo_text)
                .set_timeout(30)
                .build()
            )

            tx.sign(self.keypair)
            response = self.server.submit_transaction(tx)

            tx_hash = response['hash']
            logger.info(f"✅ Transaction recorded on Stellar: {tx_hash} - {memo_text}")
            return tx_hash

        except Exception as e:
            logger.error(f"❌ Failed to record transaction on Stellar: {e}")
            return None

    def verify_transaction(self, tx_hash):
        """Check if a transaction exists on the blockchain"""
        try:
            tx = self.server.transactions().transaction(tx_hash).call()
            return {
                'hash': tx['hash'],
                'ledger': tx['ledger'],
                'created_at': tx['created_at'],
                'memo': tx.get('memo', ''),
                'successful': tx['successful']
            }
        except Exception as e:
            logger.error(f"Failed to verify transaction: {e}")
            return None

    def __str__(self):
        status = "✅ Available" if self.is_available else "❌ Not Available"
        return f"StellarService({status}, Network: {self.network})"


def test_stellar_service():
    """Test function to verify Stellar service is working"""
    print("\n" + "=" * 60)
    print("🧪 TESTING STELLAR SERVICE")
    print("=" * 60)

    stellar = StellarService()

    print(f"\n📊 Service Status:")
    print(f"  - Available: {stellar.is_available}")
    print(f"  - Network: {stellar.network}")
    print(f"  - Public Key: {stellar.public_key[:10]}...")

    if not stellar.is_available:
        print("\n❌ Service not available. Check your settings.")
        return

    print(f"\n🌐 Testing Connection:")
    connected, message = stellar.check_connection()
    print(f"  - {message}")

    if connected:
        print(f"\n💰 Getting Account Info:")
        info = stellar.get_account_info()
        if info:
            print(f"  - Account ID: {info['id'][:10]}...")
            print(f"  - Sequence: {info['sequence']}")
            for balance in info['balances']:
                print(f"    • {balance['type']}: {balance['balance']}")

            print(f"\n📝 Testing Transaction Recording:")
            tx_hash = stellar.record_transaction(
                transaction_type='TEST',
                transaction_id=999,
                amount=1000,
                member_name='test_user'
            )
            if tx_hash:
                print(f"  ✅ Test transaction recorded!")
                print(f"  🔗 Hash: {tx_hash[:20]}...")

                print(f"\n🔍 Verifying Transaction:")
                time.sleep(2)
                verified = stellar.verify_transaction(tx_hash)
                if verified:
                    print(f"  ✅ Transaction verified on Stellar!")
                    print(f"  📅 Created: {verified['created_at']}")
                    print(f"  📝 Memo: {verified['memo']}")
                else:
                    print(f"  ❌ Could not verify transaction")
            else:
                print(f"  ❌ Failed to record test transaction")
        else:
            print("  ❌ Could not retrieve account info")
            print(f"\n💡 Fund via: https://friendbot.stellar.org?addr={stellar.public_key}")

    print("\n" + "=" * 60)
    print("✅ Test complete!")
    print("=" * 60)