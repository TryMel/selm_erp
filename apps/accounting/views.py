import csv
from decimal import Decimal
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import render
from apps.core.money import sanitize_csv_cell
from apps.core.permissions import require_perm
from .models import Account, JournalEntry, JournalLine


@require_perm('accounting.view')
def accounting_view(request):
    accounts = list(Account.objects.all())
    agg = {
        row['account_id']: (row['td'] or Decimal('0'), row['tc'] or Decimal('0'))
        for row in JournalLine.objects.filter(entry__company=request.company)
        .values('account_id')
        .annotate(td=Sum('debit'), tc=Sum('credit'))
    }
    balance = []
    tot_debit = Decimal('0')
    tot_credit = Decimal('0')
    for a in accounts:
        d, c = agg.get(a.id, (Decimal('0'), Decimal('0')))
        tot_debit += d
        tot_credit += c
        balance.append({'account': a, 'debit': d, 'credit': c, 'balance': d - c})

    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="balance-syscohada-{request.company.code}.csv"'
        resp.write('\ufeff')
        w = csv.writer(resp, delimiter=';')
        w.writerow(['Compte', 'Intitulé', 'Classe', 'Total Débit', 'Total Crédit', 'Solde'])
        for row in balance:
            w.writerow([sanitize_csv_cell(row['account'].code), sanitize_csv_cell(row['account'].name), row['account'].account_class, row['debit'], row['credit'], row['balance']])
        return resp

    entries = JournalEntry.objects.filter(company=request.company).prefetch_related('lines__account')[:60]
    return render(request, 'accounting/accounting.html', {
        'entries': entries,
        'balance': balance,
        'tot_debit': tot_debit,
        'tot_credit': tot_credit,
    })
