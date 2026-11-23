from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from .models import CashRegister, CashSession
from .services import CashService


@require_perm('cash.view')
def cash_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'open':
                if not user_has_perm(request.user, 'cash.operate'):
                    return render(request, 'errors/403.html', {'required_perms': ['cash.operate']}, status=403)
                reg = get_object_or_404(CashRegister, pk=request.POST['register_id'], store__company=request.company)
                CashService.open_session(register=reg, opening_float=request.POST.get('opening_float') or 0, actor=request.user)
                messages.success(request, f'Caisse {reg.name} ouverte.')
            elif action == 'movement':
                if not user_has_perm(request.user, 'cash.operate'):
                    return render(request, 'errors/403.html', {'required_perms': ['cash.operate']}, status=403)
                ses = get_object_or_404(CashSession, pk=request.POST['session_id'], company=request.company)
                CashService.record_manual_movement(
                    session=ses, movement_type=request.POST['movement_type'], amount=request.POST['amount'],
                    reason=request.POST['reason'].strip(), actor=request.user,
                )
                messages.success(request, 'Mouvement enregistré.')
            elif action == 'close':
                if not user_has_perm(request.user, 'cash.operate'):
                    return render(request, 'errors/403.html', {'required_perms': ['cash.operate']}, status=403)
                ses = get_object_or_404(CashSession, pk=request.POST['session_id'], company=request.company)
                updated = CashService.close_session(
                    session=ses,
                    counted_cash=request.POST.get('counted_cash') or 0,
                    counted_mobile=request.POST.get('counted_mobile') or 0,
                    counted_card=request.POST.get('counted_card') or 0,
                    variance_notes=request.POST.get('variance_notes', ''),
                    actor=request.user,
                )
                if updated.status == 'PENDING_VALIDATION':
                    messages.warning(request, f'Écart supérieur au seuil ({request.company.cash_variance_threshold} FCFA) : validation responsable requise.')
                else:
                    messages.success(request, f'Session {ses.number} clôturée (écriture SYSCOHADA générée si écart).')
            elif action == 'validate':
                if not user_has_perm(request.user, 'cash.validate_close'):
                    return render(request, 'errors/403.html', {'required_perms': ['cash.validate_close']}, status=403)
                ses = get_object_or_404(CashSession, pk=request.POST['session_id'], company=request.company)
                CashService.validate_closure(session=ses, approver=request.user)
                messages.success(request, f'Clôture {ses.number} validée et comptabilisée.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/cash/')

    registers = CashRegister.objects.filter(store__company=request.company).select_related('warehouse')
    active_sessions = CashSession.objects.filter(
        company=request.company, status__in=['OPEN', 'PENDING_VALIDATION']
    ).select_related('register', 'opened_by')
    open_by_reg = {s.register_id: s for s in active_sessions}
    sessions = CashSession.objects.filter(company=request.company).select_related('register', 'opened_by', 'validated_by')[:25]
    return render(request, 'cash/cash.html', {
        'registers': [(r, open_by_reg.get(r.id)) for r in registers],
        'sessions': sessions,
    })
