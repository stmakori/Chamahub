from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Sum, Q
from django.db.models.functions import TruncMonth
from django.conf import settings
from decimal import Decimal
import json
import requests
import csv
import hmac
import hashlib
import logging
from datetime import timedelta

from .models import Contribution, Loan, Repayment, ChamaProfile, Withdrawal
from .models import (
    get_group_balance, get_member_balance,
    get_member_contributions_balance, get_member_loans_balance
)
from .forms import (
    ContributionForm, LoanForm, RepaymentForm,
    UserRegistrationForm, WithdrawalForm
)

logger = logging.getLogger(__name__)


def _get_stellar_recorder():
    if not getattr(settings, 'STELLAR_ENABLED', False):
        return None
    try:
        from core.utils.stellar_recorder import StellarRecorder
        recorder = StellarRecorder()
        return recorder if recorder.enabled else None
    except Exception as e:
        logger.error(f"Could not load StellarRecorder: {e}")
        return None


def home(request):
    return render(request, 'core/home.html')


def register(request):
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            ChamaProfile.objects.create(user=user)
            messages.success(request, f'Account created for {form.cleaned_data.get("username")}!')
            return redirect('login')
    else:
        form = UserRegistrationForm()
    return render(request, 'core/register.html', {'form': form})


@login_required
def dashboard(request):
    try:
        profile = request.user.chama_profile
        if request.user.is_superuser or request.user.is_staff:
            if profile.role not in ['treasurer', 'chairperson']:
                profile.role = 'treasurer'
                profile.save()
        is_treasurer = profile.is_treasurer()
    except ChamaProfile.DoesNotExist:
        if request.user.is_superuser or request.user.is_staff:
            profile = ChamaProfile.objects.create(user=request.user, role='treasurer')
            is_treasurer = True
        else:
            profile = ChamaProfile.objects.create(user=request.user, role='member')
            is_treasurer = False

    if is_treasurer:
        return treasurer_dashboard(request)
    return member_dashboard(request)


def member_dashboard(request):
    user = request.user

    contributions = user.contributions.all().order_by('-date')[:10]
    loans = user.loans.all().order_by('-created_at')[:10]
    repayments = Repayment.objects.filter(loan__member=user).order_by('-date')[:10]

    member_balance = get_member_balance(user)
    contributions_balance = get_member_contributions_balance(user)
    loans_balance = get_member_loans_balance(user)
    total_contributions = sum(c.amount for c in user.contributions.filter(status='confirmed'))
    total_loans = sum(l.amount for l in user.loans.filter(disbursed=True))

    active_loans = user.loans.filter(disbursed=True)
    for loan in active_loans:
        loan.repayment_progress = loan.get_repayment_progress()
        loan.paid_amount = sum(r.amount for r in loan.repayments.filter(status='confirmed'))
        loan.remaining_amount = loan.total_due() - loan.paid_amount
        if loan.remaining_amount < 0:
            loan.remaining_amount = Decimal('0.00')
        loan.is_fully_repaid = loan.remaining_amount <= 0

    pending_contributions = user.contributions.filter(status='pending').count()
    pending_repayments = Repayment.objects.filter(loan__member=user, status='pending').count()
    pending_payments = pending_contributions + pending_repayments

    current_date = timezone.now()
    six_months_ago = timezone.now() - timedelta(days=180)

    monthly_contributions = (
        Contribution.objects
        .filter(member=user, status='confirmed', date__gte=six_months_ago)
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum('amount'))
        .order_by('month')
    )
    monthly_repayments = (
        Repayment.objects
        .filter(loan__member=user, status='confirmed', date__gte=six_months_ago)
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum('amount'))
        .order_by('month')
    )

    contrib_dict = {
        item['month'].strftime('%b'): float(item['total'])
        for item in monthly_contributions if item['month']
    }
    repay_dict = {
        item['month'].strftime('%b'): float(item['total'])
        for item in monthly_repayments if item['month']
    }

    chart_labels, chart_contributions, chart_repayments = [], [], []
    for i in range(5, -1, -1):
        month = (timezone.now() - timedelta(days=30 * i)).strftime('%b')
        chart_labels.append(month)
        chart_contributions.append(contrib_dict.get(month, 0))
        chart_repayments.append(repay_dict.get(month, 0))

    group_total_contributions = sum(
        c.amount for c in Contribution.objects.filter(status='confirmed')
    )
    group_balance = get_group_balance()

    all_members = []
    for m in (
        User.objects
        .filter(is_active=True)
        .select_related('chama_profile')
        .order_by('first_name', 'username')
    ):
        try:
            profile = m.chama_profile
        except Exception:
            continue
        member_contrib_total = sum(
            c.amount for c in m.contributions.filter(status='confirmed')
        )
        all_members.append({
            'user': m,
            'profile': profile,
            'display_name': m.get_full_name() or m.username,
            'role': profile.role,
            'total_contributions': member_contrib_total,
            'is_current_user': m == user,
        })
    all_members.sort(key=lambda x: (not x['is_current_user'], -float(x['total_contributions'])))

    context = {
        'contributions': contributions,
        'loans': loans,
        'repayments': repayments,
        'member_balance': member_balance,
        'contributions_balance': contributions_balance,
        'loans_balance': loans_balance,
        'total_contributions': total_contributions,
        'total_loans': total_loans,
        'active_loans': active_loans,
        'pending_payments': pending_payments,
        'current_date': current_date,
        'chart_labels': json.dumps(chart_labels),
        'chart_contributions': json.dumps(chart_contributions),
        'chart_repayments': json.dumps(chart_repayments),
        'group_total_contributions': group_total_contributions,
        'group_balance': group_balance,
        'all_members': all_members,
    }
    return render(request, 'core/member_dashboard.html', context)


