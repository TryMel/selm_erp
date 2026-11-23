import json
from decimal import InvalidOperation
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.cash.services import CashService
from apps.catalog.models import Category, Product, ProductPackaging
from apps.clients.models import Client
from apps.core.exceptions import BusinessRuleError, ManagerOverrideRequiredError
from apps.core.money import parse_int
from apps.core.permissions import require_perm, user_has_perm
from apps.inventory.models import StockLevel
from apps.organization.models import Warehouse
from apps.pricing.services import PricingEngine
from .models import DeliveryOrder, SalesDocument
from .services import SalesService


@require_perm('pos.sell')
def pos_view(request):
    session = CashService.get_open_session(request.company)
    wh = session.register.warehouse if session else Warehouse.objects.filter(store__company=request.company, is_default=True).first()
    levels = {sl.product_id: sl.available_qty for sl in StockLevel.objects.filter(warehouse=wh)} if wh else {}

    products = list(
        Product.objects.filter(is_active=True)
        .select_related('category', 'base_unit', 'tax_rule')
        .prefetch_related('packagings__unit')
    )
    catalog_data = [
        {
            'id': p.id,
            'sku': p.sku,
            'barcode': p.barcode or '',
            'name': p.name,
            'category_id': p.category_id,
            'standard_price': int(p.standard_price),
            'unit_name': p.base_unit.name,
            'unit_symbol': p.base_unit.symbol,
            'available': float(levels.get(p.id, 0)),
            'is_favorite': p.is_favorite,
            'packagings': [
                {
                    'id': pk.id,
                    'label': pk.label,
                    'factor': float(pk.conversion_factor),
                    'barcode': pk.barcode or '',
                    'price': int(pk.standard_price) if pk.standard_price is not None else int(p.standard_price * pk.conversion_factor),
                }
                for pk in p.packagings.all()
            ],
        }
        for p in products
    ]

    return render(request, 'sales/pos.html', {
        'session': session,
        'categories': Category.objects.filter(is_active=True),
        'clients': Client.objects.filter(company=request.company, status='ACTIVE'),
        'catalog_data': catalog_data,
        'max_discount': request.user.max_discount_percent,
    })


@require_perm('pos.sell')
def pos_resolve_price_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'POST requis'}, status=405)
    try:
        body = json.loads(request.body.decode('utf-8'))
        product = get_object_or_404(Product, pk=parse_int(body.get('product_id'), 'article'))
        pkg = ProductPackaging.objects.filter(pk=body.get('packaging_id'), product=product).first() if body.get('packaging_id') else None
        client = Client.objects.filter(pk=body.get('client_id'), company=request.company).first() if body.get('client_id') else None
        res = PricingEngine.resolve(request.company, product, body.get('quantity', 1), packaging=pkg, client=client)
        return JsonResponse({
            'ok': True,
            'unit_label': res['unit_label'],
            'conversion_factor': float(res['conversion_factor']),
            'base_price': int(res['base_price']),
            'resolved_price': int(res['resolved_price']),
            'applied_rule': res['applied_rule'],
        })
    except (json.JSONDecodeError, BusinessRuleError, KeyError, ValueError, TypeError, InvalidOperation) as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)


@require_perm('pos.sell')
def pos_checkout_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'POST requis'}, status=405)
    try:
        body = json.loads(request.body.decode('utf-8'))
        doc = SalesService.pos_checkout(
            company=request.company,
            session_id=parse_int(body.get('session_id'), 'session caisse'),
            client_id=body.get('client_id') or None,
            raw_lines=body.get('lines', []),
            payments=body.get('payments', []),
            actor=request.user,
            override_username=body.get('override_username', ''),
            override_secret=body.get('override_secret', ''),
            notes=body.get('notes', ''),
            ip_address=request.META.get('REMOTE_ADDR', ''),
        )
        return JsonResponse({
            'ok': True,
            'id': doc.id,
            'number': doc.number,
            'total_ttc': int(doc.total_ttc),
            'fne_reference': doc.fne_reference,
            'receipt_url': f'/sales/{doc.id}/',
        }, status=201)
    except ManagerOverrideRequiredError as exc:
        return JsonResponse({'ok': False, 'code': exc.code, 'message': exc.message, 'reasons': exc.reasons}, status=403)
    except (json.JSONDecodeError, BusinessRuleError, KeyError, ValueError, TypeError, InvalidOperation) as exc:
        code = getattr(exc, 'code', 'BAD_REQUEST')
        msg = getattr(exc, 'message', 'Requête JSON invalide.')
        return JsonResponse({'ok': False, 'code': code, 'message': msg}, status=400)


