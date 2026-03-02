# core/views_blockchain.py
"""
Blockchain dashboard views for ChamaHub.
Members see their own blockchain activity.
Treasurers see full group activity plus Stellar account details.
"""

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.contrib import messages
from django.conf import settings
from django.utils.dateparse import parse_datetime
import logging

from core.models import (
    BlockchainTransaction,
    Contribution,
    Loan,
    Repayment,
    Withdrawal,
    ChamaProfile,
)
from core.services.stellar import StellarService

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Shared helper                                                       #
# ------------------------------------------------------------------ #

def _stellar_network():
    """
    Normalised, lowercase Stellar network name for use in templates and URLs.
    Returns 'testnet' or 'public'.
    """
    return getattr(settings, 'STELLAR_NETWORK', 'TESTNET').lower()


# ------------------------------------------------------------------ #
#  Blockchain dashboard                                               #
# ------------------------------------------------------------------ #

@login_required
def blockchain_dashboard(request):
    """
    Blockchain activity dashboard.

    Members see:
      - Their own blockchain-recorded transactions
      - Count of their confirmed transactions not yet on Stellar

    Treasurers additionally see:
      - Live Stellar account info (balance, sequence number)
      - Group-wide unrecorded transaction counts
      - All members' blockchain transactions (paginated)
    """
    stellar_enabled = getattr(settings, 'STELLAR_ENABLED', False)

    # ------------------------------------------------------------------ #
    #  Determine role                                                      #
    # ------------------------------------------------------------------ #
    try:
        profile = request.user.chama_profile
        is_treasurer = profile.is_treasurer()
    except ChamaProfile.DoesNotExist:
        is_treasurer = False

    # ------------------------------------------------------------------ #
    #  Stellar service status — only connect when enabled                 #
    # ------------------------------------------------------------------ #
    stellar_available = False
    account_info = None
    stellar_balance = 0.0   # FIX: float default, not int

    if stellar_enabled:
        try:
            stellar = StellarService()
            stellar_available = stellar.is_available

            # Only fetch live account info for treasurers — it contains
            # balance and sequence number which are sensitive operational data
            if is_treasurer and stellar_available:
                account_info = stellar.get_account_info()
                # get_account_info() returns balances as [{'type': 'XLM', 'balance': float}, ...]
                if account_info and account_info.get('balances'):
                    for balance in account_info['balances']:
                        if balance['type'] == 'XLM':
                            stellar_balance = float(balance['balance'])
                            break
        except Exception as e:
            logger.error(f"Could not initialise StellarService in dashboard: {e}")

    # ------------------------------------------------------------------ #
    #  Member's own blockchain transactions (paginated)                   #
    # ------------------------------------------------------------------ #
    member_txs_qs = (
        BlockchainTransaction.objects
        .filter(member=request.user)
        .select_related('member')
        .order_by('-created_at')
    )

    member_paginator = Paginator(member_txs_qs, 15)
    member_page = request.GET.get('page', 1)
    member_txs = member_paginator.get_page(member_page)

    # ------------------------------------------------------------------ #
    #  Member's unrecorded confirmed transactions                         #
    # ------------------------------------------------------------------ #
    member_unrecorded = {
        'contributions': Contribution.objects.filter(
            member=request.user,
            status='confirmed',
            stellar_tx_hash__isnull=True
        ).count(),
        'repayments': Repayment.objects.filter(
            loan__member=request.user,
            status='confirmed',
            stellar_tx_hash__isnull=True
        ).count(),
        'withdrawals': Withdrawal.objects.filter(
            member=request.user,
            status='confirmed',
            stellar_tx_hash__isnull=True
        ).count(),
    }
    member_unrecorded['total'] = sum(member_unrecorded.values())

    # Member's recorded count per transaction type (for summary cards)
    member_recorded_counts = {
        'contributions': BlockchainTransaction.objects.filter(
            member=request.user, transaction_type='contribution'
        ).count(),
        'repayments': BlockchainTransaction.objects.filter(
            member=request.user, transaction_type='repayment'
        ).count(),
        'withdrawals': BlockchainTransaction.objects.filter(
            member=request.user, transaction_type='withdrawal'
        ).count(),
        'loans': BlockchainTransaction.objects.filter(
            member=request.user, transaction_type='loan_disbursement'
        ).count(),
    }
    member_recorded_counts['total'] = (
        BlockchainTransaction.objects.filter(member=request.user).count()
    )

    # ------------------------------------------------------------------ #
    #  Treasurer-only: group-wide stats + all transactions                #
    # ------------------------------------------------------------------ #
    group_unrecorded = None
    group_recorded_counts = None
    all_txs = None

    if is_treasurer:
        # Unrecorded confirmed transactions across the whole group
        group_unrecorded = {
            'contributions': Contribution.objects.filter(
                status='confirmed',
                stellar_tx_hash__isnull=True
            ).count(),
            'loans': Loan.objects.filter(
                disbursed=True,
                stellar_tx_hash__isnull=True
            ).count(),
            'repayments': Repayment.objects.filter(
                status='confirmed',
                stellar_tx_hash__isnull=True
            ).count(),
            'withdrawals': Withdrawal.objects.filter(
                status='confirmed',
                stellar_tx_hash__isnull=True
            ).count(),
        }
        group_unrecorded['total'] = sum(group_unrecorded.values())

        # Total recorded per type across the group
        group_recorded_counts = {
            'contributions': BlockchainTransaction.objects.filter(
                transaction_type='contribution'
            ).count(),
            'repayments': BlockchainTransaction.objects.filter(
                transaction_type='repayment'
            ).count(),
            'withdrawals': BlockchainTransaction.objects.filter(
                transaction_type='withdrawal'
            ).count(),
            'loans': BlockchainTransaction.objects.filter(
                transaction_type='loan_disbursement'
            ).count(),
        }
        group_recorded_counts['total'] = BlockchainTransaction.objects.count()

        # All blockchain transactions paginated (treasurer sees everything)
        all_txs_qs = (
            BlockchainTransaction.objects
            .select_related('member')
            .order_by('-created_at')
        )
        all_paginator = Paginator(all_txs_qs, 20)
        all_page = request.GET.get('all_page', 1)
        all_txs = all_paginator.get_page(all_page)

    # ------------------------------------------------------------------ #
    #  Build context                                                       #
    # ------------------------------------------------------------------ #
    context = {
        # Service status
        'stellar_enabled': stellar_enabled,
        'stellar_available': stellar_available,

        # Role
        'is_treasurer': is_treasurer,

        # Member's own view
        'member_txs': member_txs,
        'member_unrecorded': member_unrecorded,
        'member_recorded_counts': member_recorded_counts,

        # Treasurer-only — None for regular members
        'account_info': account_info,
        'stellar_balance': stellar_balance,
        'group_unrecorded': group_unrecorded,
        'group_recorded_counts': group_recorded_counts,
        'all_txs': all_txs,

        # FIX: consistent key name 'stellar_network' (was 'network' in detail view)
        'stellar_network': _stellar_network(),

        # Public key only sent to treasurers — not needed by members
        'stellar_public_key': (
            getattr(settings, 'STELLAR_PUBLIC_KEY', '') if is_treasurer else ''
        ),
    }

    return render(request, 'core/blockchain_dashboard.html', context)


