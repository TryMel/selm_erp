from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from apps.catalog.models import Category, Product
from apps.clients.models import Client
from apps.core.permissions import require_perm, user_has_perm
from .models import PricingRule
from .services import PricingEngine


@require_perm('pricing.view')
def pricing_view(request):
    sim_result = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'simulate':
            prod = get_object_or_404(Product, pk=request.POST['product_id'])
            cli = Client.objects.filter(pk=request.POST.get('client_id'), company=request.company).first() if request.POST.get('client_id') else None
            qty = request.POST.get('quantity') or 1
            sim_result = PricingEngine.resolve(request.company, prod, qty, client=cli)
            sim_result['product'] = prod
            sim_result['quantity'] = qty
            sim_result['client'] = cli
        elif action == 'create_rule':
            if not user_has_perm(request.user, 'pricing.manage'):
                return render(request, 'errors/403.html', {'required_perms': ['pricing.manage']}, status=403)
            cli_id = request.POST.get('client_id')
            cli = Client.objects.filter(pk=cli_id, company=request.company).first() if cli_id else None
            PricingRule.objects.create(
                company=request.company,
                name=request.POST['name'].strip(),
                rule_type=request.POST['rule_type'],
                priority=int(request.POST.get('priority') or 40),
                product_id=request.POST.get('product_id') or None,
                category_id=request.POST.get('category_id') or None,
                client=cli,
                client_segment=request.POST.get('client_segment') or '',
                min_quantity=request.POST.get('min_quantity') or 1,
                price_mode=request.POST['price_mode'],
                value=request.POST['value'],
            )
            messages.success(request, 'Règle de tarification créée.')
            return redirect('/pricing/')
        elif action == 'delete_rule' and user_has_perm(request.user, 'pricing.manage'):
            PricingRule.objects.filter(pk=request.POST['rule_id'], company=request.company).delete()
            messages.success(request, 'Règle supprimée.')
            return redirect('/pricing/')

    return render(request, 'pricing/pricing.html', {
        'rules': PricingRule.objects.filter(company=request.company).select_related('product', 'category', 'client'),
        'products': Product.objects.filter(is_active=True),
        'categories': Category.objects.filter(is_active=True),
        'clients': Client.objects.filter(company=request.company),
        'sim_result': sim_result,
    })