def treasurer_dashboard(request):
    group_balance = get_group_balance()
    total_contributions = sum(c.amount for c in Contribution.objects.filter(status='confirmed'))
    total_disbursed_loans = sum(l.amount for l in Loan.objects.filter(disbursed=True))
    pending_loans = Loan.objects.filter(status='pending').order_by('-created_at')

    pending_contributions_count = Contribution.objects.filter(status='pending').count()
    pending_repayments_count = Repayment.objects.filter(status='pending').count()
    pending_withdrawals_count = Withdrawal.objects.filter(status='pending').count()
    total_pending_payments = (
        pending_contributions_count + pending_repayments_count + pending_withdrawals_count
    )

    recent_contributions = Contribution.objects.filter(status='confirmed').order_by('-date')[:10]
    recent_repayments = Repayment.objects.filter(status='confirmed').order_by('-date')[:10]

    members = User.objects.filter(is_active=True)
    member_balances = []
    total_members = total_positive = total_negative = 0

    for member in members:
        try:
            profile = member.chama_profile
            balance = get_member_balance(member)
            member_balances.append({'member': member, 'balance': balance, 'role': profile.role})
            total_members += 1
            if balance >= 0:
                total_positive += 1
            else:
                total_negative += 1
        except ChamaProfile.DoesNotExist:
            continue

    context = {
        'group_balance': group_balance,
        'total_contributions': total_contributions,
        'total_disbursed_loans': total_disbursed_loans,
        'pending_loans': pending_loans,
        'recent_contributions': recent_contributions,
        'recent_repayments': recent_repayments,
        'member_balances': member_balances,
        'total_members': total_members,
        'total_positive': total_positive,
        'total_negative': total_negative,
        'current_date': timezone.now(),
        'total_loans_active': Loan.objects.filter(status='disbursed').count(),
        'total_contributions_count': Contribution.objects.filter(status='confirmed').count(),
        'total_pending_payments': total_pending_payments,
    }
    return render(request, 'core/treasurer_dashboard.html', context)


@login_required
def make_contribution(request):
    user = request.user
    if request.method == 'POST':
        form = ContributionForm(request.POST)
        if form.is_valid():
            contribution = form.save(commit=False)
            contribution.member = user
            contribution.save()
            return redirect('initiate_payment', payment_type='contribution', reference_id=contribution.id)
    else:
        form = ContributionForm()
    return render(request, 'core/make_contribution.html', {
        'form': form,
        'current_balance': get_member_balance(user),
        'recent_contributions': user.contributions.all().order_by('-date')[:5],
    })


@login_required
def apply_for_loan(request):
    user = request.user
    contributions_count = user.contributions.filter(status='confirmed').count()
    total_contributions = sum(c.amount for c in user.contributions.filter(status='confirmed'))
    is_eligible = contributions_count >= 3

    if request.method == 'POST':
        form = LoanForm(request.POST)
        if not is_eligible:
            messages.error(request, f'You need at least 3 confirmed contributions. You have {contributions_count}.')
            return render(request, 'core/apply_loan.html', {
                'form': form, 'contributions_count': contributions_count,
                'total_contributions': total_contributions, 'is_eligible': is_eligible,
            })
        if form.is_valid():
            loan = form.save(commit=False)
            loan.member = user
            loan.save()
            messages.success(request, 'Loan application submitted for approval!')
            return redirect('dashboard')
        messages.error(request, 'Please correct the errors below.')
    else:
        form = LoanForm()

    return render(request, 'core/apply_loan.html', {
        'form': form, 'contributions_count': contributions_count,
        'total_contributions': total_contributions, 'is_eligible': is_eligible,
    })


