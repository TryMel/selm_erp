from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError
from apps.core.models import next_doc_number
from apps.core.money import round_fcfa
from apps.core.permissions import user_has_perm
from .models import CashMovement, CashRegister, CashSession

ALLOWED_MANUAL_MOVEMENTS = {'CASH_IN', 'CASH_OUT', 'EXPENSE_OUT', 'REFUND_OUT'}


class CashService:
    """Module 09 : Contrôle sessions caisse, seuil d'écart & impact comptable SYSCOHADA (658000/758000)."""

    @staticmethod
    def get_open_session(company, register=None):
        qs = CashSession.objects.select_related('register', 'register__warehouse', 'opened_by').filter(
            company=company, status='OPEN'
        )
        if register:
            qs = qs.filter(register=register)
        return qs.first()

    @staticmethod
    @transaction.atomic
    def open_session(*, register: CashRegister, opening_float, actor) -> CashSession:
        # Verrouille la caisse parente pour empêcher 2 ouvertures simultanées
        reg = CashRegister.objects.select_for_update().get(pk=register.pk)
        existing = CashSession.objects.filter(register=reg, status__in=['OPEN', 'PENDING_VALIDATION']).first()
        if existing:
            raise BusinessRuleError(f'La caisse {reg.name} a déjà une session active ({existing.number}).')

        amt = round_fcfa(opening_float)
        if amt < 0:
            raise BusinessRuleError('Le fonds initial ne peut pas être négatif.')
        number = next_doc_number(reg.company, 'CASH_SESSION')
        session = CashSession.objects.create(
            company=reg.company,
            number=number,
            register=reg,
            opened_by=actor,
            opening_float=amt,
            expected_cash=amt,
        )
        log_business(
            company=reg.company,
            action='CASH_SESSION_OPENED',
            module='CASH',
            entity_type='CashSession',
            entity_id=session.id,
            document_number=session.number,
            actor=actor,
            summary=f'Ouverture {session.number} ({reg.name}) — Fonds initial {int(amt)} FCFA',
        )
        return session

    @staticmethod
    @transaction.atomic
    def record_sale_inflow(*, session: CashSession, payment_method: str, amount, reason: str, reference: str, actor):
        ses = CashSession.objects.select_for_update().get(pk=session.pk)
        if ses.status != 'OPEN':
            raise BusinessRuleError('La session de caisse est fermée.', 'CASH_SESSION_CLOSED')
        amt = round_fcfa(amount)
        CashMovement.objects.create(
            session=ses,
            movement_type='SALE_IN',
            payment_method=payment_method,
            amount=amt,
            reason=reason,
            reference=reference,
            actor=actor,
        )
        if payment_method == 'CASH':
            ses.expected_cash = round_fcfa(ses.expected_cash + amt)
        elif payment_method in ('WAVE', 'ORANGE_MONEY', 'MTN_MOMO'):
            ses.expected_mobile = round_fcfa(ses.expected_mobile + amt)
        else:
            ses.expected_card = round_fcfa(ses.expected_card + amt)
        ses.save(update_fields=['expected_cash', 'expected_mobile', 'expected_card', 'updated_at'])

    @staticmethod
    @transaction.atomic
    def record_manual_movement(*, session: CashSession, movement_type: str, amount, reason: str, actor, reference=''):
        if movement_type not in ALLOWED_MANUAL_MOVEMENTS:
            raise BusinessRuleError(f'Type de mouvement caisse invalide : {movement_type}')
        ses = CashSession.objects.select_for_update().get(pk=session.pk)
        if ses.status != 'OPEN':
            raise BusinessRuleError('Session non ouverte.')
        amt = round_fcfa(amount)
        if amt <= 0:
            raise BusinessRuleError('Montant strictement positif requis.')
        is_out = movement_type in ('CASH_OUT', 'EXPENSE_OUT', 'REFUND_OUT')
        if is_out and amt > ses.expected_cash:
            raise BusinessRuleError(f'Solde espèces insuffisant ({int(ses.expected_cash)} FCFA).')

        mvt = CashMovement.objects.create(
            session=ses,
            movement_type=movement_type,
            payment_method='CASH',
            amount=amt,
            reason=reason,
            reference=reference,
            actor=actor,
        )
        ses.expected_cash = round_fcfa(ses.expected_cash - amt if is_out else ses.expected_cash + amt)
        ses.save(update_fields=['expected_cash', 'updated_at'])
        log_business(
            company=ses.company,
            action=f'CASH_{movement_type}',
            module='CASH',
            entity_type='CashSession',
            entity_id=ses.id,
            document_number=ses.number,
            actor=actor,
            summary=f'{movement_type} {int(amt)} FCFA sur {ses.number} — {reason}',
        )
        return mvt

    @staticmethod
    def _post_variance_accounting(session: CashSession, actor_name: str):
        variance = round_fcfa(session.cash_variance)
        if variance == 0:
            return
        from apps.accounting.services import AccountingBridge
        if variance < 0:
            abs_v = abs(variance)
            lines = [
                {'account_code': '658000', 'label': f'Déficit de caisse {session.number}', 'debit': abs_v},
                {'account_code': '571000', 'label': f'Régularisation caisse {session.number}', 'credit': abs_v},
            ]
        else:
            lines = [
                {'account_code': '571000', 'label': f'Excédent de caisse {session.number}', 'debit': variance},
                {'account_code': '758000', 'label': f'Produit divers excédent {session.number}', 'credit': variance},
            ]
        AccountingBridge.post_entry(
            company=session.company,
            journal_code='CAI',
            description=f'Écart de clôture caisse {session.number} ({int(variance)} FCFA)',
            source_doc_type='CASH_VARIANCE',
            source_doc_number=session.number,
            actor_name=actor_name,
            lines=lines,
        )

    @staticmethod
    @transaction.atomic
    def close_session(*, session: CashSession, counted_cash, counted_mobile, counted_card, variance_notes: str, actor):
        ses = CashSession.objects.select_for_update().get(pk=session.pk)
        if ses.status != 'OPEN':
            raise BusinessRuleError('Session déjà clôturée.')
        c_cash = round_fcfa(counted_cash)
        c_mob = round_fcfa(counted_mobile or 0)
        c_card = round_fcfa(counted_card or 0)
        if c_cash < 0 or c_mob < 0 or c_card < 0:
            raise BusinessRuleError('Les montants comptés ne peuvent pas être négatifs.')
        variance = round_fcfa(c_cash - ses.expected_cash)

        if variance != 0 and not (variance_notes or '').strip():
            raise BusinessRuleError(
                f'Un écart de {int(variance)} FCFA est constaté. Une justification écrite est obligatoire.'
            )

        threshold = Decimal(str(ses.company.cash_variance_threshold or 0))
        exceeds_threshold = abs(variance) > threshold
        can_auto_close = (not exceeds_threshold) or user_has_perm(actor, 'cash.validate_close')

        ses.counted_cash = c_cash
        ses.counted_mobile = c_mob
        ses.counted_card = c_card
        ses.cash_variance = variance
        ses.variance_notes = (variance_notes or '').strip()
        ses.closed_by = actor
        ses.closed_at = timezone.now()
        if can_auto_close:
            ses.status = 'CLOSED'
            ses.validated_by = actor
            ses.save()
            CashService._post_variance_accounting(ses, actor.display_name)
        else:
            ses.status = 'PENDING_VALIDATION'
            ses.save()

        log_business(
            company=ses.company,
            action='CASH_SESSION_CLOSED',
            module='CASH',
            entity_type='CashSession',
            entity_id=ses.id,
            document_number=ses.number,
            actor=actor,
            approver=actor if can_auto_close else None,
            summary=f'Clôture {ses.number} — Attendu {int(ses.expected_cash)} / Compté {int(c_cash)} / Écart {int(variance)} FCFA (seuil {int(threshold)})',
        )
        return ses

    @staticmethod
    @transaction.atomic
    def validate_closure(*, session: CashSession, approver):
        ses = CashSession.objects.select_for_update().get(pk=session.pk)
        if ses.status != 'PENDING_VALIDATION':
            raise BusinessRuleError("Cette session n'est pas en attente de validation.")
        ses.status = 'CLOSED'
        ses.validated_by = approver
        ses.save(update_fields=['status', 'validated_by', 'updated_at'])
        CashService._post_variance_accounting(ses, approver.display_name)
        log_business(
            company=ses.company,
            action='CASH_CLOSURE_VALIDATED',
            module='CASH',
            entity_type='CashSession',
            entity_id=ses.id,
            document_number=ses.number,
            actor=approver,
            approver=approver,
            summary=f'Validation responsable clôture {ses.number} (écart {int(ses.cash_variance)} FCFA comptabilisé)',
        )
        return ses
