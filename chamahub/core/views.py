from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from decimal import Decimal
import json
import requests

from .models import Contribution, Loan, Repayment, ChamaProfile, Withdrawal, get_group_balance, get_member_balance, get_member_contributions_balance, get_member_loans_balance
from .forms import ContributionForm, LoanForm, RepaymentForm, UserRegistrationForm, WithdrawalForm


def home(request):
    """Home page with login/signup options"""
    return render(request, 'core/home.html')


def register(request):
    """User registration view"""
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Create a chama profile for the new user
            ChamaProfile.objects.create(user=user)
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}!')
            return redirect('login')
    else:
        form = UserRegistrationForm()
    return render(request, 'core/register.html', {'form': form})


@login_required
def dashboard(request):
    """Main dashboard - shows different views based on user role"""
    try:
        profile = request.user.chama_profile
        is_treasurer = profile.is_treasurer()
    except ChamaProfile.DoesNotExist:
        # Create profile if it doesn't exist
        profile = ChamaProfile.objects.create(user=request.user)
        is_treasurer = False
    
    if is_treasurer:
        return treasurer_dashboard(request)
    else:
        return member_dashboard(request)


def member_dashboard(request):
    """Member dashboard showing personal contributions, loans, and repayments"""
    user = request.user
    
    # Get user's data
    contributions = user.contributions.all()[:10]  # Last 10 contributions
    loans = user.loans.all()[:10]  # Last 10 loans
    repayments = Repayment.objects.filter(loan__member=user)[:10]  # Last 10 repayments
    
    # Calculate separate balances
    member_balance = get_member_balance(user)
    contributions_balance = get_member_contributions_balance(user)
    loans_balance = get_member_loans_balance(user)
    total_contributions = sum(c.amount for c in user.contributions.filter(status='confirmed'))
    total_loans = sum(l.amount for l in user.loans.filter(disbursed=True))
    
    # Get active loans with repayment progress
    active_loans = user.loans.filter(disbursed=True)
    for loan in active_loans:
        loan.repayment_progress = loan.get_repayment_progress()
    
    # Count pending payments
    pending_contributions = user.contributions.filter(status='pending').count()
    pending_repayments = Repayment.objects.filter(loan__member=user, status='pending').count()
    pending_payments = pending_contributions + pending_repayments
    
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
    }
    
    return render(request, 'core/member_dashboard.html', context)


def treasurer_dashboard(request):
    """Treasurer dashboard for approving loans and viewing group balance"""
    # Get group statistics
    group_balance = get_group_balance()
    total_contributions = sum(c.amount for c in Contribution.objects.filter(status='confirmed'))
    total_disbursed_loans = sum(l.amount for l in Loan.objects.filter(disbursed=True))
    
    # Get pending loans for approval
    pending_loans = Loan.objects.filter(status='pending')
    
    # Get recent contributions and repayments
    recent_contributions = Contribution.objects.filter(status='confirmed')[:10]
    recent_repayments = Repayment.objects.filter(status='confirmed')[:10]
    
    # Get all members and their balances
    members = User.objects.all()
    member_balances = []
    for member in members:
        try:
            profile = member.chama_profile
            balance = get_member_balance(member)
            member_balances.append({
                'member': member,
                'balance': balance,
                'role': profile.role
            })
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
    }
    
    return render(request, 'core/treasurer_dashboard.html', context)


@login_required
def make_contribution(request):
    """View for members to make contributions"""
    if request.method == 'POST':
        form = ContributionForm(request.POST)
        if form.is_valid():
            contribution = form.save(commit=False)
            contribution.member = request.user
            contribution.save()
            
            # Redirect to Payhero payment
            return redirect('initiate_payment', payment_type='contribution', reference_id=contribution.id)
    else:
        form = ContributionForm()
    
    return render(request, 'core/make_contribution.html', {'form': form})


@login_required
def apply_for_loan(request):
    """View for members to apply for loans"""
    if request.method == 'POST':
        form = LoanForm(request.POST)
        if form.is_valid():
            loan = form.save(commit=False)
            loan.member = request.user
            loan.save()
            messages.success(request, 'Loan application submitted for approval!')
            return redirect('dashboard')
    else:
        form = LoanForm()
    
    return render(request, 'core/apply_loan.html', {'form': form})