@login_required
def make_repayment(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id, member=request.user)
    total_due = loan.total_due()
    paid_amount = sum(r.amount for r in loan.repayments.filter(status='confirmed'))
    remaining_amount = total_due - paid_amount

    if request.method == 'POST':
        form = RepaymentForm(request.POST)
        if form.is_valid():
            repayment = form.save(commit=False)
            repayment.loan = loan
            repayment.save()
            return redirect('initiate_payment', payment_type='repayment', reference_id=repayment.id)
    else:
        form = RepaymentForm()

    return render(request, 'core/make_repayment.html', {
        'form': form, 'loan': loan, 'total_due': total_due,
        'repayment_progress': loan.get_repayment_progress(),
        'paid_amount': paid_amount, 'remaining_amount': remaining_amount,
        'recent_repayments': loan.repayments.all().order_by('-date')[:5],
    })


@login_required
def make_withdrawal(request):
    user = request.user
    current_balance = get_member_balance(user)
    has_phone = False
    try:
        has_phone = bool(user.chama_profile.phone_number)
    except Exception:
        pass

    if request.method == 'POST':
        form = WithdrawalForm(request.POST, member=user)
        if form.is_valid():
            withdrawal = form.save(commit=False)
            withdrawal.member = user
            withdrawal.save()
            return redirect('initiate_payment', payment_type='withdrawal', reference_id=withdrawal.id)
    else:
        form = WithdrawalForm(member=user)

    return render(request, 'core/make_withdrawal.html', {
        'form': form,
        'current_balance': current_balance,
        'contributions_balance': get_member_contributions_balance(user),
        'loans_balance': get_member_loans_balance(user),
        'recent_withdrawals': user.withdrawals.all().order_by('-date')[:10],
        'total_withdrawn': sum(w.amount for w in user.withdrawals.filter(status='confirmed')),
        'pending_count': user.withdrawals.filter(status='pending').count(),
        'confirmed_count': user.withdrawals.filter(status='confirmed').count(),
        'has_phone': has_phone,
    })


@login_required
def approve_loan(request, loan_id):
    logger.debug(f"approve_loan | loan_id={loan_id} | method={request.method}")

    try:
        profile = request.user.chama_profile
        if not profile.is_treasurer():
            messages.error(request, 'You do not have permission to approve loans.')
            return redirect('dashboard')
    except ChamaProfile.DoesNotExist:
        messages.error(request, 'Profile not found.')
        return redirect('dashboard')

    loan = get_object_or_404(Loan, id=loan_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve':
            loan.status = 'approved'
            loan.approved = True
            loan.approved_at = timezone.now()
            loan.approved_by = request.user
            loan.save()
            messages.success(request, f'Loan #{loan.id} for {loan.member.username} approved!')
            logger.info(f"Loan {loan_id} approved by {request.user.username}")
            return redirect('dashboard')

        elif action == 'reject':
            loan.status = 'rejected'
            loan.save()
            messages.warning(request, f'Loan #{loan.id} for {loan.member.username} rejected.')
            logger.info(f"Loan {loan_id} rejected by {request.user.username}")
            return redirect('dashboard')

        elif action == 'disburse':
            loan.status = 'disbursed'
            loan.disbursed = True
            loan.disbursed_at = timezone.now()
            loan.save()
            logger.info(f"Loan {loan_id} disbursed by {request.user.username}")

            recorder = _get_stellar_recorder()
            if recorder:
                tx_hash = recorder.record_loan_disbursement(loan)
                if tx_hash:
                    messages.success(
                        request,
                        f'Loan #{loan.id} disbursed and recorded on blockchain! TX: {tx_hash[:10]}...'
                    )
                else:
                    messages.warning(request, f'Loan #{loan.id} disbursed but blockchain recording failed.')
            else:
                messages.success(request, f'Loan #{loan.id} for {loan.member.username} disbursed!')
            return redirect('dashboard')

        else:
            messages.error(request, 'Invalid action.')
            return redirect('dashboard')

    # GET
    contributions = loan.member.contributions.filter(status='confirmed').order_by('-date')
    total_contributions = sum(c.amount for c in contributions)
    previous_loans = loan.member.loans.exclude(id=loan_id).filter(disbursed=True)
    member_tenure = (timezone.now().date() - loan.member.date_joined.date()).days // 30

    return render(request, 'core/approve_loan.html', {
        'loan': loan,
        'total_due': loan.total_due(),
        'contributions': contributions,
        'total_contributions': total_contributions,
        'contribution_count': contributions.count(),
        'previous_loans': previous_loans,
        'active_loans_count': loan.member.loans.filter(disbursed=True).count(),
        'repayment_rate': 100,
        'member_tenure': member_tenure,
    })


@login_required
def update_phone(request):
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number', '').strip().replace(' ', '')
        if not phone_number:
            messages.error(request, 'Phone number is required.')
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))
        if not (phone_number.startswith('0') or phone_number.startswith('254')):
            messages.error(request, 'Phone number must start with 0 or 254')
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        try:
            profile = request.user.chama_profile
            profile.phone_number = phone_number
            profile.save()
        except ChamaProfile.DoesNotExist:
            ChamaProfile.objects.create(user=request.user, phone_number=phone_number, role='member')
        messages.success(request, 'Phone number updated successfully!')
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))


