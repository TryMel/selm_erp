import csv
from django.http import HttpResponse
from django.shortcuts import render
from apps.core.money import sanitize_csv_cell
from apps.core.permissions import require_perm, user_has_perm
from .services import build_dashboard_data


@require_perm('dashboard.view')
def dashboard_view(request):
    can_margins = user_has_perm(request.user, 'reporting.view_margins')
    data = build_dashboard_data(request.company, can_view_margins=can_margins)
    return render(request, 'reporting/dashboard.html', {'data': data})


@require_perm('reporting.view')
def reporting_view(request):
    can_margins = user_has_perm(request.user, 'reporting.view_margins')
    data = build_dashboard_data(request.company, can_view_margins=can_margins)
    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = 'attachment; filename="rapport-rentabilite-quincapro.csv"'
        resp.write('\ufeff')
        w = csv.writer(resp, delimiter=';')
        w.writerow(['Article', 'Qté Base', 'CA TTC (FCFA)', 'Coût (FCFA)', 'Marge HT (FCFA)', 'Taux %'])
        for p in data['top_products']:
            w.writerow([sanitize_csv_cell(p['name']), p['qty'], p['revenue'], p['cost'] or '', p['margin'] or '', p['margin_rate'] or ''])
        return resp
    return render(request, 'reporting/reporting.html', {'data': data})