@login_required
def make_repayment(request, loan_id):
    """View for members to make loan repayments"""
    loan = get_object_or_404(Loan, id=loan_id, member=request.user)
    
    if request.method == 'POST':
        form = RepaymentForm(request.POST)
        if form.is_valid():
            repayment = form.save(commit=False)
            repayment.loan = loan
            repayment.save()
            
            # Redirect to Payhero payment
            return redirect('initiate_payment', payment_type='repayment', reference_id=repayment.id)
    else:
        form = RepaymentForm()
    
    context = {
        'form': form,
        'loan': loan,
        'total_due': loan.total_due(),
        'repayment_progress': loan.get_repayment_progress(),
    }
    
    return render(request, 'core/make_repayment.html', context)


@login_required
def make_withdrawal(request):
    """View for members to make withdrawals from their balance"""
    user = request.user
    current_balance = get_member_balance(user)
    
    if request.method == 'POST':
        form = WithdrawalForm(request.POST, member=user)
        if form.is_valid():
            withdrawal = form.save(commit=False)
            withdrawal.member = user
            withdrawal.save()
            
            # Redirect to Payhero payment
            return redirect('initiate_payment', payment_type='withdrawal', reference_id=withdrawal.id)
    else:
        form = WithdrawalForm(member=user)
    
    context = {
        'form': form,
        'current_balance': current_balance,
    }
    
    return render(request, 'core/make_withdrawal.html', context)


@login_required
def approve_loan(request, loan_id):
    """View for treasurers to approve loans"""
    # Check if user is a treasurer
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
            loan.save()
            messages.success(request, f'Loan for {loan.member.username} approved!')
        elif action == 'reject':
            loan.status = 'rejected'
            loan.save()
            messages.success(request, f'Loan for {loan.member.username} rejected.')
        elif action == 'disburse':
            loan.status = 'disbursed'
            loan.disbursed = True
            loan.disbursed_at = timezone.now()
            loan.save()
            messages.success(request, f'Loan for {loan.member.username} disbursed!')
        
        return redirect('dashboard')
    
    context = {
        'loan': loan,
        'total_due': loan.total_due(),
    }
    
    return render(request, 'core/approve_loan.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def payhero_webhook(request):
    """
    Webhook endpoint to receive payment confirmations from Payhero.
    """
    import hmac
    import hashlib
    from django.conf import settings
    
    try:
        # Get the raw request body
        payload = request.body
        signature = request.headers.get('X-Payhero-Signature', '')
        
        # Validate webhook signature if webhook secret is set
        if settings.PAYHERO_WEBHOOK_SECRET and settings.PAYHERO_WEBHOOK_SECRET != 'your_webhook_secret_here':
            expected_signature = hmac.new(
                settings.PAYHERO_WEBHOOK_SECRET.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature, expected_signature):
                return JsonResponse({'error': 'Invalid signature'}, status=400)
        
        # Parse the webhook data
        data = json.loads(payload)
        
        # Extract payment information from Payhero callback format
        response_data = data.get('response', {})
        external_reference = response_data.get('ExternalReference')
        status = response_data.get('Status')
        result_code = response_data.get('ResultCode')
        amount = response_data.get('Amount')
        mpesa_receipt = response_data.get('MpesaReceiptNumber')
        phone = response_data.get('Phone')
        
        # Log webhook for debugging
        print(f"Payhero Webhook: {external_reference} - {status} - {result_code} - {amount}")
        
        # Update contribution status if it's a contribution payment
        try:
            contribution = Contribution.objects.get(payhero_reference=external_reference)
            if status == 'Success' and result_code == 0:
                contribution.status = 'confirmed'
                contribution.save()
                print(f"Contribution {contribution.id} confirmed - M-Pesa Receipt: {mpesa_receipt}")
                return JsonResponse({'status': 'success', 'message': 'Contribution confirmed'})
            else:
                contribution.status = 'failed'
                contribution.save()
                print(f"Contribution {contribution.id} failed - Result: {result_code}")
                return JsonResponse({'status': 'failed', 'message': 'Contribution failed'})
                
        except Contribution.DoesNotExist:
            pass
        
        # Update repayment status if it's a repayment payment
        try:
            repayment = Repayment.objects.get(payhero_reference=external_reference)
            if status == 'Success' and result_code == 0:
                repayment.status = 'confirmed'
                repayment.save()
                print(f"Repayment {repayment.id} confirmed - M-Pesa Receipt: {mpesa_receipt}")
                return JsonResponse({'status': 'success', 'message': 'Repayment confirmed'})
            else:
                repayment.status = 'failed'
                repayment.save()
                print(f"Repayment {repayment.id} failed - Result: {result_code}")
                return JsonResponse({'status': 'failed', 'message': 'Repayment failed'})
                
        except Repayment.DoesNotExist:
            pass
        
        # Update withdrawal status if it's a withdrawal payment
        try:
            withdrawal = Withdrawal.objects.get(payhero_reference=external_reference)
            if status == 'Success' and result_code == 0:
                withdrawal.status = 'confirmed'
                withdrawal.save()
                print(f"Withdrawal {withdrawal.id} confirmed - M-Pesa Receipt: {mpesa_receipt}")
                return JsonResponse({'status': 'success', 'message': 'Withdrawal confirmed'})
            else:
                withdrawal.status = 'failed'
                withdrawal.save()
                print(f"Withdrawal {withdrawal.id} failed - Result: {result_code}")
                return JsonResponse({'status': 'failed', 'message': 'Withdrawal failed'})
                
        except Withdrawal.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'Reference not found'})
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)})