@login_required
def payment_status(request):
    user = request.user
    contributions = user.contributions.all().order_by('-date')
    repayments = Repayment.objects.filter(loan__member=user).order_by('-date')

    pending_contributions = contributions.filter(status='pending')
    confirmed_contributions = contributions.filter(status='confirmed')
    failed_contributions = contributions.filter(status='failed')
    pending_repayments = repayments.filter(status='pending')
    confirmed_repayments = repayments.filter(status='confirmed')
    failed_repayments = repayments.filter(status='failed')

    return render(request, 'core/payment_status.html', {
        'contributions': contributions, 'repayments': repayments,
        'pending_contributions': pending_contributions,
        'confirmed_contributions': confirmed_contributions,
        'failed_contributions': failed_contributions,
        'pending_repayments': pending_repayments,
        'confirmed_repayments': confirmed_repayments,
        'failed_repayments': failed_repayments,
        'total_pending': pending_contributions.count() + pending_repayments.count(),
        'confirmed_count': confirmed_contributions.count() + confirmed_repayments.count(),
        'failed_count': failed_contributions.count() + failed_repayments.count(),
        'pending_contributions_count': pending_contributions.count(),
        'confirmed_contributions_count': confirmed_contributions.count(),
        'failed_contributions_count': failed_contributions.count(),
        'pending_repayments_count': pending_repayments.count(),
        'confirmed_repayments_count': confirmed_repayments.count(),
        'failed_repayments_count': failed_repayments.count(),
        'total_contributions': sum(c.amount for c in confirmed_contributions),
        'total_repayments': sum(r.amount for r in confirmed_repayments),
    })


# ------------------------------------------------------------------ #
#  Treasurer payment management — FIXED                                #
# ------------------------------------------------------------------ #

@login_required
def treasurer_payments(request):
    """
    Treasurer payment management page.

    KEY FIX: pending_withdrawals is now a list of dicts that includes each
    withdrawal's member balance and an `is_affordable` flag. The treasurer
    template should use these to show a warning badge next to any withdrawal
    where the member's balance has dropped below the requested amount since
    the request was made.
    """
    try:
        profile = request.user.chama_profile
        if not profile.is_treasurer():
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('dashboard')
    except ChamaProfile.DoesNotExist:
        messages.error(request, 'Profile not found.')
        return redirect('dashboard')

    pending_contributions = Contribution.objects.filter(status='pending').order_by('-date')
    pending_repayments = Repayment.objects.filter(status='pending').order_by('-date')

    # Annotate each pending withdrawal with the member's current live balance.
    # The treasurer needs to know whether a withdrawal can safely be approved.
    raw_withdrawals = Withdrawal.objects.filter(status='pending').order_by('-date').select_related('member')
    pending_withdrawals = []
    for w in raw_withdrawals:
        current_balance = get_member_balance(w.member)
        pending_withdrawals.append({
            'withdrawal': w,
            'member_balance': current_balance,
            'is_affordable': current_balance >= w.amount,
        })

    recent_contributions = Contribution.objects.filter(status='confirmed').order_by('-date')[:10]
    recent_repayments = Repayment.objects.filter(status='confirmed').order_by('-date')[:10]
    recent_withdrawals = Withdrawal.objects.filter(status='confirmed').order_by('-date')[:10]

    recent_confirmations = []
    for c in recent_contributions:
        recent_confirmations.append({
            'date': c.date, 'type': 'Contribution',
            'member': c.member.get_full_name() or c.member.username,
            'amount': c.amount, 'status': c.status,
            'icon': 'piggy-bank', 'color': 'primary',
        })
    for r in recent_repayments:
        recent_confirmations.append({
            'date': r.date, 'type': 'Repayment',
            'member': r.loan.member.get_full_name() or r.loan.member.username,
            'amount': r.amount, 'status': r.status,
            'icon': 'credit-card', 'color': 'success',
        })
    for w in recent_withdrawals:
        recent_confirmations.append({
            'date': w.date, 'type': 'Withdrawal',
            'member': w.member.get_full_name() or w.member.username,
            'amount': w.amount, 'status': w.status,
            'icon': 'money-bill-wave', 'color': 'warning',
        })
    recent_confirmations.sort(key=lambda x: x['date'], reverse=True)

    return render(request, 'core/treasurer_payments.html', {
        'pending_contributions': pending_contributions,
        'pending_repayments': pending_repayments,
        'pending_withdrawals': pending_withdrawals,        # list of dicts now
        'recent_confirmations': recent_confirmations[:15],
        'total_pending': (
            pending_contributions.count() +
            pending_repayments.count() +
            len(pending_withdrawals)
        ),
    })


