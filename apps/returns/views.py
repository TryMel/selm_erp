from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from apps.organization.models import Warehouse
from apps.suppliers.models import Supplier
from .models import ReturnOrder
from .services import ReturnService


@require_perm('returns.view')
def returns_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'returns.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['returns.manage']}, status=403)
        try:
            rtype = request.POST['return_type']
            wh = get_object_or_404(Warehouse, pk=request.POST['warehouse_id'], store__company=request.company)
            prod = get_object_or_404(Product, pk=request.POST['product_id'])
            client = Client.objects.filter(pk=request.POST.get('client_id'), company=request.company).first() if rtype == 'CLIENT_RETURN' else None
            supplier = Supplier.objects.filter(pk=request.POST.get('supplier_id'), company=request.company).first() if rtype == 'SUPPLIER_RETURN' else None
            ReturnService.process_return(
                company=request.company, return_type=rtype, warehouse=wh, product=prod,
                quantity=request.POST['quantity'], unit_price=request.POST.get('unit_price') or prod.standard_price,
                condition=request.POST['condition'], resolution=request.POST['resolution'],
                reason=request.POST['reason'].strip(), actor=request.user, client=client, supplier=supplier,
            )
            messages.success(request, 'Retour traité (stock / quarantaine & avoir mis à jour).')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/returns/')

    return render(request, 'returns/returns.html', {
        'returns': ReturnOrder.objects.filter(company=request.company).select_related('product', 'client', 'supplier', 'warehouse'),
        'clients': Client.objects.filter(company=request.company),
        'suppliers': Supplier.objects.filter(company=request.company),
        'warehouses': Warehouse.objects.filter(store__company=request.company, is_active=True),
        'products': Product.objects.filter(is_active=True),
    })
