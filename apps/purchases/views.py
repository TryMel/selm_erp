from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from apps.catalog.models import Product
from apps.core.exceptions import BusinessRuleError
from apps.core.money import parse_int
from apps.core.permissions import require_perm, user_has_perm
from apps.organization.models import Warehouse
from apps.suppliers.models import Supplier
from .models import PurchaseOrder
from .services import PurchaseService


@require_perm('purchases.view')
def purchases_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                if not user_has_perm(request.user, 'purchases.manage'):
                    return render(request, 'errors/403.html', {'required_perms': ['purchases.manage']}, status=403)
                sup = get_object_or_404(Supplier, pk=parse_int(request.POST.get('supplier_id'), 'fournisseur'), company=request.company)
                wh = get_object_or_404(Warehouse, pk=parse_int(request.POST.get('warehouse_id'), 'dépôt'), store__company=request.company)
                pids = request.POST.getlist('product_id')
                pkgs = request.POST.getlist('packaging_id')
                qtys = request.POST.getlist('ordered_qty')
                costs = request.POST.getlist('unit_cost')
                lines_input = []
                for idx, pid in enumerate(pids):
                    if not pid:
                        continue
                    lines_input.append({
                        'product_id': parse_int(pid, 'article'),
                        'packaging_id': parse_int(pkgs[idx], 'conditionnement') if (idx < len(pkgs) and pkgs[idx]) else None,
                        'ordered_qty': qtys[idx] if idx < len(qtys) else 1,
                        'unit_cost': costs[idx] if idx < len(costs) else 0,
                    })
                po = PurchaseService.create_order(
                    company=request.company,
                    supplier=sup,
                    warehouse=wh,
                    lines_input=lines_input,
                    actor=request.user,
                    notes=request.POST.get('notes', '').strip(),
                )
                messages.success(request, f'Commande fournisseur {po.number} créée ({len(lines_input)} ligne(s)).')
            elif action == 'receive':
                if not user_has_perm(request.user, 'purchases.receive'):
                    return render(request, 'errors/403.html', {'required_perms': ['purchases.receive']}, status=403)
                po_id = parse_int(request.POST.get('po_id'), 'commande')
                qty_map = {}
                for k, v in request.POST.items():
                    if k.startswith('rec_') and v not in ('', '0', '0.0'):
                        qty_map[parse_int(k.split('_')[1], 'ligne')] = v
                PurchaseService.receive_order(
                    po_id=po_id,
                    qty_by_line_id=qty_map,
                    supplier_invoice_ref=request.POST.get('supplier_invoice_ref', ''),
                    actor=request.user,
                    company=request.company,
                )
                messages.success(request, 'Réception validée : stock converti en unités de base & CMUP HT recalculé.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/purchases/')

    prefill_product_id = request.GET.get('product_id', '')
    return render(request, 'purchases/purchases.html', {
        'orders': PurchaseOrder.objects.filter(company=request.company).select_related('supplier', 'warehouse').prefetch_related('lines'),
        'suppliers': Supplier.objects.filter(company=request.company, is_active=True),
        'warehouses': Warehouse.objects.filter(store__company=request.company, is_active=True),
        'products': Product.objects.filter(is_active=True).prefetch_related('packagings'),
        'prefill_product_id': prefill_product_id,
    })