@login_required
def confirm_payment(request, payment_type, payment_id):
    """
    Confirm a single pending payment.

    KEY FIX (withdrawals): before confirming, verify the member's current
    balance covers the withdrawal amount. If not, mark it failed and tell
    the treasurer why — don't silently overdraw the member's account.
    """
    try:
        profile = request.user.chama_profile
        if not profile.is_treasurer():
            messages.error(request, 'You do not have permission to confirm payments.')
            return redirect('dashboard')
    except ChamaProfile.DoesNotExist:
        messages.error(request, 'Profile not found.')
        return redirect('dashboard')

    if request.method != 'POST':
        return redirect('treasurer_payments')

    try:
        recorder = _get_stellar_recorder()

        if payment_type == 'contribution':
            payment = get_object_or_404(Contribution, id=payment_id)
            payment.status = 'confirmed'
            payment.save()
            if recorder:
                recorder.record_contribution(payment)
            messages.success(
                request,
                f'Contribution of KSh {payment.amount} confirmed for '
                f'{payment.member.get_full_name() or payment.member.username}!'
            )

        elif payment_type == 'repayment':
            payment = get_object_or_404(Repayment, id=payment_id)
            payment.status = 'confirmed'
            payment.save()
            if recorder:
                recorder.record_repayment(payment)
            messages.success(
                request,
                f'Repayment of KSh {payment.amount} confirmed for '
                f'{payment.loan.member.get_full_name() or payment.loan.member.username}!'
            )

        elif payment_type == 'withdrawal':
            payment = get_object_or_404(Withdrawal, id=payment_id)

            # Balance check — get_member_balance excludes pending withdrawals,
            # so this is the true available balance right now.
            current_balance = get_member_balance(payment.member)
            member_name = payment.member.get_full_name() or payment.member.username

            if current_balance < payment.amount:
                payment.status = 'failed'
                payment.save()
                messages.error(
                    request,
                    f'Withdrawal of KSh {payment.amount} for {member_name} REJECTED — '
                    f'their current balance is KSh {current_balance:.2f}, '
                    f'which is less than the requested amount. '
                    f'The withdrawal has been marked as failed.'
                )
                logger.warning(
                    f"Withdrawal {payment_id} rejected — "
                    f"balance {current_balance} < amount {payment.amount} "
                    f"for {payment.member.username}"
                )
            else:
                payment.status = 'confirmed'
                payment.save()
                if recorder:
                    recorder.record_withdrawal(payment)
                messages.success(
                    request,
                    f'Withdrawal of KSh {payment.amount} confirmed for {member_name}!'
                )

        else:
            messages.error(request, 'Invalid payment type.')

    except Exception as e:
        logger.error(f"confirm_payment error | type={payment_type} id={payment_id}: {e}")
        messages.error(request, f'Error confirming payment: {str(e)}')

    return redirect('treasurer_payments')


@login_required
def bulk_confirm_payments(request):
    """
    Bulk confirm multiple payments.

    KEY FIX (withdrawals): each withdrawal is validated individually. If the
    member's balance is insufficient, the withdrawal is marked failed rather
    than confirmed. The treasurer gets a clear summary of what passed and what
    failed, and why.
    """
    try:
        profile = request.user.chama_profile
        if not profile.is_treasurer():
            messages.error(request, 'You do not have permission to confirm payments.')
            return redirect('dashboard')
    except ChamaProfile.DoesNotExist:
        messages.error(request, 'Profile not found.')
        return redirect('dashboard')

    if request.method != 'POST':
        return redirect('treasurer_payments')

    payment_ids = request.POST.getlist('payment_ids')
    payment_type = request.POST.get('payment_type')
    confirmed_count = 0
    failed_count = 0
    stellar_count = 0

    try:
        recorder = _get_stellar_recorder()

        if payment_type == 'contributions':
            for pid in payment_ids:
                try:
                    payment = Contribution.objects.get(id=pid, status='pending')
                    payment.status = 'confirmed'
                    payment.save()
                    confirmed_count += 1
                    if recorder and recorder.record_contribution(payment):
                        stellar_count += 1
                except Contribution.DoesNotExist:
                    logger.warning(f"bulk_confirm: Contribution {pid} not found or not pending")

        elif payment_type == 'repayments':
            for pid in payment_ids:
                try:
                    payment = Repayment.objects.get(id=pid, status='pending')
                    payment.status = 'confirmed'
                    payment.save()
                    confirmed_count += 1
                    if recorder and recorder.record_repayment(payment):
                        stellar_count += 1
                except Repayment.DoesNotExist:
                    logger.warning(f"bulk_confirm: Repayment {pid} not found or not pending")

        elif payment_type == 'withdrawals':
            # Process oldest-first so earlier requests get priority
            # if a member has multiple pending withdrawals.
            qs = Withdrawal.objects.filter(id__in=payment_ids, status='pending').order_by('date')
            for payment in qs:
                current_balance = get_member_balance(payment.member)

                if current_balance < payment.amount:
                    payment.status = 'failed'
                    payment.save()
                    failed_count += 1
                    logger.warning(
                        f"bulk_confirm: Withdrawal {payment.id} failed — "
                        f"balance {current_balance} < amount {payment.amount} "
                        f"for {payment.member.username}"
                    )
                else:
                    payment.status = 'confirmed'
                    payment.save()
                    confirmed_count += 1
                    if recorder and recorder.record_withdrawal(payment):
                        stellar_count += 1

        # Build summary message
        parts = [f'Confirmed {confirmed_count} payment(s).']
        if failed_count:
            parts.append(
                f'{failed_count} withdrawal(s) failed due to insufficient member balance '
                f'and have been marked as failed.'
            )
        if stellar_count:
            parts.append(f'{stellar_count} recorded on blockchain.')

        if failed_count:
            messages.warning(request, ' '.join(parts))
        else:
            messages.success(request, ' '.join(parts))

    except Exception as e:
        logger.error(f"bulk_confirm_payments error | type={payment_type}: {e}")
        messages.error(request, f'Error confirming payments: {str(e)}')

    return redirect('treasurer_payments')