def initiate_payhero_payment(request, payment_type, reference_id):
    """
    Initiate STK push payment with Payhero API.
    """
    from django.conf import settings
    
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number')
        if not phone_number:
            messages.error(request, 'Phone number is required for payment')
            return redirect('dashboard')
        
        try:
            if payment_type == 'contribution':
                contribution = get_object_or_404(Contribution, id=reference_id)
                amount = float(contribution.amount)
                description = f"ChamaHub Contribution - {contribution.member.username}"
                
            elif payment_type == 'repayment':
                repayment = get_object_or_404(Repayment, id=reference_id)
                amount = float(repayment.amount)
                description = f"ChamaHub Loan Repayment - {repayment.loan.member.username}"
                
            elif payment_type == 'withdrawal':
                withdrawal = get_object_or_404(Withdrawal, id=reference_id)
                amount = float(withdrawal.amount)
                description = f"ChamaHub Withdrawal - {withdrawal.member.username}"
                
            else:
                messages.error(request, 'Invalid payment type')
                return redirect('dashboard')
            
            # Generate unique reference
            payhero_ref = f"CH_{payment_type.upper()}_{reference_id}_{int(timezone.now().timestamp())}"
            
            # Payhero STK Push API request
            headers = {
                'Authorization': settings.PAYHERO_BASIC_AUTH_TOKEN,
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Payhero API v2 payment data
            payment_data = {
                'amount': int(amount),  # Payhero expects integer amount
                'phone_number': phone_number,
                'channel_id': int(settings.PAYHERO_CHANNEL_ID),
                'provider': 'm-pesa',
                'external_reference': payhero_ref,
                'customer_name': request.user.get_full_name() or request.user.username,
                'callback_url': request.build_absolute_uri('/webhook/payhero/')
            }
            
            # Debug: Log the payment data being sent
            print(f"Payhero Payment Data: {payment_data}")
            print(f"Using Channel ID: {settings.PAYHERO_CHANNEL_ID}")
            print(f"API URL: {settings.PAYHERO_BASE_URL}/api/v2/payments")
            
            # Make API call to Payhero v2 payments endpoint
            try:
                response = requests.post(
                    f'{settings.PAYHERO_BASE_URL}/api/v2/payments',
                    headers=headers,
                    json=payment_data,
                    timeout=30
                )
                
                # If channel ID not found, try common channel IDs
                if response.status_code == 404 and "no rows in result set" in response.text:
                    print(f"Channel ID {settings.PAYHERO_CHANNEL_ID} not found, trying common channel IDs...")
                    
                    common_channel_ids = [911, 1, 2, 3, 100, 200, 300, 500, 1000]
                    successful_channel = None
                    
                    for channel_id in common_channel_ids:
                        payment_data['channel_id'] = channel_id
                        print(f"Trying channel ID: {channel_id}")
                        
                        try:
                            test_response = requests.post(
                                f'{settings.PAYHERO_BASE_URL}/api/v2/payments',
                                headers=headers,
                                json=payment_data,
                                timeout=10
                            )
                            
                            if test_response.status_code == 201:
                                successful_channel = channel_id
                                response = test_response
                                print(f"Success with channel ID: {channel_id}")
                                break
                                
                        except requests.exceptions.RequestException:
                            continue
                    
                    if successful_channel:
                        messages.warning(request, f'Found working channel ID: {successful_channel}. Please update PAYHERO_CHANNEL_ID in settings.py')
                    else:
                        messages.error(request, f'No working channel ID found. Please check your Payhero dashboard for the correct channel ID.')
                        return redirect('dashboard')
                
                if response.status_code == 201:  # Payhero returns 201 for successful creation
                    response_data = response.json()
                    
                    # Update the record with Payhero reference
                    if payment_type == 'contribution':
                        contribution.payhero_reference = payhero_ref
                        contribution.save()
                    else:
                        repayment.payhero_reference = payhero_ref
                        repayment.save()
                    
                    # Check if payment was successful
                    if response_data.get('success', False):
                        checkout_request_id = response_data.get('CheckoutRequestID', '')
                        messages.success(request, f'STK Push sent to {phone_number}. Please check your phone and enter your M-Pesa PIN to complete the payment. Reference: {payhero_ref}')
                    else:
                        messages.error(request, f'Failed to send STK Push: {response_data.get("message", "Unknown error")}')
                        
                else:
                    messages.error(request, f'Payhero API error: {response.status_code} - {response.text}')
                    
            except requests.exceptions.ConnectionError:
                # Fallback for development/testing when Payhero API is not available
                messages.warning(request, f'Payhero API not available. Simulating STK Push to {phone_number}. In production, this would send a real STK push.')
                
                # Update the record with Payhero reference for testing
                if payment_type == 'contribution':
                    contribution.payhero_reference = payhero_ref
                    contribution.status = 'pending'  # Keep as pending until real webhook
                    contribution.save()
                else:
                    repayment.payhero_reference = payhero_ref
                    repayment.status = 'pending'  # Keep as pending until real webhook
                    repayment.save()
                
                messages.info(request, f'Payment reference: {payhero_ref}. Status will be updated when webhook is received.')
                
        except requests.exceptions.RequestException as e:
            messages.error(request, f'Network error connecting to Payhero: {str(e)}')
        except Exception as e:
            messages.error(request, f'Error initiating payment: {str(e)}')
        
        return redirect('dashboard')
    
    else:
        # Show phone number collection form
        if payment_type == 'contribution':
            contribution = get_object_or_404(Contribution, id=reference_id)
            amount = contribution.amount
            description = f"Contribution - {contribution.member.username}"
        elif payment_type == 'repayment':
            repayment = get_object_or_404(Repayment, id=reference_id)
            amount = repayment.amount
            description = f"Loan Repayment - {repayment.loan.member.username}"
        else:
            messages.error(request, 'Invalid payment type')
            return redirect('dashboard')
        
        context = {
            'payment_type': payment_type,
            'reference_id': reference_id,
            'amount': amount,
            'description': description,
        }
        
        return render(request, 'core/phone_payment.html', context)


@login_required
def test_payhero_endpoints(request):
    """
    Debug view to test different Payhero API endpoints
    """
    from django.conf import settings
    
    if not request.user.is_superuser:
        messages.error(request, 'Only superusers can access this debug view')
        return redirect('dashboard')
    
    endpoints_to_test = [
        '/',
        '/api',
        '/api/v1',
        '/mpesa',
        '/stkpush',
        '/mpesa/stkpush',
        '/api/stkpush',
        '/api/v1/stkpush',
        '/mpesa/stkpush/v1/processrequest',
        '/v1/stkpush',
        '/payments',
        '/api/payments',
        '/mpesa/payments'
    ]
    
    results = []
    headers = {
        'Authorization': settings.PAYHERO_BASIC_AUTH_TOKEN,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    for endpoint in endpoints_to_test:
        try:
            response = requests.get(
                f'{settings.PAYHERO_BASE_URL}{endpoint}',
                headers=headers,
                timeout=5
            )
            results.append({
                'endpoint': endpoint,
                'status_code': response.status_code,
                'response': response.text[:200] if response.text else 'No response body'
            })
        except requests.exceptions.RequestException as e:
            results.append({
                'endpoint': endpoint,
                'status_code': 'Error',
                'response': str(e)
            })
    
    context = {
        'results': results,
        'base_url': settings.PAYHERO_BASE_URL
    }
    
    return render(request, 'core/test_endpoints.html', context)


@login_required
def payment_status(request):
    """
    View to show detailed payment status and allow manual confirmation
    """
    user = request.user
    
    # Get all user's payments
    contributions = user.contributions.all().order_by('-date')
    repayments = Repayment.objects.filter(loan__member=user).order_by('-date')
    
    # Count by status
    pending_contributions = contributions.filter(status='pending')
    confirmed_contributions = contributions.filter(status='confirmed')
    failed_contributions = contributions.filter(status='failed')
    
    pending_repayments = repayments.filter(status='pending')
    confirmed_repayments = repayments.filter(status='confirmed')
    failed_repayments = repayments.filter(status='failed')
    
    context = {
        'contributions': contributions,
        'repayments': repayments,
        'pending_contributions': pending_contributions,
        'confirmed_contributions': confirmed_contributions,
        'failed_contributions': failed_contributions,
        'pending_repayments': pending_repayments,
        'confirmed_repayments': confirmed_repayments,
        'failed_repayments': failed_repayments,
        'total_pending': pending_contributions.count() + pending_repayments.count(),
    }
    
    return render(request, 'core/payment_status.html', context)


@login_required
def confirm_pending_payments(request):
    """
    Debug view to manually confirm pending payments (for testing)
    """
    if not request.user.is_superuser:
        messages.error(request, 'Only superusers can access this debug view')
        return redirect('dashboard')
    
    from .models import Contribution, Repayment
    
    # Confirm pending contributions with Payhero references
    pending_contributions = Contribution.objects.filter(status='pending').exclude(payhero_reference__isnull=True).exclude(payhero_reference='')
    confirmed_contributions = 0
    
    for contrib in pending_contributions:
        contrib.status = 'confirmed'
        contrib.save()
        confirmed_contributions += 1
    
    # Confirm pending repayments with Payhero references
    pending_repayments = Repayment.objects.filter(status='pending').exclude(payhero_reference__isnull=True).exclude(payhero_reference='')
    confirmed_repayments = 0
    
    for repay in pending_repayments:
        repay.status = 'confirmed'
        repay.save()
        confirmed_repayments += 1
    
    messages.success(request, f'Confirmed {confirmed_contributions} contributions and {confirmed_repayments} repayments!')
    return redirect('dashboard')


@login_required
def test_payhero_channels(request):
    """
    Debug view to test different Payhero channel IDs
    """
    from django.conf import settings
    
    if not request.user.is_superuser:
        messages.error(request, 'Only superusers can access this debug view')
        return redirect('dashboard')
    
    # Common channel IDs to test
    channel_ids_to_test = [133, 911, 1, 2, 3, 100, 200, 300, 500, 1000]
    
    results = []
    headers = {
        'Authorization': settings.PAYHERO_BASIC_AUTH_TOKEN,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    # Test payment data
    test_payment_data = {
        'amount': 1,  # Small test amount
        'phone_number': '254712345678',  # Test phone number
        'provider': 'm-pesa',
        'external_reference': 'TEST_CHANNEL_ID',
        'customer_name': 'Test User',
        'callback_url': 'https://example.com/callback'
    }
    
    for channel_id in channel_ids_to_test:
        test_payment_data['channel_id'] = channel_id
        
        try:
            response = requests.post(
                f'{settings.PAYHERO_BASE_URL}/api/v2/payments',
                headers=headers,
                json=test_payment_data,
                timeout=10
            )
            
            results.append({
                'channel_id': channel_id,
                'status_code': response.status_code,
                'response': response.text[:300] if response.text else 'No response body',
                'success': response.status_code == 201
            })
            
        except requests.exceptions.RequestException as e:
            results.append({
                'channel_id': channel_id,
                'status_code': 'Error',
                'response': str(e),
                'success': False
            })
    
    context = {
        'results': results,
        'base_url': settings.PAYHERO_BASE_URL
    }
    
    return render(request, 'core/test_channels.html', context)