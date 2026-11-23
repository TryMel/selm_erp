from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from apps.accounting.views import accounting_view
from apps.accounts.views import login_view, logout_view, users_list_view
from apps.audit.views import audit_view
from apps.backups.views import backups_view, download_backup_view
from apps.cash.views import cash_view
from apps.catalog.views import catalog_view
from apps.clients.views import clients_view
from apps.data_import.views import data_import_view
from apps.documents.views import documents_view, download_document_view
from apps.expenses.views import expenses_view
from apps.inventory.views import inventory_view
from apps.organization.views import organization_view, switch_company_view
from apps.pricing.views import pricing_view
from apps.purchases.views import purchases_view
from apps.reporting.views import dashboard_view, reporting_view
from apps.returns.views import returns_view
from apps.sales.views import (
    deliveries_view,
    pos_checkout_api,
    pos_resolve_price_api,
    pos_view,
    sales_detail_view,
    sales_list_view,
)
from apps.stock_counts.views import stock_count_detail_view, stock_counts_view
from apps.suppliers.views import suppliers_view


def health_view(_request):
    return JsonResponse({'ok': True, 'service': 'QuincaPro ERP (Django)', 'version': '2.0'})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', health_view, name='health'),
    # Auth
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    # Pilotage
    path('', dashboard_view, name='dashboard'),
    path('reporting/', reporting_view, name='reporting'),
    # Ventes, POS, Caisses & Livraisons
    path('pos/', pos_view, name='pos'),
    path('api/pos/resolve-price/', pos_resolve_price_api, name='pos_resolve_price'),
    path('api/pos/checkout/', pos_checkout_api, name='pos_checkout'),
    path('cash/', cash_view, name='cash'),
    path('sales/', sales_list_view, name='sales_list'),
    path('sales/<int:pk>/', sales_detail_view, name='sales_detail'),
    path('deliveries/', deliveries_view, name='deliveries'),
    path('returns/', returns_view, name='returns'),
    # Catalogue & Tarification
    path('catalog/', catalog_view, name='catalog'),
    path('pricing/', pricing_view, name='pricing'),
    # Stocks, Inventaires & Achats
    path('inventory/', inventory_view, name='inventory'),
    path('stock-counts/', stock_counts_view, name='stock_counts'),
    path('stock-counts/<int:pk>/', stock_count_detail_view, name='stock_count_detail'),
    path('purchases/', purchases_view, name='purchases'),
    # Tiers & Finances
    path('clients/', clients_view, name='clients'),
    path('suppliers/', suppliers_view, name='suppliers'),
    path('expenses/', expenses_view, name='expenses'),
    path('accounting/', accounting_view, name='accounting'),
    # Gouvernance, Sites, Pièces jointes, Reprise & Sauvegardes
    path('organization/', organization_view, name='organization'),
    path('organization/switch-company/', switch_company_view, name='switch_company'),
    path('documents/', documents_view, name='documents'),
    path('documents/<int:pk>/download/', download_document_view, name='document_download'),
    path('data-import/', data_import_view, name='data_import'),
    path('backups/', backups_view, name='backups'),
    path('backups/<int:pk>/download/', download_backup_view, name='backup_download'),
    path('users/', users_list_view, name='users'),
    path('audit/', audit_view, name='audit'),
]
