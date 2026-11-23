import csv
import io
import os
from decimal import Decimal
import openpyxl
from django.conf import settings
from django.db import transaction
from apps.audit.services import log_technical
from apps.catalog.models import Category, Product, Unit
from apps.clients.models import Client
from apps.core.exceptions import BusinessRuleError
from apps.core.money import round_fcfa, round_qty
from apps.inventory.services import StockService
from apps.organization.models import Warehouse
from apps.suppliers.models import Supplier
from apps.taxation.models import TaxRule
from .models import ImportJob

MAX_IMPORT_ROWS = 5000


def parse_tabular_file(uploaded_file) -> list[dict]:
    if uploaded_file.size > settings.MAX_ATTACHMENT_SIZE_BYTES:
        raise BusinessRuleError('Fichier trop volumineux (max 5 Mo).', 'FILE_TOO_LARGE')
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext == '.csv':
        raw = uploaded_file.read()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252', errors='replace')
        delimiter = ';' if text.count(';') >= text.count(',') else ','
        rows = [dict(row) for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]
        if len(rows) > MAX_IMPORT_ROWS:
            raise BusinessRuleError(f'Limite dépassée : maximum {MAX_IMPORT_ROWS} lignes par import.')
        return rows
    if ext == '.xlsx':
        wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header_row = next(it, None)
        if not header_row:
            return []
        headers = [str(h or '').strip() for h in header_row]
        result = []
        for r in it:
            if len(result) >= MAX_IMPORT_ROWS:
                raise BusinessRuleError(f'Limite dépassée : maximum {MAX_IMPORT_ROWS} lignes par import.')
            if any(v is not None for v in r):
                result.append(dict(zip(headers, r)))
        return result
    raise BusinessRuleError('Format non supporté : utilisez un fichier .csv ou .xlsx.')


