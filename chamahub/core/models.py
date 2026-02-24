from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal


class Contribution(models.Model):
    """
    Model to store member contributions to the chama.
    Each contribution is linked to a Payhero transaction for payment processing.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]
    
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payhero_reference = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-date']
        verbose_name = 'Contribution'
        verbose_name_plural = 'Contributions'
    
    def __str__(self):
        return f"{self.member.username} - {self.amount} ({self.status})"


class Loan(models.Model):
    """
    Model to store member loans from the chama.
    Includes approval and disbursement status tracking.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('disbursed', 'Disbursed'),
        ('rejected', 'Rejected'),
    ]
    
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='loans')
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    interest_rate = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=Decimal('10.00'),  # 10% default interest rate
        help_text="Interest rate as a percentage (e.g., 10.00 for 10%)"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved = models.BooleanField(default=False)
    disbursed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    disbursed_at = models.DateTimeField(blank=True, null=True)
    purpose = models.TextField(help_text="Purpose of the loan")
    repayment_period_months = models.PositiveIntegerField(
        default=12, 
        help_text="Repayment period in months"
    )
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Loan'
        verbose_name_plural = 'Loans'
    
    def __str__(self):
        return f"{self.member.username} - {self.amount} ({self.status})"
    
    def total_due(self):
        """
        Calculate total amount due including interest.
        Formula: Principal + (Principal * Interest Rate / 100)
        """
        interest_amount = (self.amount * self.interest_rate) / 100
        return self.amount + interest_amount
    
    def get_repayment_progress(self):
        """
        Calculate repayment progress as a percentage.
        Returns the percentage of total due amount that has been repaid.
        """
        total_repaid = sum(repayment.amount for repayment in self.repayments.filter(status='confirmed'))
        total_due = self.total_due()
        if total_due > 0:
            progress = (total_repaid / total_due) * 100
            return float(progress)  # Convert to float to avoid Decimal formatting issues
        return 0.0


class Repayment(models.Model):
    """
    Model to store loan repayments.
    Each repayment is linked to a Payhero transaction for payment processing.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]
    
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='repayments')
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payhero_reference = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-date']
        verbose_name = 'Repayment'
        verbose_name_plural = 'Repayments'
    
    def __str__(self):
        return f"{self.loan.member.username} - {self.amount} ({self.status})"


class ChamaProfile(models.Model):
    """
    Extended profile for chama members.
    Stores additional information about members and their roles.
    """
    ROLE_CHOICES = [
        ('member', 'Member'),
        ('treasurer', 'Treasurer'),
        ('chairperson', 'Chairperson'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='chama_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    id_number = models.CharField(max_length=20, blank=True, null=True)
    joined_date = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = 'Chama Profile'
        verbose_name_plural = 'Chama Profiles'
    
    def __str__(self):
        return f"{self.user.username} - {self.role}"
    
    def is_treasurer(self):
        """Check if user is a treasurer or chairperson (can approve loans)"""
        return self.role in ['treasurer', 'chairperson']


# Utility functions for group balance calculation
def get_group_balance():
    """
    Calculate total group balance = all confirmed contributions - disbursed loans
    """
    total_contributions = sum(
        contribution.amount 
        for contribution in Contribution.objects.filter(status='confirmed')
    )
    
    total_disbursed_loans = sum(
        loan.amount 
        for loan in Loan.objects.filter(disbursed=True)
    )
    
    return total_contributions - total_disbursed_loans


class Withdrawal(models.Model):
    """
    Model to store member withdrawals from their balance.
    Each withdrawal is linked to a Payhero transaction for payment processing.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]
    
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='withdrawals')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('1.00'))])
    date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payhero_reference = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-date']
        verbose_name = 'Withdrawal'
        verbose_name_plural = 'Withdrawals'
    
    def __str__(self):
        return f"{self.member.username} - {self.amount} ({self.status})"


def get_member_balance(member):
    """
    Calculate individual member's balance = contributions + disbursed loans - repayments - withdrawals
    """
    member_contributions = sum(
        contribution.amount 
        for contribution in member.contributions.filter(status='confirmed')
    )
    
    # Disbursed loans are credited to balance (money received)
    member_disbursed_loans = sum(
        loan.amount 
        for loan in member.loans.filter(disbursed=True)
    )
    
    # Repayments are debited from balance (money paid back)
    member_repayments = sum(
        repayment.amount 
        for repayment in Repayment.objects.filter(loan__member=member, status='confirmed')
    )
    
    # Withdrawals are debited from balance (money withdrawn)
    member_withdrawals = sum(
        withdrawal.amount 
        for withdrawal in member.withdrawals.filter(status='confirmed')
    )
    
    return member_contributions + member_disbursed_loans - member_repayments - member_withdrawals


def get_member_contributions_balance(member):
    """
    Calculate member's contributions balance only
    """
    return sum(
        contribution.amount 
        for contribution in member.contributions.filter(status='confirmed')
    )


def get_member_loans_balance(member):
    """
    Calculate member's net loan balance (disbursed loans - repayments)
    """
    disbursed_loans = sum(
        loan.amount 
        for loan in member.loans.filter(disbursed=True)
    )
    
    repayments = sum(
        repayment.amount 
        for repayment in Repayment.objects.filter(loan__member=member, status='confirmed')
    )
    
    return disbursed_loans - repayments