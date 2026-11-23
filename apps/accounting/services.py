from decimal import Decimal
from django.db import transaction
from apps.core.exceptions import BusinessRuleError
from apps.core.models import next_doc_number
from apps.core.money import round_fcfa
from .models import Account, JournalEntry, JournalLine


def treasury_account_for(payment_method: str) -> str:
    return {
        'CASH': '571000',
        'WAVE': '522000',
        'ORANGE_MONEY': '522000',
        'MTN_MOMO': '522000',
        'CARD': '521000',
        'BANK_TRANSFER': '521000',
    }.get(payment_method, '411000')


class AccountingBridge:
    """Pont comptable découplé SYSCOHADA — garantit strictement Débit == Crédit."""

    @staticmethod
    @transaction.atomic
    def post_entry(*, company, journal_code: str, description: str, lines: list[dict], source_doc_type='', source_doc_number='', actor_name='', strict=False):
        clean = [
            {
                'account_code': l['account_code'],
                'label': l.get('label', description)[:200],
                'debit': round_fcfa(l.get('debit', 0)),
                'credit': round_fcfa(l.get('credit', 0)),
            }
            for l in lines
            if round_fcfa(l.get('debit', 0)) > 0 or round_fcfa(l.get('credit', 0)) > 0
        ]
        if len(clean) < 2:
            if strict:
                raise BusinessRuleError('Une écriture en partie double exige au moins 2 lignes.')
            return None

        tot_d = sum((l['debit'] for l in clean), Decimal('0'))
        tot_c = sum((l['credit'] for l in clean), Decimal('0'))
        if tot_d <= 0 or tot_d != tot_c:
            if strict:
                raise BusinessRuleError(f'Écriture déséquilibrée : Débit {int(tot_d)} ≠ Crédit {int(tot_c)}.')
            return None

        codes = {l['account_code'] for l in clean}
        acc_map = {a.code: a for a in Account.objects.filter(code__in=codes)}
        if len(acc_map) != len(codes):
            if strict:
                raise BusinessRuleError('Un ou plusieurs comptes SYSCOHADA sont introuvables.')
            return None

        entry = JournalEntry.objects.create(
            company=company,
            number=next_doc_number(company, 'JOURNAL_ENTRY'),
            journal_code=journal_code,
            description=description[:255],
            source_doc_type=source_doc_type,
            source_doc_number=source_doc_number,
            actor_name=actor_name,
        )
        JournalLine.objects.bulk_create([
            JournalLine(
                entry=entry,
                account=acc_map[l['account_code']],
                label=l['label'],
                debit=l['debit'],
                credit=l['credit'],
            )
            for l in clean
        ])
        return entry