@login_required
def export_csv(request):
    if not request.user.chama_profile.is_treasurer():
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="chama_transactions.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Type', 'Member', 'Amount', 'Status', 'Reference', 'Stellar TX'])

    for c in Contribution.objects.filter(status='confirmed').select_related('member'):
        writer.writerow([
            c.date.strftime('%Y-%m-%d %H:%M'), 'Contribution',
            c.member.username, f'{c.amount:.2f}', c.status,
            c.payhero_reference or '', c.stellar_tx_hash or '',
        ])
    for r in Repayment.objects.filter(status='confirmed').select_related('loan__member'):
        writer.writerow([
            r.date.strftime('%Y-%m-%d %H:%M'), 'Repayment',
            r.loan.member.username, f'{r.amount:.2f}', r.status,
            r.payhero_reference or '', r.stellar_tx_hash or '',
        ])
    for l in Loan.objects.filter(disbursed=True).select_related('member'):
        writer.writerow([
            l.disbursed_at.strftime('%Y-%m-%d %H:%M') if l.disbursed_at else '',
            'Loan Disbursement', l.member.username, f'{l.amount:.2f}',
            'disbursed', '', l.stellar_tx_hash or '',
        ])
    return response


@login_required
def export_pdf(request):
    messages.info(request, 'PDF export coming soon. Using CSV for now.')
    return redirect('export_csv')


@login_required
def transaction_details(request, transaction_type, transaction_id):
    try:
        if transaction_type == 'contribution':
            tx = Contribution.objects.get(id=transaction_id, member=request.user)
            data = {
                'id': tx.id, 'type': 'contribution',
                'amount': float(tx.amount), 'status': tx.status,
                'status_color': 'success' if tx.status == 'confirmed' else 'warning' if tx.status == 'pending' else 'danger',
                'icon': 'piggy-bank',
                'date': tx.date.strftime('%b %d, %Y %H:%M'),
                'reference': tx.payhero_reference, 'notes': tx.notes,
                'stellar_tx_hash': tx.stellar_tx_hash, 'stellar_url': tx.get_stellar_url(),
            }
        elif transaction_type == 'repayment':
            tx = Repayment.objects.get(id=transaction_id, loan__member=request.user)
            data = {
                'id': tx.id, 'type': 'repayment',
                'amount': float(tx.amount), 'status': tx.status,
                'status_color': 'success' if tx.status == 'confirmed' else 'warning' if tx.status == 'pending' else 'danger',
                'icon': 'credit-card',
                'date': tx.date.strftime('%b %d, %Y %H:%M'),
                'reference': tx.payhero_reference, 'notes': tx.notes,
                'stellar_tx_hash': tx.stellar_tx_hash, 'stellar_url': tx.get_stellar_url(),
            }
        else:
            return JsonResponse({'error': 'Invalid transaction type'}, status=400)
        return JsonResponse(data)
    except (Contribution.DoesNotExist, Repayment.DoesNotExist):
        return JsonResponse({'error': 'Transaction not found'}, status=404)
    except Exception as e:
        logger.error(f"transaction_details error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def payhero_webhook(request):
    try:
        payload = request.body
        signature = request.headers.get('X-Payhero-Signature', '')
        webhook_secret = getattr(settings, 'PAYHERO_WEBHOOK_SECRET', '')
        if webhook_secret and webhook_secret != 'your_webhook_secret_here':
            expected_signature = hmac.new(
                webhook_secret.encode(), payload, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                return JsonResponse({'error': 'Invalid signature'}, status=400)

        data = json.loads(payload)
        response_data = data.get('response', {})
        external_reference = response_data.get('ExternalReference')
        status = response_data.get('Status')
        result_code = response_data.get('ResultCode')
        mpesa_receipt = response_data.get('MpesaReceiptNumber')

        logger.info(f"Payhero webhook | ref={external_reference} status={status} code={result_code}")

        payment_succeeded = (status == 'Success' and result_code == 0)
        recorder = _get_stellar_recorder()

        try:
            contribution = Contribution.objects.get(payhero_reference=external_reference)
            if payment_succeeded:
                contribution.status = 'confirmed'
                contribution.save()
                if recorder:
                    recorder.record_contribution(contribution)
                return JsonResponse({'status': 'success', 'message': 'Contribution confirmed'})
            else:
                contribution.status = 'failed'
                contribution.save()
                return JsonResponse({'status': 'failed', 'message': 'Contribution failed'})
        except Contribution.DoesNotExist:
            pass

        try:
            repayment = Repayment.objects.get(payhero_reference=external_reference)
            if payment_succeeded:
                repayment.status = 'confirmed'
                repayment.save()
                if recorder:
                    recorder.record_repayment(repayment)
                return JsonResponse({'status': 'success', 'message': 'Repayment confirmed'})
            else:
                repayment.status = 'failed'
                repayment.save()
                return JsonResponse({'status': 'failed', 'message': 'Repayment failed'})
        except Repayment.DoesNotExist:
            pass

        try:
            withdrawal = Withdrawal.objects.get(payhero_reference=external_reference)
            if payment_succeeded:
                withdrawal.status = 'confirmed'
                withdrawal.save()
                if recorder:
                    recorder.record_withdrawal(withdrawal)
                return JsonResponse({'status': 'success', 'message': 'Withdrawal confirmed'})
            else:
                withdrawal.status = 'failed'
                withdrawal.save()
                return JsonResponse({'status': 'failed', 'message': 'Withdrawal failed'})
        except Withdrawal.DoesNotExist:
            pass

        logger.warning(f"Webhook reference not found: {external_reference}")
        return JsonResponse({'status': 'error', 'message': 'Reference not found'})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)})


