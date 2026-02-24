from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Authentication URLs
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    # Dashboard URLs
    path('dashboard/', views.dashboard, name='dashboard'),
    path('payment-status/', views.payment_status, name='payment_status'),
    
    # Member URLs
    path('contribute/', views.make_contribution, name='make_contribution'),
    path('apply-loan/', views.apply_for_loan, name='apply_loan'),
    path('repay/<int:loan_id>/', views.make_repayment, name='make_repayment'),
    path('withdraw/', views.make_withdrawal, name='make_withdrawal'),
    
    # Treasurer URLs
    path('approve-loan/<int:loan_id>/', views.approve_loan, name='approve_loan'),
    
    # Payment URLs
    path('pay/<str:payment_type>/<int:reference_id>/', views.initiate_payhero_payment, name='initiate_payment'),
    
    # Webhook URL
    path('webhook/payhero/', views.payhero_webhook, name='payhero_webhook'),
    
    # Debug URLs (superuser only)
    path('debug/test-endpoints/', views.test_payhero_endpoints, name='test_endpoints'),
    path('debug/test-channels/', views.test_payhero_channels, name='test_channels'),
    path('debug/confirm-payments/', views.confirm_pending_payments, name='confirm_payments'),
]
