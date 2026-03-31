from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import views_blockchain  # Blockchain-specific views

urlpatterns = [
    # ======================================================================
    # PUBLIC / AUTHENTICATION
    # ======================================================================
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    
    # Django's built-in auth views
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),  # Works with GET and POST
    
    # ======================================================================
    # DASHBOARD & STATUS
    # ======================================================================
    path('dashboard/', views.dashboard, name='dashboard'),  # Routes to member or treasurer
     path('dashboard/mode/<str:mode>/', views.switch_dashboard_mode, name='switch_dashboard_mode'),
    path('payment-status/', views.payment_status, name='payment_status'),  # Member's payment history
     path('payment-status/live/', views.payment_status_live, name='payment_status_live'),
    
    # ======================================================================
    # MEMBER ACTIONS
    # ======================================================================
    path('contribute/', views.make_contribution, name='make_contribution'),
    path('apply-loan/', views.apply_for_loan, name='apply_loan'),
    path('repay/<int:loan_id>/', views.make_repayment, name='make_repayment'),
    path('withdraw/', views.make_withdrawal, name='make_withdrawal'),
    path('update-phone/', views.update_phone, name='update_phone'),  # Update M-Pesa number
    
    # ======================================================================
    # TREASURER ACTIONS
    # ======================================================================
    # Loan management
    path('approve-loan/<int:loan_id>/', views.approve_loan, name='approve_loan'),
    path('pending-loans/', views.pending_loans, name='pending_loans'),  # List all pending loans
    
    # Payment confirmation
    path('treasurer/payments/', views.treasurer_payments, name='treasurer_payments'),
    path('treasurer/confirm/<str:payment_type>/<int:payment_id>/', 
         views.confirm_payment, name='confirm_payment'),
    path('treasurer/bulk-confirm/', views.bulk_confirm_payments, name='bulk_confirm_payments'),
    
    # ======================================================================
    # PAYMENTS & WEBHOOKS
    # ======================================================================
    # Payhero STK push initiation
    path('pay/<str:payment_type>/<int:reference_id>/', 
         views.initiate_payhero_payment, name='initiate_payment'),
    path('pay/status/<str:payment_type>/<int:reference_id>/',
         views.poll_payhero_payment_status, name='payment_poll_status'),
    
    # Payhero webhook (receives payment confirmations)
    path('webhook/payhero/', views.payhero_webhook, name='payhero_webhook'),
    
    # ======================================================================
    # BLOCKCHAIN (Stellar)
    # ======================================================================
    # Main blockchain dashboard
    path('blockchain/', views_blockchain.blockchain_dashboard, name='blockchain_dashboard'),
    
    # Individual transaction detail (by Stellar hash)
    path('blockchain/tx/<str:tx_hash>/', 
         views_blockchain.blockchain_transaction_detail, name='blockchain_tx_detail'),
    
    # ======================================================================
    # DEBUG / SUPERUSER ONLY
    # ======================================================================
    # Test Payhero API endpoints
    path('debug/test-endpoints/', views.test_payhero_endpoints, name='test_endpoints'),
    
    # Test different Payhero channel IDs
    path('debug/test-channels/', views.test_payhero_channels, name='test_channels'),
    
    # Manually confirm pending payments (for testing)
    path('debug/confirm-payments/', views.confirm_pending_payments, name='confirm_payments'),
]

# ======================================================================
# OPTIONAL: Add handler for 404/500 if needed
# ======================================================================
# handler404 = 'core.views.custom_404'
# handler500 = 'core.views.custom_500'