def initiate_payhero_payment(request, payment_type, reference_id):
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number')
        if not phone_number:
            messages.error(request, 'Phone number is required for payment')
            return redirect('dashboard')

        try:
            if payment_type == 'contribution':
                obj = get_object_or_404(Contribution, id=reference_id)
                amount = float(obj.amount)
                description = f"ChamaHub Contribution - {obj.member.username}"
            elif payment_type == 'repayment':
                obj = get_object_or_404(Repayment, id=reference_id)
                amount = float(obj.amount)
                description = f"ChamaHub Loan Repayment - {obj.loan.member.username}"
            elif payment_type == 'withdrawal':
                obj = get_object_or_404(Withdrawal, id=reference_id)
                amount = float(obj.amount)
                description = f"ChamaHub Withdrawal - {obj.member.username}"
            else:
                messages.error(request, 'Invalid payment type')
                return redirect('dashboard')

            payhero_ref = f"CH_{payment_type.upper()}_{reference_id}_{int(timezone.now().timestamp())}"
            headers = {
                'Authorization': settings.PAYHERO_BASIC_AUTH_TOKEN,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }
            payment_data = {
                'amount': int(amount),
                'phone_number': phone_number,
                'channel_id': int(settings.PAYHERO_CHANNEL_ID),
                'provider': 'm-pesa',
                'external_reference': payhero_ref,
                'customer_name': request.user.get_full_name() or request.user.username,
                'callback_url': request.build_absolute_uri('/webhook/payhero/'),
            }

            try:
                response = requests.post(
                    f'{settings.PAYHERO_BASE_URL}/api/v2/payments',
                    headers=headers, json=payment_data, timeout=30
                )
                if response.status_code == 404 and "no rows in result set" in response.text:
                    for channel_id in [911, 1, 2, 3, 100, 200, 300, 500, 1000]:
                        payment_data['channel_id'] = channel_id
                        try:
                            test_response = requests.post(
                                f'{settings.PAYHERO_BASE_URL}/api/v2/payments',
                                headers=headers, json=payment_data, timeout=10
                            )
                            if test_response.status_code == 201:
                                response = test_response
                                messages.warning(request, f'Found working channel ID: {channel_id}. Update settings.py')
                                break
                        except requests.exceptions.RequestException:
                            continue
                    else:
                        messages.error(request, 'No working channel ID found.')
                        return redirect('dashboard')

                if response.status_code == 201:
                    obj.payhero_reference = payhero_ref
                    obj.save()
                    if response.json().get('success', False):
                        messages.success(request, f'STK Push sent to {phone_number}. Ref: {payhero_ref}')
                    else:
                        messages.error(request, f'STK Push failed: {response.json().get("message", "Unknown error")}')
                else:
                    messages.error(request, f'Payhero error: {response.status_code}')

            except requests.exceptions.ConnectionError:
                obj.payhero_reference = payhero_ref
                obj.status = 'pending'
                obj.save()
                messages.warning(request, f'Payhero unavailable. Simulated STK Push. Ref: {payhero_ref}')

        except Exception as e:
            logger.error(f"initiate_payhero_payment error: {e}")
            messages.error(request, f'Error initiating payment: {str(e)}')

        return redirect('dashboard')

    # GET
    if payment_type == 'contribution':
        obj = get_object_or_404(Contribution, id=reference_id)
        description = f"Contribution - {obj.member.username}"
    elif payment_type == 'repayment':
        obj = get_object_or_404(Repayment, id=reference_id)
        description = f"Loan Repayment - {obj.loan.member.username}"
    elif payment_type == 'withdrawal':
        obj = get_object_or_404(Withdrawal, id=reference_id)
        description = f"Withdrawal - {obj.member.username}"
    else:
        messages.error(request, 'Invalid payment type')
        return redirect('dashboard')

    return render(request, 'core/phone_payment.html', {
        'payment_type': payment_type, 'reference_id': reference_id,
        'amount': obj.amount, 'description': description,
    })


