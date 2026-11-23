from django.contrib import messages
from django.shortcuts import redirect, render
from apps.cash.services import CashService
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from .models import Expense
from .services import ExpenseService


@require_perm('expenses.view')
def expenses_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                if not user_has_perm(request.user, 'expenses.create'):
                    return render(request, 'errors/403.html', {'required_perms': ['expenses.create']}, status=403)
                ses = CashService.get_open_session(request.company)
                ExpenseService.create_expense(
                    company=request.company,
                    category=request.POST['category'],
                    description=request.POST['description'].strip(),
                    amount=request.POST['amount'],
                    account_type=request.POST['account_type'],
                    actor=request.user,
                    reference=request.POST.get('reference', '').strip(),
                    cash_session=ses if request.POST['account_type'] == 'CAISSE' else None,
                )
                messages.success(request, 'Dépense soumise pour validation.')
            elif action in ('approve', 'reject'):
                if not user_has_perm(request.user, 'expenses.approve'):
                    return render(request, 'errors/403.html', {'required_perms': ['expenses.approve']}, status=403)
                ExpenseService.decide(
                    expense_id=int(request.POST['expense_id']),
                    approve=(action == 'approve'),
                    approver=request.user,
                    company=request.company,
                )
                messages.success(request, 'Décision enregistrée et comptabilisée.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/expenses/')

    return render(request, 'expenses/expenses.html', {
        'expenses': Expense.objects.filter(company=request.company).select_related('requested_by', 'approved_by'),
    })
