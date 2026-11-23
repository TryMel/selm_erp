from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from apps.cash.models import CashSession
from apps.clients.models import Client
from apps.expenses.models import Expense
from apps.inventory.models import StockLevel
from apps.purchases.models import PurchaseOrder
from apps.sales.models import DeliveryOrder, SalesDocument, SalesLine, SalesPayment
from apps.suppliers.models import Supplier


def build_dashboard_data(company, can_view_margins: bool) -> dict:
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_ago = now - timedelta(days=30)

    sales_qs = SalesDocument.objects.filter(
        company=company, doc_type__in=['POS_SALE', 'INVOICE'], created_at__gte=month_ago
    ).exclude(status='CANCELLED')

    today_revenue = sales_qs.filter(created_at__gte=today_start).aggregate(s=Sum('total_ttc'))['s'] or Decimal('0')
    month_revenue = sales_qs.aggregate(s=Sum('total_ttc'))['s'] or Decimal('0')
    month_revenue_ht = sales_qs.aggregate(s=Sum('subtotal_ht'))['s'] or Decimal('0')
    tva_collected = sales_qs.aggregate(s=Sum('tax_total'))['s'] or Decimal('0')
    sales_count = sales_qs.count()

    # Rentabilité stricte sur CA HT (norme comptable — TVA exclue de la marge)
    lines = SalesLine.objects.select_related('product', 'product__category').filter(document__in=sales_qs)
    month_cost = Decimal('0')
    prod_map = {}
    for l in lines:
        line_cost = l.cost_price_base * l.quantity_base
        month_cost += line_cost
        entry = prod_map.setdefault(
            l.product_id,
            {'name': l.product_name, 'qty': Decimal('0'), 'revenue': Decimal('0'), 'revenue_ht': Decimal('0'), 'cost': Decimal('0')},
        )
        entry['qty'] += l.quantity_base
        entry['revenue'] += l.line_total_ttc
        entry['revenue_ht'] += l.line_total_ht
        entry['cost'] += line_cost

    top_products = []
    for item in sorted(prod_map.values(), key=lambda x: x['revenue'], reverse=True)[:10]:
        margin = item['revenue_ht'] - item['cost']
        rate = round(float(margin / item['revenue_ht']) * 100, 1) if item['revenue_ht'] > 0 else 0
        top_products.append({
            'name': item['name'],
            'qty': item['qty'],
            'revenue': item['revenue'],
            'cost': item['cost'] if can_view_margins else None,
            'margin': margin if can_view_margins else None,
            'margin_rate': rate if can_view_margins else None,
        })

    estimated_margin = (month_revenue_ht - month_cost) if can_view_margins else None
    margin_rate = round(float((month_revenue_ht - month_cost) / month_revenue_ht) * 100, 1) if (can_view_margins and month_revenue_ht > 0) else None

    # Stocks & Alertes
    levels = list(
        StockLevel.objects.select_related('product', 'warehouse')
        .filter(warehouse__store__company=company, product__is_active=True)
    )
    low_stocks = [sl for sl in levels if sl.is_low_stock]
    stock_valuation = sum(sl.valuation for sl in levels) if can_view_margins else None

    clients_due = list(Client.objects.filter(company=company, current_balance__gt=0).order_by('-current_balance'))
    total_receivables = sum((c.current_balance for c in clients_due), Decimal('0'))
    total_payables = Supplier.objects.filter(company=company).aggregate(s=Sum('current_debt'))['s'] or Decimal('0')

    open_sessions = list(
        CashSession.objects.select_related('register', 'opened_by').filter(
            company=company, status__in=['OPEN', 'PENDING_VALIDATION']
        )
    )
    pending_expenses = Expense.objects.filter(company=company, status='PENDING').count()
    active_deliveries = DeliveryOrder.objects.filter(
        company=company, status__in=['PREPARING', 'LOADED', 'IN_TRANSIT', 'FAILED']
    ).count()

    # Alertes temps réel (Module 23)
    alerts = []
    for sl in low_stocks[:6]:
        alerts.append({
            'level': 'danger' if sl.available_qty <= 0 else 'warning',
            'category': 'STOCK FAIBLE',
            'message': f'{sl.product.name} ({sl.warehouse.name}) : {sl.available_qty} dispo (seuil min {sl.product.min_stock})',
            'url': '/inventory/',
        })
    for c in clients_due:
        if c.credit_limit > 0 and c.current_balance >= c.credit_limit * Decimal('0.85'):
            alerts.append({
                'level': 'danger' if c.current_balance >= c.credit_limit else 'warning',
                'category': 'PLAFOND CRÉDIT',
                'message': f'{c.name} : encours {int(c.current_balance):,} / plafond {int(c.credit_limit):,} FCFA'.replace(',', ' '),
                'url': '/clients/',
            })
    if pending_expenses:
        alerts.append({
            'level': 'info',
            'category': 'DÉPENSES',
            'message': f'{pending_expenses} dépense(s) en attente de validation responsable',
            'url': '/expenses/',
        })

    # Répartition paiements
    payments = (
        SalesPayment.objects.filter(document__in=sales_qs)
        .values('payment_method')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    # TVA déductible sur achats réceptionnés (Audit 2A)
    po_received_ttc = (
        PurchaseOrder.objects.filter(company=company, status__in=['PARTIALLY_RECEIVED', 'RECEIVED', 'PAID'], created_at__gte=month_ago)
        .aggregate(s=Sum('total_ttc'))['s']
        or Decimal('0')
    )
    tva_deductible = round(po_received_ttc - (po_received_ttc / Decimal('1.18')))

    return {
        'kpis': {
            'today_revenue': today_revenue,
            'month_revenue': month_revenue,
            'sales_count': sales_count,
            'estimated_margin': estimated_margin,
            'margin_rate': margin_rate,
            'stock_valuation': stock_valuation,
            'low_stock_count': len(low_stocks),
            'total_receivables': total_receivables,
            'total_payables': total_payables,
            'open_sessions_count': len(open_sessions),
            'active_deliveries_count': active_deliveries,
            'tva_collected': tva_collected,
            'tva_deductible': tva_deductible,
            'tva_net': tva_collected - tva_deductible,
        },
        'top_products': top_products,
        'alerts': alerts,
        'open_sessions': open_sessions,
        'payment_breakdown': list(payments),
    }
