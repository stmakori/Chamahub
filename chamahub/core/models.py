from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, RegexValidator
from django.utils import timezone
from decimal import Decimal


# ===== STELLAR MIXIN =====

class StellarMixin(models.Model):
    STELLAR_NETWORK = 'testnet'

    stellar_tx_hash = models.CharField(max_length=64, blank=True, null=True)
    stellar_recorded_at = models.DateTimeField(blank=True, null=True)

    def get_stellar_url(self):
        if self.stellar_tx_hash:
            return f"https://stellar.expert/explorer/{self.STELLAR_NETWORK}/tx/{self.stellar_tx_hash}"
        return None

    def is_on_stellar(self):
        return bool(self.stellar_tx_hash)

    def mark_stellar_recorded(self, tx_hash):
        self.stellar_tx_hash = tx_hash
        self.stellar_recorded_at = timezone.now()
        self.save(update_fields=['stellar_tx_hash', 'stellar_recorded_at'])

        model_to_type = {
            'Contribution': 'contribution',
            'Loan': 'loan_disbursement',
            'Repayment': 'repayment',
            'Withdrawal': 'withdrawal',
        }
        tx_type = model_to_type.get(self.__class__.__name__)

        if tx_type:
            member = getattr(self, 'member', None) or getattr(
                getattr(self, 'loan', None), 'member', None
            )
            type_code = tx_type.upper()[:6]
            memo = f"{type_code}:{self.pk}:{int(self.amount)}"[:28]

            BlockchainTransaction.objects.get_or_create(
                stellar_tx_hash=tx_hash,
                defaults={
                    'transaction_type': tx_type,
                    'reference_id': self.pk,
                    'amount': self.amount,
                    'member': member,
                    'memo': memo,
                    'confirmed_at': timezone.now(),
                }
            )

    class Meta:
        abstract = True


# ===== MODELS =====

class Contribution(StellarMixin, models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]

    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(Decimal('0.01'))])
    date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payhero_reference = models.CharField(max_length=100, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    status_updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Contribution'
        verbose_name_plural = 'Contributions'
        indexes = [
            models.Index(fields=['member', 'status']),
            models.Index(fields=['payhero_reference']),
        ]

    def __str__(self):
        return f"{self.member.username} - {self.amount} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk:
            old = Contribution.objects.filter(pk=self.pk).first()
            if old and old.status != self.status:
                self.status_updated_at = timezone.now()
        super().save(*args, **kwargs)


class Loan(StellarMixin, models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('disbursed', 'Disbursed'),
        ('rejected', 'Rejected'),
    ]

    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='loans')
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(Decimal('0.01'))])
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved = models.BooleanField(default=False)
    disbursed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    disbursed_at = models.DateTimeField(blank=True, null=True)
    purpose = models.TextField()
    repayment_period_months = models.PositiveIntegerField(default=12)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='approved_loans')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Loan'
        verbose_name_plural = 'Loans'
        indexes = [
            models.Index(fields=['member', 'status']),
            models.Index(fields=['approved', 'disbursed']),
        ]

    def __str__(self):
        return f"{self.member.username} - {self.amount} ({self.status})"

    def total_due(self):
        interest_amount = (self.amount * self.interest_rate) / 100
        return self.amount + interest_amount

    def get_repayment_progress(self):
        total_repaid = sum(r.amount for r in self.repayments.filter(status='confirmed'))
        total_due = self.total_due()
        if total_due > 0:
            return float((total_repaid / total_due) * 100)
        return 0.0

    def get_paid_amount(self):
        return sum(r.amount for r in self.repayments.filter(status='confirmed'))

    def get_remaining_amount(self):
        remaining = self.total_due() - self.get_paid_amount()
        return max(Decimal('0.00'), remaining)

    def is_overdue(self):
        if not self.disbursed or not self.disbursed_at:
            return False
        expected_date = self.disbursed_at + timezone.timedelta(days=30 * self.repayment_period_months)
        return timezone.now() > expected_date and self.get_remaining_amount() > 0


