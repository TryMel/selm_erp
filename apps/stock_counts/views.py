from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from apps.organization.models import Warehouse
from .models import StockCount
from .services import StockCountService


@require_perm('stock_counts.view')
def stock_counts_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'stock_counts.count'):
            return render(request, 'errors/403.html', {'required_perms': ['stock_counts.count']}, status=403)
        try:
            wh = get_object_or_404(Warehouse, pk=request.POST['warehouse_id'], store__company=request.company)
            sc = StockCountService.create_count(
                company=request.company, warehouse=wh, mode=request.POST.get('mode', 'DOUBLE'), actor=request.user
            )
            messages.success(request, f'Inventaire {sc.number} créé.')
            return redirect(f'/stock-counts/{sc.id}/')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
            return redirect('/stock-counts/')

    return render(request, 'stock_counts/list.html', {
        'counts': StockCount.objects.filter(company=request.company).select_related('warehouse', 'created_by'),
        'warehouses': Warehouse.objects.filter(store__company=request.company, is_active=True),
    })


@require_perm('stock_counts.view')
def stock_count_detail_view(request, pk: int):
    sc = get_object_or_404(StockCount.objects.select_related('warehouse'), pk=pk, company=request.company)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action in ('pass_1', 'pass_2'):
                if not user_has_perm(request.user, 'stock_counts.count'):
                    return render(request, 'errors/403.html', {'required_perms': ['stock_counts.count']}, status=403)
                counts = {}
                for k, v in request.POST.items():
                    if k.startswith('line_'):
                        counts[int(k.split('_')[1])] = v
                StockCountService.record_pass(
                    stock_count=sc, pass_number=1 if action == 'pass_1' else 2, counts_by_line_id=counts, actor=request.user
                )
                messages.success(request, 'Comptage enregistré.')
            elif action == 'validate':
                if not user_has_perm(request.user, 'stock_counts.validate'):
                    return render(request, 'errors/403.html', {'required_perms': ['stock_counts.validate']}, status=403)
                StockCountService.validate_count(stock_count=sc, actor=request.user)
                messages.success(request, f'Inventaire {sc.number} validé : stocks ajustés.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect(f'/stock-counts/{sc.id}/')

    return render(request, 'stock_counts/detail.html', {
        'sc': sc,
        'lines': sc.lines.select_related('product', 'product__base_unit'),
    })
