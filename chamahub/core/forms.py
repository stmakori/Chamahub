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
                'min': '0.01',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Optional notes about this contribution',
                'rows': 3,
            }),
        }
        labels = {
            'amount': 'Contribution Amount (KSh)',
            'notes': 'Notes (Optional)',
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
                'min': '0.01',
            }),
            'purpose': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Describe the purpose of this loan',
                'rows': 3,
            }),
            'repayment_period_months': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Repayment period in months',
                'min': '1',
                'max': '60',
            }),
            'interest_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Interest rate percentage',
                'step': '0.01',
                'min': '0.01',
                'max': '50.00',
            }),
        }
        labels = {
            'amount': 'Loan Amount (KSh)',
            'purpose': 'Purpose of Loan',
            'repayment_period_months': 'Repayment Period (Months)',
            'interest_rate': 'Interest Rate (%)',
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
                'min': '0.01',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Optional notes about this repayment',
                'rows': 3,
            }),
        }
        labels = {
            'amount': 'Repayment Amount (KSh)',
            'notes': 'Notes (Optional)',
        }


class WithdrawalForm(forms.ModelForm):
    """Form for member withdrawals"""

    class Meta:
        model = Withdrawal
        fields = ['amount', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={
                # FIX 1: form-control-lg added to match the input-group-lg wrapper in
                # the template. Without it Bootstrap's sizing is inconsistent and the
                # submit button can appear detached or unclickable on some browsers.
                'class': 'form-control form-control-lg',
                'step': '0.01',
                'min': '1.00',
                'placeholder': '0.00',
                'id': 'id_amount',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional notes about this withdrawal (e.g. Emergency)',
            }),
        }
        labels = {
            'amount': 'Withdrawal Amount (KSh)',
            'notes': 'Notes (Optional)',
        }

    def __init__(self, *args, **kwargs):
        # member is injected by the view: WithdrawalForm(request.POST, member=user)
        self.member = kwargs.pop('member', None)
        super().__init__(*args, **kwargs)

        if self.member:
            from .models import get_member_balance
            current_balance = get_member_balance(self.member)

            # Clamp the browser-side max to the available balance.
            # This is a UX hint only — clean_amount() enforces it server-side.
            safe_balance = max(current_balance, Decimal('0.00'))
            self.fields['amount'].widget.attrs['max'] = str(safe_balance)

            # FIX 2: Guard against negative balance being shown in the help text.
            # If the member has outstanding loans that exceed contributions the
            # balance can be negative; showing "Max: KSh -500" is confusing.
            if current_balance > 0:
                self.fields['amount'].help_text = (
                    f"Available balance: KSh {current_balance:.2f}"
                )
            else:
                self.fields['amount'].help_text = (
                    "You have no available balance for withdrawal."
                )

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')

        if amount is None:
            raise forms.ValidationError("Please enter a withdrawal amount.")

        # FIX 3: Enforce minimum independently so the error message is specific.
        if amount < Decimal('1.00'):
            raise forms.ValidationError("Minimum withdrawal amount is KSh 1.00.")

        if self.member:
            from .models import get_member_balance
            current_balance = get_member_balance(self.member)

            # FIX 4: If balance is zero or negative, block with a clear message
            # rather than an arithmetic comparison that looks nonsensical.
            if current_balance <= 0:
                raise forms.ValidationError(
                    "You have no available balance to withdraw. "
                    "Make contributions or repay your loans to build a positive balance."
                )

            if amount > current_balance:
                raise forms.ValidationError(
                    f"Amount exceeds your available balance. "
                    f"You can withdraw up to KSh {current_balance:.2f}."
                )

        return amount