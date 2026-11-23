from django.contrib import messages
from django.shortcuts import redirect, render
from apps.core.exceptions import BusinessRuleError
from apps.core.money import parse_int
from apps.core.permissions import require_perm, user_has_perm
from .models import Supplier
from .services import SupplierDebtService


@require_perm('suppliers.view')
def suppliers_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'suppliers.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['suppliers.manage']}, status=403)
        action = request.POST.get('action')
        try:
            if action == 'create':
                code = f"{request.company.code}-FRN-{Supplier.objects.filter(company=request.company).count() + 1:03d}"
                Supplier.objects.create(
                    company=request.company,
                    code=code,
                    name=request.POST.get('name', '').strip(),
                    contact_name=request.POST.get('contact_name', '').strip(),
                    phone=request.POST.get('phone', '').strip(),
                    avg_lead_days=parse_int(request.POST.get('avg_lead_days'), 'délai', default=3),
                    conformity_rate=request.POST.get('conformity_rate') or 98,
                )
                messages.success(request, f'Fournisseur {code} créé.')
            elif action == 'pay':
                SupplierDebtService.pay_supplier(
                    supplier_id=parse_int(request.POST.get('supplier_id'), 'fournisseur'),
                    amount=request.POST.get('amount'),
                    payment_method=request.POST.get('payment_method', 'BANK_TRANSFER'),
                    reference=request.POST.get('reference', '').strip(),
                    actor=request.user,
                    company=request.company,
                )
                messages.success(request, 'Règlement fournisseur enregistré et comptabilisé.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/suppliers/')

    return render(request, 'suppliers/suppliers.html', {
        'suppliers': Supplier.objects.filter(company=request.company),
    })
