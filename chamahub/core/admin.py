from django.contrib import admin
from .models import Contribution, Loan, Repayment, ChamaProfile, Withdrawal


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ['member', 'amount', 'status', 'date', 'payhero_reference']
    list_filter = ['status', 'date']
    search_fields = ['member__username', 'payhero_reference']
    readonly_fields = ['date']


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ['member', 'amount', 'interest_rate', 'status', 'approved', 'disbursed', 'created_at']
    list_filter = ['status', 'approved', 'disbursed', 'created_at']
    search_fields = ['member__username', 'purpose']
    readonly_fields = ['created_at', 'approved_at', 'disbursed_at']
    
    def save_model(self, request, obj, form, change):
        # Auto-update timestamps when status changes
        if change:
            if obj.approved and not obj.approved_at:
                from django.utils import timezone
                obj.approved_at = timezone.now()
            if obj.disbursed and not obj.disbursed_at:
                from django.utils import timezone
                obj.disbursed_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(Repayment)
class RepaymentAdmin(admin.ModelAdmin):
    list_display = ['loan', 'amount', 'status', 'date', 'payhero_reference']
    list_filter = ['status', 'date']
    search_fields = ['loan__member__username', 'payhero_reference']
    readonly_fields = ['date']


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ['member', 'amount', 'status', 'date', 'payhero_reference']
    list_filter = ['status', 'date']
    search_fields = ['member__username', 'member__first_name', 'member__last_name', 'payhero_reference']
    readonly_fields = ['date']
    ordering = ['-date']


@admin.register(ChamaProfile)
class ChamaProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'phone_number', 'joined_date', 'is_active']
    list_filter = ['role', 'is_active', 'joined_date']
    search_fields = ['user__username', 'phone_number', 'id_number']
    readonly_fields = ['joined_date']