# ------------------------------------------------------------------ #
#  Transaction detail                                                  #
# ------------------------------------------------------------------ #

@login_required
def blockchain_transaction_detail(request, tx_hash):
    """
    Detail view for a single blockchain transaction.
    Members can only view their own. Treasurers can view any.
    """
    # FIX: replaced print() with logger.debug() — no stdout noise in production
    logger.debug("=== BLOCKCHAIN TRANSACTION DETAIL VIEW ===")
    logger.debug(f"Transaction Hash: {tx_hash}  |  User: {request.user.username}")

    try:
        profile = request.user.chama_profile
        is_treasurer = profile.is_treasurer()
    except ChamaProfile.DoesNotExist:
        is_treasurer = False

    # Members can only see their own transactions
    try:
        if is_treasurer:
            tx = BlockchainTransaction.objects.select_related('member').get(
                stellar_tx_hash=tx_hash
            )
        else:
            tx = BlockchainTransaction.objects.select_related('member').get(
                stellar_tx_hash=tx_hash,
                member=request.user
            )
        logger.debug(f"Found transaction in database: id={tx.id}")
    except BlockchainTransaction.DoesNotExist:
        logger.debug("Transaction not found in database")
        messages.error(request, 'Transaction not found.')
        return redirect('blockchain_dashboard')

    # Fetch the source object (Contribution / Loan / Repayment / Withdrawal)
    source_obj = None
    try:
        source_obj = tx.get_source_object()
        logger.debug(f"Found source object: {type(source_obj).__name__}")
    except Exception as e:
        logger.warning(f"Could not fetch source object for tx {tx.id}: {e}")

    # Optionally verify on-chain if Stellar is available
    on_chain_data = None
    stellar_enabled = getattr(settings, 'STELLAR_ENABLED', False)

    if stellar_enabled:
        try:
            stellar = StellarService()
            if stellar.is_available:
                on_chain_data = stellar.verify_transaction(tx_hash)
                logger.debug(f"Verified on Stellar: {bool(on_chain_data)}")
        except Exception as e:
            logger.error(f"Could not verify transaction {tx_hash} on Stellar: {e}")

    # ------------------------------------------------------------------ #
    #  Build tx_data dict for the template                                #
    #                                                                     #
    #  FIX 1: verify_transaction() returns created_at as a raw ISO        #
    #  string (e.g. "2024-01-15T09:22:11Z") from the Stellar API.         #
    #  Django's |date template filter only works on datetime objects,      #
    #  not strings — parse it here so the template always receives a       #
    #  proper datetime regardless of source.                               #
    #                                                                     #
    #  FIX 2: 'successful' defaults to None (not True) when we have no    #
    #  on-chain data — prevents showing "Verified ✅" on unverified txs.  #
    # ------------------------------------------------------------------ #
    if on_chain_data:
        raw_created_at = on_chain_data.get('created_at')
        # Parse ISO string → datetime; fall back to local DB timestamp if parsing fails
        if isinstance(raw_created_at, str):
            created_at = parse_datetime(raw_created_at) or tx.created_at
        else:
            created_at = raw_created_at or tx.created_at

        tx_data = {
            'hash':       on_chain_data.get('hash', tx_hash),
            'ledger':     on_chain_data.get('ledger', 'N/A'),
            'created_at': created_at,
            'memo':       on_chain_data.get('memo') or tx.memo,
            'successful': on_chain_data.get('successful'),   # None = unverified
        }
    else:
        # Stellar unavailable or tx not yet confirmed on-chain — use local data only
        tx_data = {
            'hash':       tx_hash,
            'ledger':     'N/A',
            'created_at': tx.created_at,
            'memo':       tx.memo,
            'successful': None,   # FIX: None = "not verified", not True = "confirmed"
        }

    # Recent transactions for sidebar (always current user's own)
    recent_txs = (
        BlockchainTransaction.objects
        .filter(member=request.user)
        .exclude(stellar_tx_hash=tx_hash)
        .order_by('-created_at')[:5]
    )

    type_meta = {
        'contribution':      ('piggy-bank',         'success'),
        'repayment':         ('credit-card',         'info'),
        'withdrawal':        ('money-bill-wave',      'warning'),
        'loan_disbursement': ('hand-holding-usd',    'primary'),
    }
    recent_list = []
    for rtx in recent_txs:
        icon, color = type_meta.get(rtx.transaction_type, ('circle', 'secondary'))
        recent_list.append({
            'hash':   rtx.stellar_tx_hash,
            'date':   rtx.created_at,
            'type':   rtx.get_transaction_type_display(),
            'amount': rtx.amount,
            'icon':   icon,
            'color':  color,
        })

    context = {
        'tx':                  tx,
        'tx_data':             tx_data,
        'tx_hash':             tx_hash,
        'source_obj':          source_obj,
        'record_type':         type(source_obj).__name__ if source_obj else 'Unknown',
        'on_chain_data':       on_chain_data,
        'is_treasurer':        is_treasurer,
        'stellar_enabled':     stellar_enabled,
        # FIX: consistent key name across both views
        'stellar_network':     _stellar_network(),
        'recent_transactions': recent_list,
        'debug':               settings.DEBUG,
    }

    return render(request, 'core/blockchain_transaction_detail.html', context)