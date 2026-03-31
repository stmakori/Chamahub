import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Contribution, Loan, Repayment, Withdrawal
from .utils.stellar_recorder import StellarRecorder

logger = logging.getLogger(__name__)


def _should_auto_record():
    return bool(getattr(settings, 'STELLAR_ENABLED', False) and getattr(settings, 'STELLAR_AUTO_RECORD', True))


def _record_if_needed(record_fn, instance, label):
    if not _should_auto_record():
        return

    if getattr(instance, 'stellar_tx_hash', None):
        return

    try:
        recorder = StellarRecorder()
        if recorder.enabled:
            record_fn(recorder, instance)
    except Exception as exc:
        logger.error("Auto-record failed for %s #%s: %s", label, instance.pk, exc)


@receiver(post_save, sender=Contribution)
def auto_record_contribution(sender, instance, **kwargs):
    if instance.status == 'confirmed':
        _record_if_needed(lambda r, obj: r.record_contribution(obj), instance, 'contribution')


@receiver(post_save, sender=Repayment)
def auto_record_repayment(sender, instance, **kwargs):
    if instance.status == 'confirmed':
        _record_if_needed(lambda r, obj: r.record_repayment(obj), instance, 'repayment')


@receiver(post_save, sender=Withdrawal)
def auto_record_withdrawal(sender, instance, **kwargs):
    if instance.status == 'confirmed':
        _record_if_needed(lambda r, obj: r.record_withdrawal(obj), instance, 'withdrawal')


@receiver(post_save, sender=Loan)
def auto_record_loan_disbursement(sender, instance, **kwargs):
    if instance.status == 'disbursed' or instance.disbursed:
        _record_if_needed(lambda r, obj: r.record_loan_disbursement(obj), instance, 'loan_disbursement')
