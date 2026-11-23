from django.db.models import Q
from django.shortcuts import render
from apps.core.permissions import require_perm
from .models import BusinessAuditLog, TechnicalLog


@require_perm('audit.view')
def audit_view(request):
    q = request.GET.get('q', '').strip()
    scope = Q(company=request.company) | Q(company__isnull=True) if request.user.is_superuser else Q(company=request.company)
    business = BusinessAuditLog.objects.select_related('actor', 'approver').filter(scope)
    if q:
        business = business.filter(Q(summary__icontains=q) | Q(action__icontains=q) | Q(document_number__icontains=q))
    technical = TechnicalLog.objects.filter(scope)[:80]
    return render(request, 'audit/audit.html', {
        'business_logs': business[:100],
        'technical_logs': technical,
        'q': q,
    })