@login_required
def pending_loans(request):
    try:
        profile = request.user.chama_profile
        if not profile.is_treasurer():
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('dashboard')
    except ChamaProfile.DoesNotExist:
        messages.error(request, 'Profile not found.')
        return redirect('dashboard')

    loans = Loan.objects.filter(status='pending').order_by('-created_at')
    return render(request, 'core/pending_loans.html', {
        'pending_loans': loans,
        'total_amount': sum(loan.amount for loan in loans),
    })


@login_required
def test_payhero_endpoints(request):
    if not request.user.is_superuser:
        messages.error(request, 'Superusers only.')
        return redirect('dashboard')
    endpoints = ['/', '/api', '/api/v1', '/payments', '/api/payments']
    headers = {'Authorization': settings.PAYHERO_BASIC_AUTH_TOKEN, 'Content-Type': 'application/json'}
    results = []
    for endpoint in endpoints:
        try:
            r = requests.get(f'{settings.PAYHERO_BASE_URL}{endpoint}', headers=headers, timeout=5)
            results.append({'endpoint': endpoint, 'status_code': r.status_code, 'response': r.text[:200]})
        except requests.exceptions.RequestException as e:
            results.append({'endpoint': endpoint, 'status_code': 'Error', 'response': str(e)})
    return render(request, 'core/test_endpoints.html', {'results': results, 'base_url': settings.PAYHERO_BASE_URL})


@login_required
def confirm_pending_payments(request):
    if not request.user.is_superuser:
        messages.error(request, 'Superusers only.')
        return redirect('dashboard')

    recorder = _get_stellar_recorder()
    confirmed_contributions = confirmed_repayments = stellar_count = 0

    for contrib in Contribution.objects.filter(status='pending').exclude(payhero_reference__isnull=True).exclude(payhero_reference=''):
        contrib.status = 'confirmed'
        contrib.save()
        confirmed_contributions += 1
        if recorder and recorder.record_contribution(contrib):
            stellar_count += 1

    for repay in Repayment.objects.filter(status='pending').exclude(payhero_reference__isnull=True).exclude(payhero_reference=''):
        repay.status = 'confirmed'
        repay.save()
        confirmed_repayments += 1
        if recorder and recorder.record_repayment(repay):
            stellar_count += 1

    messages.success(
        request,
        f'Confirmed {confirmed_contributions} contributions and {confirmed_repayments} repayments'
        + (f', {stellar_count} on blockchain!' if stellar_count else '!')
    )
    return redirect('dashboard')


@login_required
def test_payhero_channels(request):
    if not request.user.is_superuser:
        messages.error(request, 'Superusers only.')
        return redirect('dashboard')
    headers = {'Authorization': settings.PAYHERO_BASIC_AUTH_TOKEN, 'Content-Type': 'application/json'}
    test_data = {'amount': 1, 'phone_number': '254712345678', 'provider': 'm-pesa',
                 'external_reference': 'TEST_CH', 'customer_name': 'Test', 'callback_url': 'https://example.com/cb'}
    results = []
    for channel_id in [133, 911, 1, 2, 3, 100, 200, 300, 500, 1000]:
        test_data['channel_id'] = channel_id
        try:
            r = requests.post(f'{settings.PAYHERO_BASE_URL}/api/v2/payments', headers=headers, json=test_data, timeout=10)
            results.append({'channel_id': channel_id, 'status_code': r.status_code, 'response': r.text[:300], 'success': r.status_code == 201})
        except requests.exceptions.RequestException as e:
            results.append({'channel_id': channel_id, 'status_code': 'Error', 'response': str(e), 'success': False})
    return render(request, 'core/test_channels.html', {'results': results, 'base_url': settings.PAYHERO_BASE_URL})