# core/utils/stellar_recorder.py
"""
Helper functions to record Django model transactions on Stellar blockchain.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class StellarRecorder:
    """
    Records chama model transactions (Contribution, Loan, Repayment, Withdrawal)
    onto the Stellar blockchain.

    Relies on StellarMixin.mark_stellar_recorded() to:
      - Update stellar_tx_hash and stellar_recorded_at on the model instance
      - Create the corresponding BlockchainTransaction log entry (get_or_create)

    So this class only needs to call the Stellar service and then delegate
    the DB writes to the mixin — no duplication.
    """

    # Maps each model type to the short code StellarService uses in the memo
    TRANSACTION_TYPE_CODES = {
        'contribution':       'CONTRIB',
        'loan_disbursement':  'LOAN',
        'repayment':          'REPAY',
        'withdrawal':         'WITHDRAW',
    }

    def __init__(self):
        self.stellar = None
        try:
            # Lazy import prevents Django startup failure when stellar_sdk is missing.
            from core.services.stellar import StellarService
            self.stellar = StellarService()
        except Exception as exc:
            logger.warning("Stellar service unavailable during init: %s", exc)

        self.enabled = (
            getattr(settings, 'STELLAR_ENABLED', False)
            and self.stellar is not None
            and self.stellar.is_available
        )

        if not self.enabled:
            logger.info("⚠️ StellarRecorder: recording is disabled or service unavailable")

    def _record(self, tx_type, obj, amount, member):
        """
        Internal method shared by all public record_* methods.

        Args:
            tx_type (str): One of TRANSACTION_TYPE_CODES keys
            obj: The model instance (Contribution, Loan, Repayment, Withdrawal)
            amount (Decimal): The transaction amount
            member (User): The member who owns the transaction

        Returns:
            str | None: Stellar tx hash on success, None on failure
        """
        if not self.enabled:
            logger.info(f"Stellar recording disabled — skipping {tx_type} #{obj.pk}")
            return None

        # Idempotency guard: never submit again if we already stored a hash.
        if getattr(obj, 'stellar_tx_hash', None):
            logger.info(f"Stellar already recorded — skipping {tx_type} #{obj.pk}")
            return obj.stellar_tx_hash

        type_code = self.TRANSACTION_TYPE_CODES[tx_type]

        try:
            tx_hash = self.stellar.record_transaction(
                transaction_type=type_code,
                transaction_id=obj.pk,
                amount=float(amount),
                member_name=member.username
            )

            if not tx_hash:
                logger.error(f"❌ Stellar returned no hash for {tx_type} #{obj.pk}")
                return None

            # Delegates DB writes to StellarMixin.mark_stellar_recorded():
            #   - saves stellar_tx_hash + stellar_recorded_at on the instance
            #   - creates BlockchainTransaction via get_or_create (safe on retries)
            obj.mark_stellar_recorded(tx_hash)

            logger.info(f"✅ {tx_type} #{obj.pk} recorded on Stellar: {tx_hash}")
            return tx_hash

        except Exception as e:
            logger.error(f"❌ Error recording {tx_type} #{obj.pk} on Stellar: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def record_contribution(self, contribution):
        """
        Record a confirmed contribution on Stellar.

        Args:
            contribution (Contribution): Must already be saved (has a pk)

        Returns:
            str | None: Stellar tx hash on success, None on failure
        """
        return self._record(
            tx_type='contribution',
            obj=contribution,
            amount=contribution.amount,
            member=contribution.member,
        )

    def record_loan_disbursement(self, loan):
        """
        Record a loan disbursement on Stellar.

        Args:
            loan (Loan): Must be in disbursed state and already saved

        Returns:
            str | None: Stellar tx hash on success, None on failure
        """
        return self._record(
            tx_type='loan_disbursement',
            obj=loan,
            amount=loan.amount,
            member=loan.member,
        )

    def record_repayment(self, repayment):
        """
        Record a loan repayment on Stellar.

        Args:
            repayment (Repayment): Must already be saved

        Returns:
            str | None: Stellar tx hash on success, None on failure
        """
        return self._record(
            tx_type='repayment',
            obj=repayment,
            amount=repayment.amount,
            # Repayment has no direct member FK — traverse through loan
            member=repayment.loan.member,
        )

    def record_withdrawal(self, withdrawal):
        """
        Record a withdrawal on Stellar.

        Args:
            withdrawal (Withdrawal): Must already be saved

        Returns:
            str | None: Stellar tx hash on success, None on failure
        """
        return self._record(
            tx_type='withdrawal',
            obj=withdrawal,
            amount=withdrawal.amount,
            member=withdrawal.member,
        )