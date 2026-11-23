from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from apps.inventory.models import StockLevel
from apps.organization.models import Warehouse
from apps.taxation.models import TaxRule
from .models import Brand, Category, Product, ProductPackaging, Unit, UnitFamily


@require_perm('catalog.view')
def catalog_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'catalog.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['catalog.manage']}, status=403)
        action = request.POST.get('action')
        try:
            if action == 'create_product':
                p = Product.objects.create(
                    sku=request.POST['sku'].strip().upper(),
                    barcode=request.POST.get('barcode', '').strip() or None,
                    name=request.POST['name'].strip(),
                    category_id=request.POST['category_id'],
                    brand_id=request.POST.get('brand_id') or None,
                    base_unit_id=request.POST['base_unit_id'],
                    tax_rule_id=request.POST['tax_rule_id'],
                    purchase_cost=int(request.POST.get('purchase_cost') or 0),
                    standard_price=int(request.POST.get('standard_price') or 0),
                    min_stock=int(request.POST.get('min_stock') or 5),
                    is_favorite=request.POST.get('is_favorite') == 'on',
                )
                for wh in Warehouse.objects.filter(store__company=request.company, is_active=True):
                    StockLevel.objects.get_or_create(product=p, warehouse=wh, defaults={'cmup': p.purchase_cost})
                log_business(company=request.company, action='PRODUCT_CREATED', module='CATALOG', entity_type='Product', entity_id=p.id, actor=request.user, summary=f'Création article [{p.sku}] {p.name}')
                messages.success(request, f'Article {p.name} créé.')
            elif action == 'add_packaging':
                p = get_object_or_404(Product, pk=request.POST['product_id'])
                ProductPackaging.objects.create(
                    product=p,
                    unit_id=request.POST['unit_id'],
                    label=request.POST['label'].strip(),
                    conversion_factor=request.POST['conversion_factor'],
                    standard_price=int(request.POST['standard_price']) if request.POST.get('standard_price') else None,
                    barcode=request.POST.get('barcode', '').strip() or None,
                )
                messages.success(request, f'Conditionnement ajouté sur {p.sku}.')
            elif action == 'toggle_active':
                p = get_object_or_404(Product, pk=request.POST['product_id'])
                p.is_active = not p.is_active
                p.save(update_fields=['is_active'])
                messages.success(request, f'Article {p.sku} {"réactivé" if p.is_active else "désactivé (historique conservé)"}.')
        except (ValidationError, BusinessRuleError, Exception) as exc:
            messages.error(request, str(exc))
        return redirect('/catalog/')

    q = request.GET.get('q', '').strip()
    products = Product.objects.select_related('category', 'brand', 'base_unit', 'tax_rule').prefetch_related('packagings__unit')
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q))
    products = products.annotate(
        total_stock=Sum('stock_levels__physical_qty', filter=Q(stock_levels__warehouse__store__company=request.company))
    )

    return render(request, 'catalog/catalog.html', {
        'products': products,
        'q': q,
        'categories': Category.objects.filter(is_active=True),
        'brands': Brand.objects.filter(is_active=True),
        'units': Unit.objects.select_related('family').all(),
        'unit_families': UnitFamily.objects.prefetch_related('units').all(),
        'tax_rules': TaxRule.objects.filter(is_active=True),
    })