class Repayment(StellarMixin, models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='repayments')
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(Decimal('0.01'))])
    date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payhero_reference = models.CharField(max_length=100, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    status_updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Repayment'
        verbose_name_plural = 'Repayments'
        indexes = [
            models.Index(fields=['loan', 'status']),
            models.Index(fields=['payhero_reference']),
        ]

    def __str__(self):
        return f"{self.loan.member.username} - {self.amount} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk:
            old = Repayment.objects.filter(pk=self.pk).first()
            if old and old.status != self.status:
                self.status_updated_at = timezone.now()
        super().save(*args, **kwargs)


class Withdrawal(StellarMixin, models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]

    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='withdrawals')
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(Decimal('1.00'))])
    date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payhero_reference = models.CharField(max_length=100, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    status_updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Withdrawal'
        verbose_name_plural = 'Withdrawals'
        indexes = [
            models.Index(fields=['member', 'status']),
            models.Index(fields=['payhero_reference']),
        ]

    def __str__(self):
        return f"{self.member.username} - {self.amount} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk:
            old = Withdrawal.objects.filter(pk=self.pk).first()
            if old and old.status != self.status:
                self.status_updated_at = timezone.now()
        super().save(*args, **kwargs)


class ChamaProfile(models.Model):
    ROLE_CHOICES = [
        ('member', 'Member'),
        ('treasurer', 'Treasurer'),
        ('chairperson', 'Chairperson'),
    ]

    phone_regex = RegexValidator(
        regex=r'^(254|0)[0-9]{9}$',
        message="Phone number must be in format: 254712345678 or 0712345678"
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='chama_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    phone_number = models.CharField(max_length=15, blank=True, null=True,
                                    validators=[phone_regex])
    id_number = models.CharField(max_length=20, blank=True, null=True)
    joined_date = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact = models.CharField(max_length=15, blank=True, null=True)
    occupation = models.CharField(max_length=100, blank=True, null=True)
    email_notifications = models.BooleanField(default=True)
    sms_notifications = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Chama Profile'
        verbose_name_plural = 'Chama Profiles'
        indexes = [
            models.Index(fields=['role']),
            models.Index(fields=['phone_number']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.role}"

    def is_treasurer(self):
        return self.role in ['treasurer', 'chairperson']

    def get_full_name(self):
        return self.user.get_full_name() or self.user.username

    def get_member_since_days(self):
        return (timezone.now().date() - self.joined_date.date()).days


# ===== BLOCKCHAIN TRANSACTION LOG =====

class BlockchainTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('contribution', 'Contribution'),
        ('loan_disbursement', 'Loan Disbursement'),
        ('repayment', 'Repayment'),
        ('withdrawal', 'Withdrawal'),
    ]
    STELLAR_NETWORK = 'testnet'

    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    reference_id = models.PositiveIntegerField()
    stellar_tx_hash = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blockchain_txs')
    memo = models.CharField(max_length=28, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Blockchain Transaction'
        verbose_name_plural = 'Blockchain Transactions'
        indexes = [
            models.Index(fields=['transaction_type', 'reference_id']),
            models.Index(fields=['stellar_tx_hash']),
            models.Index(fields=['member', '-created_at']),
        ]

    def __str__(self):
        return f"{self.get_transaction_type_display()} #{self.reference_id} — {self.stellar_tx_hash[:10]}..."

    def get_stellar_url(self):
        return f"https://stellar.expert/explorer/{self.STELLAR_NETWORK}/tx/{self.stellar_tx_hash}"

    def get_source_object(self):
        model_map = {
            'contribution': Contribution,
            'loan_disbursement': Loan,
            'repayment': Repayment,
            'withdrawal': Withdrawal,
        }
        model = model_map.get(self.transaction_type)
        if model:
            return model.objects.filter(pk=self.reference_id).first()
        return None

    def is_confirmed(self):
        return self.confirmed_at is not None


# ===== AUDIT LOG =====

class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('contribution', 'Contribution'),
        ('loan_apply', 'Loan Application'),
        ('loan_approve', 'Loan Approval'),
        ('loan_reject', 'Loan Rejection'),
        ('loan_disburse', 'Loan Disbursement'),
        ('repayment', 'Repayment'),
        ('withdrawal', 'Withdrawal'),
        ('profile_update', 'Profile Update'),
        ('stellar_record', 'Stellar Blockchain Record'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    content_type = models.CharField(max_length=50, blank=True)
    object_id = models.PositiveIntegerField(blank=True, null=True)
    object_repr = models.CharField(max_length=200, blank=True)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['action', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.action}"


# ===== UTILITY FUNCTIONS =====

def get_group_balance():
    """
    Group balance = total confirmed contributions
                  - total disbursed loan amounts
                  + total confirmed repayments
                  - total confirmed withdrawals
    """
    total_contributions = sum(
        c.amount for c in Contribution.objects.filter(status='confirmed')
    )
    total_disbursed_loans = sum(
        l.amount for l in Loan.objects.filter(disbursed=True)
    )
    total_repayments = sum(
        r.amount for r in Repayment.objects.filter(status='confirmed')
    )
    total_withdrawals = sum(
        w.amount for w in Withdrawal.objects.filter(status='confirmed')
    )
    return total_contributions - total_disbursed_loans + total_repayments - total_withdrawals


def get_member_balance(member):
    """
    Individual member balance = what they can withdraw.

    Formula:
        contributions (money put in)
      - disbursed loans (money already received from the chama)
      + repayments (loan debt paid back — increases available balance)
      - withdrawals (money already taken out)

    FIX: the original code had `+ member_disbursed_loans` which ADDED loan
    amounts to the balance, making members appear richer than they are and
    allowing them to withdraw money they don't have.
    """
    member_contributions = sum(
        c.amount for c in member.contributions.filter(status='confirmed')
    )
    member_disbursed_loans = sum(
        l.amount for l in member.loans.filter(disbursed=True)
    )
    member_repayments = sum(
        r.amount for r in Repayment.objects.filter(loan__member=member, status='confirmed')
    )
    member_withdrawals = sum(
        w.amount for w in member.withdrawals.filter(status='confirmed')
    )
    # FIXED: was `+ member_disbursed_loans` — loans must be SUBTRACTED
    return member_contributions - member_disbursed_loans + member_repayments - member_withdrawals


def get_member_contributions_balance(member):
    return sum(c.amount for c in member.contributions.filter(status='confirmed'))


def get_member_loans_balance(member):
    """Returns outstanding loan debt (positive = still owed)."""
    disbursed_loans = sum(l.amount for l in member.loans.filter(disbursed=True))
    repayments = sum(
        r.amount for r in Repayment.objects.filter(loan__member=member, status='confirmed')
    )
    return disbursed_loans - repayments


def get_member_withdrawals_total(member):
    return sum(w.amount for w in member.withdrawals.filter(status='confirmed'))


def get_dashboard_stats():
    """Overall dashboard statistics for treasurer."""
    return {
        'total_members': User.objects.filter(is_active=True).count(),
        'total_contributions': sum(c.amount for c in Contribution.objects.filter(status='confirmed')),
        'total_loans_disbursed': sum(l.amount for l in Loan.objects.filter(disbursed=True)),
        'total_repayments': sum(r.amount for r in Repayment.objects.filter(status='confirmed')),
        'pending_loans_count': Loan.objects.filter(status='pending').count(),
        'pending_contributions_count': Contribution.objects.filter(status='pending').count(),
        'group_balance': get_group_balance(),
        'stellar_recorded_contributions': Contribution.objects.exclude(stellar_tx_hash=None).count(),
        'stellar_recorded_loans': Loan.objects.exclude(stellar_tx_hash=None).count(),
        'stellar_recorded_repayments': Repayment.objects.exclude(stellar_tx_hash=None).count(),
        'stellar_recorded_withdrawals': Withdrawal.objects.exclude(stellar_tx_hash=None).count(),
        'total_blockchain_transactions': BlockchainTransaction.objects.count(),
    }