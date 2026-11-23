from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from apps.catalog.models import Product
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from apps.organization.models import Warehouse
from .models import StockLevel, StockMovement
from .services import StockService


@require_perm('inventory.view')
def inventory_view(request):
    warehouses = Warehouse.objects.filter(store__company=request.company, is_active=True)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            product = get_object_or_404(Product, pk=request.POST['product_id'])
            if action == 'transfer':
                if not user_has_perm(request.user, 'inventory.transfer'):
                    return render(request, 'errors/403.html', {'required_perms': ['inventory.transfer']}, status=403)
                src = get_object_or_404(Warehouse, pk=request.POST['from_warehouse_id'], store__company=request.company)
                dst = get_object_or_404(Warehouse, pk=request.POST['to_warehouse_id'], store__company=request.company)
                StockService.transfer(
                    product=product, from_warehouse=src, to_warehouse=dst,
                    qty_base=request.POST['quantity'], reason=request.POST.get('reason', '').strip(), actor=request.user,
                )
                messages.success(request, 'Transfert inter-dépôts effectué.')
            elif action == 'adjust':
                if not user_has_perm(request.user, 'inventory.adjust'):
                    return render(request, 'errors/403.html', {'required_perms': ['inventory.adjust']}, status=403)
                wh = get_object_or_404(Warehouse, pk=request.POST['warehouse_id'], store__company=request.company)
                mtype = request.POST['movement_type']
                qty = request.POST['quantity']
                reason = request.POST['reason'].strip()
                if mtype == 'ADJUSTMENT_IN':
                    StockService.receive(
                        product=product, warehouse=wh, qty_base=qty, unit_cost_base=product.purchase_cost,
                        movement_type='ADJUSTMENT_IN', unit_label=product.base_unit.name, conversion_factor=1,
                        document_type='ADJUSTMENT', document_number='', reason=reason, actor=request.user, recalculate_cmup=False,
                    )
                elif mtype == 'QUARANTINE':
                    StockService.quarantine(product=product, warehouse=wh, qty_base=qty, reason=reason, actor=request.user)
                else:
                    StockService.deduct(
                        product=product, warehouse=wh, qty_base=qty, movement_type=mtype,
                        unit_label=product.base_unit.name, conversion_factor=1,
                        document_type='ADJUSTMENT', document_number='', reason=reason, actor=request.user,
                    )
                messages.success(request, 'Ajustement de stock enregistré et tracé.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/inventory/')

    wh_id = request.GET.get('warehouse_id')
    levels = StockLevel.objects.select_related('product', 'product__base_unit', 'warehouse', 'location').filter(
        warehouse__store__company=request.company, product__is_active=True
    )
    if wh_id:
        levels = levels.filter(warehouse_id=wh_id)
    movements = StockMovement.objects.select_related('product', 'warehouse', 'actor').filter(company=request.company)[:60]
    return render(request, 'inventory/inventory.html', {
        'levels': levels,
        'movements': movements,
        'warehouses': warehouses,
        'selected_wh': wh_id or '',
        'products': Product.objects.filter(is_active=True),
    })
