from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.urls import reverse
from django.db import transaction
from django.db.models import Sum, Q
from django.db.models.functions import TruncMonth
from django.conf import settings
from decimal import Decimal
import json
import requests
import csv
import hmac
import hashlib
import base64
import logging
import secrets
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


DASHBOARD_MODE_MEMBER = 'member'
DASHBOARD_MODE_TREASURER = 'treasurer'


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


def _normalize_mpesa_phone(phone_number):
    """Normalize to the format Payhero expects: 07xxxxxxxx or 01xxxxxxxx."""
    if not phone_number:
        return None

    cleaned = ''.join(ch for ch in str(phone_number).strip() if ch.isdigit() or ch == '+')
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]

    digits = ''.join(ch for ch in cleaned if ch.isdigit())
    if digits.startswith('254') and len(digits) == 12:
        digits = '0' + digits[3:]

    if len(digits) == 10 and digits.startswith('0'):
        return digits

    return None


def _build_payhero_auth_header():
    """Build Authorization header with backward compatibility for existing token setup."""
    token = getattr(settings, 'PAYHERO_BASIC_AUTH_TOKEN', '')
    if token:
        return token if token.startswith('Basic ') else f'Basic {token}'

    username = getattr(settings, 'PAYHERO_API_USERNAME', '')
    api_key = getattr(settings, 'PAYHERO_API_KEY', '') or getattr(settings, 'PAYHERO_API_PASSWORD', '')
    if not username or not api_key:
        return ''

    raw = f'{username}:{api_key}'.encode('utf-8')
    return f"Basic {base64.b64encode(raw).decode('utf-8')}"


def _payhero_headers():
    return {
        'Authorization': _build_payhero_auth_header(),
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }


def _is_payhero_success(status, result_code):
    normalized_status = str(status or '').strip().lower()
    normalized_code = str(result_code).strip()
    return normalized_code == '0' and normalized_status in {'success', 'completed'}


def _is_payhero_failure(status, result_code):
    normalized_status = str(status or '').strip().lower()
    normalized_code = str(result_code).strip()
    if normalized_code in {'1', '1032', '2001'}:
        return True
    return normalized_status in {'failed', 'cancelled', 'canceled', 'error'}


def _append_note(existing, note):
    if not note:
        return existing
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}\n{note}"


def _is_positive_initiation_response(data):
    """Best-effort success detection for Payhero initiation payloads."""
    if not isinstance(data, dict):
        return True

    if data.get('success') is False:
        return False

    status_value = str(data.get('Status') or data.get('status') or '').strip().lower()
    if status_value in {'failed', 'error', 'rejected'}:
        return False

    return True


def _update_payment_by_reference(external_reference, status, result_code, result_desc='', provider_reference=''):
    """
    Update a local payment using Payhero callback/polling data.
    Returns (matched, local_status, payment_label).
    """
    if not external_reference:
        return False, 'unknown', ''

    succeeded = _is_payhero_success(status, result_code)
    failed = _is_payhero_failure(status, result_code)
    if not succeeded and not failed:
        return False, 'pending', ''

    recorder = _get_stellar_recorder()
    candidates = [
        ('contribution', Contribution, lambda r, o: r.record_contribution(o)),
        ('repayment', Repayment, lambda r, o: r.record_repayment(o)),
        ('withdrawal', Withdrawal, lambda r, o: r.record_withdrawal(o)),
    ]

    for label, model, recorder_fn in candidates:
        try:
            with transaction.atomic():
                payment = model.objects.select_for_update().get(payhero_reference=external_reference)

                if payment.status != 'pending':
                    return True, payment.status, label

                payment.status = 'confirmed' if succeeded else 'failed'
                if provider_reference:
                    payment.notes = _append_note(payment.notes, f'Provider reference: {provider_reference}')
                if result_desc:
                    payment.notes = _append_note(payment.notes, f'Payhero: {result_desc}')
                payment.save()

            if succeeded and recorder:
                recorder_fn(recorder, payment)
            return True, payment.status, label
        except model.DoesNotExist:
            continue

    return False, 'not_found', ''


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

    requested_mode = request.session.get('dashboard_mode')
    if is_treasurer:
        if requested_mode == DASHBOARD_MODE_MEMBER:
            return member_dashboard(request)
        request.session['dashboard_mode'] = DASHBOARD_MODE_TREASURER
        return treasurer_dashboard(request)

    request.session['dashboard_mode'] = DASHBOARD_MODE_MEMBER
    return member_dashboard(request)


