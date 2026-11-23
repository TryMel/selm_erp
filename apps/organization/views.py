from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError
from apps.core.money import parse_int
from apps.core.permissions import require_perm, user_has_perm
from apps.taxation.models import TaxRule
from .middleware import user_can_switch_companies
from .models import Company, Location, Store, Warehouse, Zone


@login_required
def switch_company_view(request):
    if request.method == 'POST' and user_can_switch_companies(request.user):
        cid = request.POST.get('company_id')
        comp = Company.objects.filter(pk=cid, is_active=True).first()
        if comp:
            request.session['active_company_id'] = comp.id
            messages.info(request, f'Société active basculée sur : {comp.legal_name}')
    elif request.method == 'POST':
        messages.error(request, 'Seule la Direction peut basculer entre sociétés juridiques.')

    referer = request.META.get('HTTP_REFERER') or '/'
    if not url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        referer = '/'
    return redirect(referer)


@require_perm('organization.view')
def organization_view(request):
    company = request.company
    if request.method == 'POST':
        if not user_has_perm(request.user, 'organization.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['organization.manage']}, status=403)
        action = request.POST.get('action')
        try:
            if action == 'update_rules':
                neg_policy = request.POST.get('negative_stock_policy', company.negative_stock_policy)
                if neg_policy not in dict(Company.NEG_STOCK_CHOICES):
                    raise BusinessRuleError('Politique stock négatif invalide.')
                val_method = request.POST.get('valuation_method', company.valuation_method)
                if val_method not in dict(Company.VALUATION_CHOICES):
                    raise BusinessRuleError('Méthode de valorisation invalide.')
                threshold = parse_int(request.POST.get('cash_variance_threshold'), 'seuil caisse', default=2000)
                if threshold < 0:
                    raise BusinessRuleError("Le seuil d'écart de caisse ne peut pas être négatif.")

                company.negative_stock_policy = neg_policy
                company.valuation_method = val_method
                company.cash_variance_threshold = threshold
                company.fne_enabled = request.POST.get('fne_enabled') == 'on'
                company.ncc = request.POST.get('ncc', company.ncc).strip()
                company.rccm = request.POST.get('rccm', company.rccm).strip()
                company.save()
                log_business(
                    company=company, action='COMPANY_RULES_UPDATED', module='ORGANIZATION',
                    entity_type='Company', entity_id=company.id, actor=request.user,
                    summary=f'Règles {company.code} : stock négatif={company.negative_stock_policy}, valo={company.valuation_method}, seuil={threshold}',
                )
                messages.success(request, 'Paramètres de gouvernance enregistrés.')
            elif action == 'create_warehouse':
                store = get_object_or_404(Store, pk=parse_int(request.POST.get('store_id'), 'magasin'), company=company)
                Warehouse.objects.create(
                    store=store,
                    code=request.POST.get('code', '').strip().upper(),
                    name=request.POST.get('name', '').strip(),
                    address=request.POST.get('address', '').strip(),
                )
                messages.success(request, 'Nouveau dépôt ajouté.')
            elif action == 'create_location':
                zone = get_object_or_404(Zone, pk=parse_int(request.POST.get('zone_id'), 'zone'), warehouse__store__company=company)
                Location.objects.create(
                    zone=zone,
                    code=request.POST.get('code', '').strip().upper(),
                    label=request.POST.get('label', '').strip(),
                )
                messages.success(request, 'Emplacement créé.')
        except (BusinessRuleError, IntegrityError) as exc:
            messages.error(request, str(exc))
        return redirect('/organization/')

    return render(request, 'organization/overview.html', {
        'company': company,
        'stores': Store.objects.filter(company=company).prefetch_related('warehouses__zones__locations'),
        'zones': Zone.objects.filter(warehouse__store__company=company).select_related('warehouse'),
        'tax_rules': TaxRule.objects.all(),
    })