@require_perm('sales.view')
def sales_list_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'sales.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['sales.manage']}, status=403)
        try:
            client = get_object_or_404(Client, pk=parse_int(request.POST.get('client_id'), 'client'), company=request.company)
            wh = get_object_or_404(Warehouse, pk=parse_int(request.POST.get('warehouse_id'), 'dépôt'), store__company=request.company)
            prod_ids = request.POST.getlist('product_id')
            qtys = request.POST.getlist('quantity')
            discs = request.POST.getlist('discount_percent')
            raw_lines = []
            for idx, (pid, q) in enumerate(zip(prod_ids, qtys)):
                if pid and q:
                    raw_lines.append({
                        'product_id': parse_int(pid, 'article'),
                        'quantity': q,
                        'discount_percent': discs[idx] if idx < len(discs) else 0,
                    })
            doc = SalesService.create_document(
                company=request.company,
                doc_type=request.POST.get('doc_type', 'QUOTE'),
                client=client,
                warehouse=wh,
                raw_lines=raw_lines,
                actor=request.user,
                notes=request.POST.get('notes', ''),
            )
            messages.success(request, f'Document {doc.number} créé ({len(raw_lines)} ligne(s)).')
            return redirect(f'/sales/{doc.id}/')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
            return redirect('/sales/')

    doc_type = request.GET.get('type', '')
    q = request.GET.get('q', '').strip()
    docs = SalesDocument.objects.filter(company=request.company).select_related('client', 'warehouse', 'source_doc')
    if doc_type:
        docs = docs.filter(doc_type=doc_type)
    if q:
        docs = docs.filter(number__icontains=q) | docs.filter(client__name__icontains=q)

    return render(request, 'sales/list.html', {
        'docs': docs[:100],
        'doc_type': doc_type,
        'q': q,
        'clients': Client.objects.filter(company=request.company, status='ACTIVE'),
        'warehouses': Warehouse.objects.filter(store__company=request.company, is_active=True),
        'products': Product.objects.filter(is_active=True),
    })


@require_perm('sales.view')
def sales_detail_view(request, pk: int):
    doc = get_object_or_404(
        SalesDocument.objects.select_related('client', 'warehouse', 'seller', 'approved_by', 'source_doc'),
        pk=pk, company=request.company,
    )
    if request.method == 'POST':
        if not user_has_perm(request.user, 'sales.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['sales.manage']}, status=403)
        action = request.POST.get('action')
        try:
            if action == 'transform':
                new_doc = SalesService.transform_document(
                    source_id=doc.id,
                    target_type=request.POST.get('target_type', ''),
                    actor=request.user,
                    delivery_address=request.POST.get('delivery_address', ''),
                    company=request.company,
                )
                messages.success(request, f'Transformé sans ressaisie → {new_doc.number}')
                return redirect(f'/sales/{new_doc.id}/')
            elif action == 'cancel':
                SalesService.cancel_document(doc_id=doc.id, reason=request.POST.get('reason', ''), actor=request.user, company=request.company)
                messages.success(request, f'Document {doc.number} annulé (stock réservé libéré).')
            elif action == 'pay':
                ses = CashService.get_open_session(request.company)
                SalesService.pay_invoice(
                    doc_id=doc.id,
                    amount=request.POST.get('amount'),
                    payment_method=request.POST.get('payment_method', 'WAVE'),
                    reference=request.POST.get('reference', ''),
                    actor=request.user,
                    cash_session=ses,
                    company=request.company,
                )
                messages.success(request, 'Règlement enregistré.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect(f'/sales/{doc.id}/')

    return render(request, 'sales/detail.html', {
        'doc': doc,
        'lines': doc.lines.select_related('product'),
        'payments': doc.payments.select_related('received_by').all(),
        'derived': doc.derived_docs.all(),
        'deliveries': doc.deliveries.all(),
    })


@require_perm('deliveries.view')
def deliveries_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'deliveries.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['deliveries.manage']}, status=403)
        try:
            liv = get_object_or_404(DeliveryOrder, pk=parse_int(request.POST.get('delivery_id'), 'livraison'), company=request.company)
            new_status = request.POST.get('status', '')
            if new_status not in dict(DeliveryOrder.STATUS_CHOICES):
                raise BusinessRuleError('Statut de livraison invalide.')
            if new_status == 'FAILED' and not request.POST.get('failure_reason', '').strip():
                raise BusinessRuleError("Un motif d'échec est obligatoire.")
            liv.status = new_status
            if request.POST.get('driver_name', '').strip():
                liv.driver_name = request.POST['driver_name'].strip()
            if request.POST.get('vehicle_plate', '').strip():
                liv.vehicle_plate = request.POST['vehicle_plate'].strip()
            if new_status == 'FAILED':
                liv.failure_reason = request.POST['failure_reason'].strip()
            if new_status == 'DELIVERED':
                liv.delivered_at = timezone.now()
                liv.recipient_name = request.POST.get('recipient_name', 'Client').strip() or 'Client'
            liv.save()
            messages.success(request, f'Livraison {liv.number} → {liv.get_status_display()}')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/deliveries/')

    return render(request, 'sales/deliveries.html', {
        'deliveries': DeliveryOrder.objects.filter(company=request.company).select_related('sales_document', 'client', 'warehouse'),
    })