@login_required
def switch_dashboard_mode(request, mode):
    normalized_mode = (mode or '').strip().lower()
    if normalized_mode not in {DASHBOARD_MODE_MEMBER, DASHBOARD_MODE_TREASURER}:
        messages.error(request, 'Invalid dashboard mode selected.')
        return redirect('dashboard')

    try:
        profile = request.user.chama_profile
    except ChamaProfile.DoesNotExist:
        messages.error(request, 'Profile not found.')
        return redirect('dashboard')

    if normalized_mode == DASHBOARD_MODE_TREASURER and not profile.is_treasurer():
        messages.error(request, 'You do not have access to treasurer mode.')
        return redirect('dashboard')

    request.session['dashboard_mode'] = normalized_mode
    label = 'Treasurer' if normalized_mode == DASHBOARD_MODE_TREASURER else 'Member'
    messages.success(request, f'Switched to {label} mode.')
    return redirect('dashboard')


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


@login_required
@require_http_methods(["GET"])
def payment_status_live(request):
    user = request.user

    contributions = user.contributions.all()
    repayments = Repayment.objects.filter(loan__member=user)

    pending_count = contributions.filter(status='pending').count() + repayments.filter(status='pending').count()
    confirmed_count = contributions.filter(status='confirmed').count() + repayments.filter(status='confirmed').count()
    failed_count = contributions.filter(status='failed').count() + repayments.filter(status='failed').count()

    recent_contribution = (
        contributions
        .filter(status='confirmed', status_updated_at__isnull=False)
        .order_by('-status_updated_at')
        .first()
    )
    recent_repayment = (
        repayments
        .filter(status='confirmed', status_updated_at__isnull=False)
        .order_by('-status_updated_at')
        .first()
    )

    latest = None
    if recent_contribution and recent_repayment:
        latest = recent_contribution if recent_contribution.status_updated_at >= recent_repayment.status_updated_at else recent_repayment
    else:
        latest = recent_contribution or recent_repayment

    latest_message = ''
    if latest is not None:
        tx_type = 'Contribution' if isinstance(latest, Contribution) else 'Repayment'
        latest_message = f'{tx_type} of KSh {latest.amount} confirmed successfully.'

    return JsonResponse({
        'pending_count': pending_count,
        'confirmed_count': confirmed_count,
        'failed_count': failed_count,
        'latest_message': latest_message,
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
        response_data = data.get('response') if isinstance(data.get('response'), dict) else data

        external_reference = response_data.get('ExternalReference') or response_data.get('external_reference')
        status = response_data.get('Status') or response_data.get('status')
        result_code = response_data.get('ResultCode') if response_data.get('ResultCode') is not None else response_data.get('result_code')
        result_desc = response_data.get('ResultDesc') or response_data.get('message', '')
        mpesa_receipt = response_data.get('MpesaReceiptNumber') or response_data.get('provider_reference', '')

        logger.info(
            "Payhero webhook | ref=%s status=%s code=%s receipt=%s",
            external_reference, status, result_code, mpesa_receipt
        )

        matched, local_status, payment_label = _update_payment_by_reference(
            external_reference=external_reference,
            status=status,
            result_code=result_code,
            result_desc=result_desc,
            provider_reference=mpesa_receipt,
        )

        if not external_reference:
            return JsonResponse({'error': 'ExternalReference missing'}, status=400)

        if not matched and local_status == 'pending':
            return JsonResponse({'status': 'accepted', 'message': 'Pending callback status'}, status=202)

        if not matched:
            logger.warning(f"Webhook reference not found: {external_reference}")
            return JsonResponse({'status': 'error', 'message': 'Reference not found'}, status=404)

        return JsonResponse({
            'status': local_status,
            'payment_type': payment_label,
            'reference': external_reference,
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)})


def initiate_payhero_payment(request, payment_type, reference_id):
    def _get_owned_payment():
        if payment_type == 'contribution':
            payment_obj = get_object_or_404(Contribution, id=reference_id, member=request.user)
            payment_amount = float(payment_obj.amount)
            payment_description = f"Contribution - {payment_obj.member.username}"
            return payment_obj, payment_amount, payment_description
        if payment_type == 'repayment':
            payment_obj = get_object_or_404(Repayment, id=reference_id, loan__member=request.user)
            payment_amount = float(payment_obj.amount)
            payment_description = f"Loan Repayment - {payment_obj.loan.member.username}"
            return payment_obj, payment_amount, payment_description
        if payment_type == 'withdrawal':
            payment_obj = get_object_or_404(Withdrawal, id=reference_id, member=request.user)
            payment_amount = float(payment_obj.amount)
            payment_description = f"Withdrawal - {payment_obj.member.username}"
            return payment_obj, payment_amount, payment_description
        return None, None, None

    obj, amount, description = _get_owned_payment()
    if not obj:
        messages.error(request, 'Invalid payment type')
        return redirect('dashboard')

    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        phone_number = _normalize_mpesa_phone(request.POST.get('phone_number', ''))
        if not phone_number:
            if is_ajax:
                return JsonResponse({'ok': False, 'message': 'Phone number is required for payment'}, status=400)
            messages.error(request, 'Phone number is required for payment')
            return redirect('initiate_payment', payment_type=payment_type, reference_id=reference_id)

        try:
            auth_header = _build_payhero_auth_header()
            if not auth_header:
                if is_ajax:
                    return JsonResponse({'ok': False, 'message': 'Payhero credentials are missing. Set username and API key in environment variables.'}, status=503)
                messages.error(request, 'Payhero credentials are missing. Set username and API key in environment variables.')
                return redirect('initiate_payment', payment_type=payment_type, reference_id=reference_id)

            payhero_ref = (
                f"CH_{payment_type.upper()}_{reference_id}_"
                f"{int(timezone.now().timestamp())}_{secrets.token_hex(3).upper()}"
            )
            callback_url = getattr(settings, 'PAYHERO_CALLBACK_URL', '') or request.build_absolute_uri(reverse('payhero_webhook'))
            if '127.0.0.1' in callback_url or 'localhost' in callback_url:
                messages.warning(
                    request,
                    'Callback URL is local. For automatic callback updates, set PAYHERO_CALLBACK_URL to a public HTTPS URL.'
                )

            payment_data = {
                'amount': int(round(amount)),
                'phone_number': phone_number,
                'channel_id': int(settings.PAYHERO_CHANNEL_ID),
                'provider': 'm-pesa',
                'external_reference': payhero_ref,
                'customer_name': request.user.get_full_name() or request.user.username,
                'callback_url': callback_url,
            }

            response = None
            last_request_error = None
            for attempt in range(2):
                try:
                    response = requests.post(
                        f'{settings.PAYHERO_BASE_URL}/api/v2/payments',
                        headers=_payhero_headers(),
                        json=payment_data,
                        timeout=30,
                    )
                    # Retry once on transient upstream failures.
                    if response.status_code >= 500 and attempt == 0:
                        logger.warning(
                            "Payhero initiate transient server error status=%s ref=%s; retrying once",
                            response.status_code,
                            payhero_ref,
                        )
                        continue
                    break
                except requests.exceptions.RequestException as exc:
                    last_request_error = exc
                    if attempt == 0:
                        logger.warning(
                            "Payhero initiate transient network error ref=%s; retrying once: %s",
                            payhero_ref,
                            exc,
                        )
                        continue
                    raise

            if response is None and last_request_error:
                raise last_request_error

            response_json = {}
            try:
                response_json = response.json()
            except Exception:
                response_json = {}

            logger.info(
                "Payhero initiate | status=%s ref=%s body=%s",
                response.status_code,
                payhero_ref,
                response.text[:500],
            )

            if response.status_code in {200, 201, 202} and _is_positive_initiation_response(response_json):
                obj.payhero_reference = payhero_ref
                obj.status = 'pending'

                checkout_request_id = (
                    response_json.get('CheckoutRequestID')
                    or response_json.get('checkout_request_id')
                )
                if checkout_request_id:
                    obj.checkout_request_id = checkout_request_id
                    obj.notes = _append_note(obj.notes, f'CheckoutRequestID: {checkout_request_id}')

                obj.save()

                if hasattr(request.user, 'chama_profile'):
                    request.user.chama_profile.phone_number = phone_number
                    request.user.chama_profile.save(update_fields=['phone_number'])

                messages.success(
                    request,
                    f'A prompt has been sent to {phone_number}. Please enter your M-Pesa PIN.'
                )
                if is_ajax:
                    return JsonResponse({
                        'ok': True,
                        'status': 'pending',
                        'reference': obj.payhero_reference,
                        'message': f'A prompt has been sent to {phone_number}. Please enter your M-Pesa PIN.',
                    })
            else:
                response_message = response_json.get('message', '') if isinstance(response_json, dict) else ''
                if not response_message:
                    response_message = response.text[:200]
                if response.status_code >= 500 and not response_message:
                    response_message = 'Payhero gateway temporary error. Please retry in a few seconds.'
                if is_ajax:
                    return JsonResponse({'ok': False, 'message': response_message or 'Unable to initiate STK push'}, status=502)
                messages.error(request, f'Payhero error ({response.status_code}): {response_message or "Unable to initiate STK push"}')

        except requests.exceptions.RequestException as exc:
            logger.error(f"initiate_payhero_payment network error: {exc}")

            # Recovery path: initiation might have succeeded server-side even if client timed out.
            # Probe by the same external reference before telling the user to retry.
            try:
                status_response = requests.get(
                    f'{settings.PAYHERO_BASE_URL}/api/v2/transaction-status',
                    headers=_payhero_headers(),
                    params={'reference': payhero_ref},
                    timeout=20,
                )

                if status_response.status_code in {200, 201}:
                    status_data = status_response.json()
                    if str(status_data.get('error_code', '')).strip().upper() != 'NOT_FOUND':
                        obj.payhero_reference = payhero_ref

                        remote_status = status_data.get('Status') or status_data.get('status')
                        remote_code = (
                            status_data.get('ResultCode')
                            if status_data.get('ResultCode') is not None
                            else status_data.get('result_code')
                        )
                        remote_desc = status_data.get('ResultDesc') or status_data.get('message', '')
                        provider_reference = status_data.get('MpesaReceiptNumber') or status_data.get('provider_reference', '')

                        if remote_code is None and str(remote_status or '').strip().lower() == 'success':
                            remote_code = 0

                        matched, local_status, _ = _update_payment_by_reference(
                            external_reference=payhero_ref,
                            status=remote_status,
                            result_code=remote_code,
                            result_desc=remote_desc,
                            provider_reference=provider_reference,
                        )

                        if matched:
                            obj.refresh_from_db(fields=['status', 'payhero_reference'])
                        else:
                            obj.status = 'pending'
                            obj.save(update_fields=['payhero_reference', 'status'])

                        if obj.status == 'confirmed':
                            if is_ajax:
                                return JsonResponse({
                                    'ok': True,
                                    'status': 'confirmed',
                                    'reference': obj.payhero_reference,
                                    'message': 'Payment already received and confirmed.',
                                })
                            messages.success(request, 'Payment already received and confirmed.')
                        else:
                            if is_ajax:
                                return JsonResponse({
                                    'ok': True,
                                    'status': 'pending',
                                    'reference': obj.payhero_reference,
                                    'message': 'Payhero responded slowly, but your payment request is active. Please complete the M-Pesa prompt on your phone.',
                                })
                            messages.warning(
                                request,
                                'Payhero responded slowly, but your payment request is active. Please complete the M-Pesa prompt on your phone.'
                            )
                        return redirect('initiate_payment', payment_type=payment_type, reference_id=reference_id)
            except Exception as probe_exc:
                logger.warning("Payhero timeout recovery probe failed for %s: %s", payhero_ref, probe_exc)

            if is_ajax:
                return JsonResponse({'ok': False, 'message': 'Could not reach Payhero. Please try again.'}, status=504)
            messages.error(request, 'Could not reach Payhero. Please try again.')
        except Exception as e:
            logger.error(f"initiate_payhero_payment error: {e}")
            if is_ajax:
                return JsonResponse({'ok': False, 'message': f'Error initiating payment: {str(e)}'}, status=500)
            messages.error(request, f'Error initiating payment: {str(e)}')

        return redirect('initiate_payment', payment_type=payment_type, reference_id=reference_id)

    return render(request, 'core/phone_payment.html', {
        'payment_type': payment_type, 'reference_id': reference_id,
        'amount': obj.amount, 'description': description,
        'active_reference': obj.payhero_reference or '',
        'current_status': obj.status,
        'current_blockchain_hash': obj.stellar_tx_hash or '',
    })


@login_required
@require_http_methods(["GET"])
def poll_payhero_payment_status(request, payment_type, reference_id):
    if payment_type == 'contribution':
        obj = get_object_or_404(Contribution, id=reference_id, member=request.user)
    elif payment_type == 'repayment':
        obj = get_object_or_404(Repayment, id=reference_id, loan__member=request.user)
    elif payment_type == 'withdrawal':
        obj = get_object_or_404(Withdrawal, id=reference_id, member=request.user)
    else:
        return JsonResponse({'error': 'Invalid payment type'}, status=400)

    if obj.status != 'pending':
        return JsonResponse({
            'status': obj.status,
            'reference': obj.payhero_reference or '',
            'payment_type': payment_type,
            'amount': float(obj.amount),
            'blockchain_tx_hash': obj.stellar_tx_hash or '',
            'blockchain_recorded': bool(obj.stellar_tx_hash),
            'message': 'Payment confirmed and blockchain record created.' if obj.status == 'confirmed' and obj.stellar_tx_hash else '',
        })

    if not obj.payhero_reference:
        return JsonResponse({'status': 'pending', 'message': 'Reference not assigned yet'})

    auth_header = _build_payhero_auth_header()
    if not auth_header:
        return JsonResponse({'status': 'pending', 'message': 'Payhero credentials not configured'})

    # Use CheckoutRequestID for polling if available, otherwise fall back to our external reference
    poll_reference = obj.checkout_request_id or obj.payhero_reference

    try:
        response = None
        for attempt in range(2):
            response = requests.get(
                f'{settings.PAYHERO_BASE_URL}/api/v2/transaction-status',
                headers=_payhero_headers(),
                params={'reference': poll_reference},
                timeout=20,
            )
            if response.status_code in {200, 201}:
                break
            if attempt == 0 and response.status_code >= 500:
                logger.debug(
                    "Payhero polling transient server error status=%s ref=%s; retrying once",
                    response.status_code,
                    obj.payhero_reference,
                )
                continue
            # For non-200 responses, check if it's a normal NOT_FOUND (transaction not yet indexed by Payhero)
            try:
                error_data = response.json()
                if str(error_data.get('error_code', '')).strip().upper() == 'NOT_FOUND':
                    # Transaction not indexed yet - this is normal during the first few seconds
                    logger.debug(
                        "Payhero polling: transaction not found yet (normal indexing delay) ref=%s",
                        obj.payhero_reference,
                    )
                    return JsonResponse({
                        'status': 'pending',
                        'reference': obj.payhero_reference,
                        'message': 'Transaction not found yet on Payhero. Waiting for callback/status sync.',
                    })
            except (ValueError, TypeError):
                pass
            
            # Unexpected non-200 response
            logger.warning(
                "Payhero polling unexpected status=%s ref=%s body=%s",
                response.status_code,
                obj.payhero_reference,
                response.text[:200],
            )
            return JsonResponse({
                'status': 'pending',
                'reference': obj.payhero_reference,
                'payment_type': payment_type,
                'amount': float(obj.amount),
                'message': 'Status service is temporarily unavailable. Still waiting for callback confirmation.',
            })

        if response is None or response.status_code not in {200, 201}:
            return JsonResponse({
                'status': 'pending',
                'reference': obj.payhero_reference,
                'payment_type': payment_type,
                'amount': float(obj.amount),
                'message': 'Status service is temporarily unavailable. Still waiting for callback confirmation.',
            })

        remote_data = response.json()

        remote_status = remote_data.get('Status') or remote_data.get('status')
        remote_code = remote_data.get('ResultCode') if remote_data.get('ResultCode') is not None else remote_data.get('result_code')
        remote_desc = remote_data.get('ResultDesc') or remote_data.get('message', '')
        provider_reference = remote_data.get('MpesaReceiptNumber') or remote_data.get('provider_reference', '')

        # Some Payhero responses only provide "status": "Success" without result code.
        if remote_code is None and str(remote_status or '').strip().lower() == 'success':
            remote_code = 0

        matched, _local_status, _ = _update_payment_by_reference(
            external_reference=obj.payhero_reference,
            status=remote_status,
            result_code=remote_code,
            result_desc=remote_desc,
            provider_reference=provider_reference,
        )

        if matched:
            obj.refresh_from_db(fields=['status', 'stellar_tx_hash'])

        return JsonResponse({
            'status': obj.status,
            'reference': obj.payhero_reference,
            'payment_type': payment_type,
            'amount': float(obj.amount),
            'remote_status': remote_status,
            'provider_reference': provider_reference,
            'blockchain_tx_hash': obj.stellar_tx_hash or '',
            'blockchain_recorded': bool(obj.stellar_tx_hash),
            'message': 'Payment confirmed and blockchain record created.' if obj.status == 'confirmed' and obj.stellar_tx_hash else '',
        })

    except requests.exceptions.RequestException as exc:
        logger.warning("Payhero polling failed for %s: %s", obj.payhero_reference, exc)
        return JsonResponse({
            'status': 'pending',
            'reference': obj.payhero_reference,
            'payment_type': payment_type,
            'amount': float(obj.amount),
            'message': 'Polling encountered a network issue. Waiting for callback confirmation.',
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