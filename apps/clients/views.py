from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from apps.cash.services import CashService
from apps.core.exceptions import BusinessRuleError
from apps.core.money import parse_int
from apps.core.permissions import require_perm, user_has_perm
from .models import Client
from .services import CreditService


@require_perm('clients.view')
def clients_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'clients.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['clients.manage']}, status=403)
        action = request.POST.get('action')
        try:
            if action == 'create':
                code = f"{request.company.code}-CLI-{Client.objects.filter(company=request.company).count() + 1:03d}"
                Client.objects.create(
                    company=request.company,
                    code=code,
                    name=request.POST.get('name', '').strip(),
                    segment=request.POST.get('segment', 'PARTICULIER'),
                    phone=request.POST.get('phone', '').strip(),
                    address=request.POST.get('address', '').strip(),
                    credit_limit=max(0, parse_int(request.POST.get('credit_limit'), 'plafond', default=0)),
                    tax_exempt=request.POST.get('tax_exempt') == 'on',
                    exemption_certificate=request.POST.get('exemption_certificate', '').strip(),
                )
                messages.success(request, f'Client {code} créé.')
            elif action == 'collect_payment':
                ses = CashService.get_open_session(request.company)
                CreditService.settle_credit(
                    client_id=parse_int(request.POST.get('client_id'), 'client'),
                    amount=request.POST.get('amount'),
                    payment_method=request.POST.get('payment_method', 'CASH'),
                    reference=request.POST.get('reference', '').strip(),
                    note='Recouvrement créance client',
                    actor=request.user,
                    cash_session=ses,
                    company=request.company,
                    allocate_fifo=True,
                )
                messages.success(request, 'Créance encaissée (factures soldées FIFO et comptabilité mise à jour).')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/clients/')

    q = request.GET.get('q', '').strip()
    clients = Client.objects.filter(company=request.company).prefetch_related('credit_ledger')
    if q:
        clients = clients.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(phone__icontains=q))
    return render(request, 'clients/clients.html', {'clients': clients, 'q': q})
