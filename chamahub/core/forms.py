from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from decimal import Decimal
from .models import Contribution, Loan, Repayment, Withdrawal


class UserRegistrationForm(UserCreationForm):
    """Custom user registration form"""
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=True)
    
    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        if commit:
            user.save()
        return user


class ContributionForm(forms.ModelForm):
    """Form for making contributions"""
    
    class Meta:
        model = Contribution
        fields = ['amount', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter contribution amount',
                'step': '0.01',
                'min': '0.01'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Optional notes about this contribution',
                'rows': 3
            })
        }
        labels = {
            'amount': 'Contribution Amount (KSh)',
            'notes': 'Notes (Optional)'
        }


class LoanForm(forms.ModelForm):
    """Form for applying for loans"""
    
    class Meta:
        model = Loan
        fields = ['amount', 'purpose', 'repayment_period_months', 'interest_rate']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter loan amount',
                'step': '0.01',
                'min': '0.01'
            }),
            'purpose': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Describe the purpose of this loan',
                'rows': 3
            }),
            'repayment_period_months': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Repayment period in months',
                'min': '1',
                'max': '60'
            }),
            'interest_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Interest rate percentage',
                'step': '0.01',
                'min': '0.01',
                'max': '50.00'
            })
        }
        labels = {
            'amount': 'Loan Amount (KSh)',
            'purpose': 'Purpose of Loan',
            'repayment_period_months': 'Repayment Period (Months)',
            'interest_rate': 'Interest Rate (%)'
        }


class RepaymentForm(forms.ModelForm):
    """Form for making loan repayments"""
    
    class Meta:
        model = Repayment
        fields = ['amount', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter repayment amount',
                'step': '0.01',
                'min': '0.01'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Optional notes about this repayment',
                'rows': 3
            })
        }
        labels = {
            'amount': 'Repayment Amount (KSh)',
            'notes': 'Notes (Optional)'
        }


class WithdrawalForm(forms.ModelForm):
    """Form for member withdrawals"""
    
    class Meta:
        model = Withdrawal
        fields = ['amount', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '1.00',
                'placeholder': 'Enter withdrawal amount'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional notes about this withdrawal'
            })
        }
        labels = {
            'amount': 'Withdrawal Amount (KSh)',
            'notes': 'Notes (Optional)'
        }
    
    def __init__(self, *args, **kwargs):
        self.member = kwargs.pop('member', None)
        super().__init__(*args, **kwargs)
        
        if self.member:
            # Get member's current balance
            from .models import get_member_balance
            current_balance = get_member_balance(self.member)
            
            # Set max amount to current balance
            self.fields['amount'].widget.attrs['max'] = str(current_balance)
            self.fields['amount'].help_text = f"Maximum withdrawal: KSh {current_balance:.2f}"
    
    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if self.member and amount:
            from .models import get_member_balance
            current_balance = get_member_balance(self.member)
            
            if amount > current_balance:
                raise forms.ValidationError(f"Insufficient balance. Available: KSh {current_balance:.2f}")
            
            if amount < 1:
                raise forms.ValidationError("Minimum withdrawal amount is KSh 1.00")
        
        return amount