def run_import(*, company, target: str, uploaded_file, commit: bool, actor) -> ImportJob:
    rows = parse_tabular_file(uploaded_file)
    errors = []
    valid_payloads = []

    default_cat = Category.objects.first()
    default_unit = Unit.objects.filter(code='U').first() or Unit.objects.first()
    default_tax = TaxRule.objects.filter(is_default=True).first() or TaxRule.objects.first()
    default_wh = Warehouse.objects.filter(store__company=company, is_default=True).first() or Warehouse.objects.filter(store__company=company).first()

    for idx, row in enumerate(rows, start=2):
        try:
            if target == 'PRODUCTS':
                sku = str(row.get('sku') or '').strip().upper()
                name = str(row.get('name') or '').strip()
                price = round_fcfa(row.get('standard_price') or 0)
                cost = round_fcfa(row.get('purchase_cost') or 0)
                if not sku or not name:
                    raise ValueError('Champs sku et name obligatoires.')
                if price < 0 or cost < 0:
                    raise ValueError('Prix négatif interdit.')
                valid_payloads.append({'sku': sku, 'name': name, 'price': price, 'cost': cost})

            elif target == 'CLIENTS':
                code = str(row.get('code') or '').strip().upper()
                name = str(row.get('name') or '').strip()
                limit = round_fcfa(row.get('credit_limit') or 0)
                if not code or not name:
                    raise ValueError('Champs code et name obligatoires.')
                if limit < 0:
                    raise ValueError('Plafond crédit négatif interdit.')
                # Isolation multi-sociétés : refuse d'écraser un code appartenant à une autre société
                if Client.objects.filter(code=code).exclude(company=company).exists():
                    raise ValueError(f'Code client "{code}" déjà attribué à une autre société.')
                valid_payloads.append({
                    'code': code,
                    'name': name,
                    'phone': str(row.get('phone') or '').strip(),
                    'segment': str(row.get('segment') or 'PARTICULIER').strip().upper(),
                    'credit_limit': limit,
                })

            elif target == 'SUPPLIERS':
                code = str(row.get('code') or '').strip().upper()
                name = str(row.get('name') or '').strip()
                if not code or not name:
                    raise ValueError('Champs code et name obligatoires.')
                if Supplier.objects.filter(code=code).exclude(company=company).exists():
                    raise ValueError(f'Code fournisseur "{code}" déjà attribué à une autre société.')
                valid_payloads.append({'code': code, 'name': name, 'phone': str(row.get('phone') or '').strip()})

            elif target == 'INITIAL_STOCK':
                sku = str(row.get('sku') or '').strip().upper()
                qty = round_qty(row.get('quantity') or 0)
                wh_code = str(row.get('warehouse_code') or '').strip().upper()
                product = Product.objects.filter(sku=sku).first()
                wh = Warehouse.objects.filter(code=wh_code, store__company=company).first() if wh_code else default_wh
                if not product:
                    raise ValueError(f'Article SKU "{sku}" introuvable.')
                if not wh:
                    raise ValueError(f'Dépôt "{wh_code}" introuvable dans cette société.')
                if qty <= 0:
                    raise ValueError('La quantité initiale doit être > 0.')
                unit_cost = Decimal(str(row.get('unit_cost') or product.purchase_cost))
                if unit_cost < 0:
                    raise ValueError('Coût unitaire négatif interdit.')
                valid_payloads.append({'product': product, 'warehouse': wh, 'qty': qty, 'cost': unit_cost})
        except Exception as exc:
            errors.append({'row': idx, 'error': str(exc)})

    status = 'SIMULATED'
    if commit and not errors and valid_payloads:
        with transaction.atomic():
            if target == 'PRODUCTS':
                for p in valid_payloads:
                    Product.objects.update_or_create(
                        sku=p['sku'],
                        defaults={
                            'name': p['name'],
                            'standard_price': p['price'],
                            'purchase_cost': p['cost'],
                            'category': default_cat,
                            'base_unit': default_unit,
                            'tax_rule': default_tax,
                        },
                    )
            elif target == 'CLIENTS':
                for c in valid_payloads:
                    Client.objects.update_or_create(
                        company=company,
                        code=c['code'],
                        defaults={
                            'name': c['name'],
                            'phone': c['phone'],
                            'segment': c['segment'] if c['segment'] in ('PARTICULIER', 'PROFESSIONNEL', 'ENTREPRISE', 'GROSSISTE') else 'PARTICULIER',
                            'credit_limit': c['credit_limit'],
                        },
                    )
            elif target == 'SUPPLIERS':
                for s in valid_payloads:
                    Supplier.objects.update_or_create(
                        company=company, code=s['code'], defaults={'name': s['name'], 'phone': s['phone']}
                    )
            elif target == 'INITIAL_STOCK':
                for st in valid_payloads:
                    StockService.receive(
                        product=st['product'],
                        warehouse=st['warehouse'],
                        qty_base=st['qty'],
                        unit_cost_base=st['cost'],
                        movement_type='IMPORT_INIT',
                        unit_label=st['product'].base_unit.name,
                        conversion_factor=1,
                        document_type='IMPORT',
                        document_number=uploaded_file.name[:40],
                        reason='Reprise stock initial Excel/CSV',
                        actor=actor,
                        recalculate_cmup=True,
                    )
        status = 'COMMITTED'

    job = ImportJob.objects.create(
        company=company,
        target=target,
        filename=os.path.basename(uploaded_file.name),
        status=status,
        total_rows=len(rows),
        valid_rows=len(valid_payloads),
        error_rows=len(errors),
        errors=errors[:100],
        actor=actor,
    )
    log_technical(
        company=company,
        category='IMPORT_EXPORT',
        level='WARNING' if errors else 'INFO',
        message=f'Reprise {target} ({status}) : {len(valid_payloads)} valides / {len(errors)} erreurs ({job.filename})',
        actor_name=actor.display_name,
    )
    return job
