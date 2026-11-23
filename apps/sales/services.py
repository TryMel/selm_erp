from decimal import Decimal
from django.db import transaction
from apps.accounts.services import verify_manager_override
from apps.accounting.services import AccountingBridge, treasury_account_for
from apps.audit.services import log_business
from apps.cash.models import CashSession
from apps.cash.services import CashService
from apps.catalog.models import Product, ProductPackaging
from apps.clients.models import Client
from apps.clients.services import CreditService
from apps.core.exceptions import BusinessRuleError, ManagerOverrideRequiredError
from apps.core.models import next_doc_number
from apps.core.money import compute_line_totals, round_fcfa, round_qty
from apps.inventory.models import StockLevel
from apps.inventory.services import StockService
from apps.pricing.services import PricingEngine
from apps.taxation.services import generate_fne_stamp, resolve_effective_tax
from .models import DeliveryOrder, SalesDocument, SalesLine, SalesPayment

ALLOWED_TRANSFORMS = {
    'QUOTE': {'ORDER'},
    'ORDER': {'DELIVERY_NOTE', 'INVOICE'},
    'DELIVERY_NOTE': {'INVOICE'},
}
VALID_PAYMENT_METHODS = {code for code, _ in SalesPayment.METHODS}


class SalesService:
    """
    Service commercial sécurisé :
    - Prix TOUJOURS recalculé côté serveur via PricingEngine (anti-falsification POS)
    - Extraction HT réelle pour clients exonérés de TVA
    - Machine d'états stricte sans double facturation ni annulation de facture verrouillée
    """

    @staticmethod
    def _prepare_lines(company, warehouse, client, raw_lines: list[dict], actor, allow_override_check=True):
        if not raw_lines:
            raise BusinessRuleError('Au moins une ligne article est requise.')

        override_reasons = []
        prepared = []
        for item in raw_lines:
            product = Product.objects.select_related('base_unit', 'tax_rule', 'category').filter(
                pk=item.get('product_id'), is_active=True
            ).first()
            if not product:
                raise BusinessRuleError('Article introuvable ou inactif.', 'INACTIVE_PRODUCT')

            qty = round_qty(item.get('quantity', 0))
            if qty <= 0:
                raise BusinessRuleError(f'Quantité invalide pour {product.name}.')

            packaging = None
            if item.get('packaging_id'):
                packaging = ProductPackaging.objects.filter(pk=item['packaging_id'], product=product).first()

            # SÉCURITÉ (Red Team C1) : Toujours résoudre le prix côté serveur via PricingEngine
            priced = PricingEngine.resolve(company, product, qty, packaging=packaging, client=client)
            unit_price = priced['resolved_price']

            # Si le client a tenté d'envoyer un prix unitaire inférieur au tarif résolu, exige approbation responsable
            if item.get('unit_price') is not None:
                req_price = round_fcfa(item['unit_price'])
                if req_price < 0:
                    raise BusinessRuleError(f'Prix unitaire négatif interdit pour {product.name}.')
                if req_price < unit_price:
                    override_reasons.append(
                        f'Prix manuel {int(req_price)} FCFA < tarif autorisé {int(unit_price)} FCFA sur {product.name}'
                    )
                    unit_price = req_price

            disc_pct = round_qty(item.get('discount_percent') or 0, 2)
            if disc_pct < 0 or disc_pct > 100:
                raise BusinessRuleError('Le taux de remise doit être compris entre 0 et 100 %.')

            if allow_override_check and float(disc_pct) > actor.max_discount_percent:
                override_reasons.append(
                    f'Remise {disc_pct}% sur {product.name} (limite rôle : {actor.max_discount_percent}%)'
                )

            tax_rate, tax_exempt, tax_label = resolve_effective_tax(product, client=client)
            base_tax_rate = product.tax_rule.rate if product.tax_rule else Decimal('18')
            totals = compute_line_totals(
                qty, unit_price, disc_pct, tax_rate, tax_exempt=tax_exempt, base_tax_rate=base_tax_rate
            )
            level = StockLevel.objects.filter(product=product, warehouse=warehouse).first()
            cost_base = level.cmup if level else product.purchase_cost

            prepared.append({
                'product': product,
                'product_name': product.name,
                'unit_label': priced['unit_label'],
                'conversion_factor': priced['conversion_factor'],
                'quantity': qty,
                'quantity_base': round_qty(qty * priced['conversion_factor']),
                'base_price': priced['base_price'],
                'unit_price': unit_price,
                'discount_percent': disc_pct,
                'tax_rate': tax_rate,
                'tax_label': tax_label,
                'line_total_ht': totals['total_ht'],
                'line_tax': totals['tax_amount'],
                'line_total_ttc': totals['total_ttc'],
                'discount_amount': totals['discount_amount'],
                'cost_price_base': cost_base,
                'pricing_rule_applied': priced['applied_rule'],
            })

        return prepared, override_reasons

    @staticmethod
    @transaction.atomic
    def pos_checkout(
        *,
        company,
        session_id: int,
        client_id,
        raw_lines: list[dict],
        payments: list[dict],
        actor,
        override_username: str = '',
        override_secret: str = '',
        notes: str = '',
        ip_address: str = '',
    ) -> SalesDocument:
        session = CashSession.objects.select_for_update().select_related('register', 'register__warehouse').filter(
            pk=session_id, company=company, status='OPEN'
        ).first()
        if not session:
            raise BusinessRuleError('Aucune session de caisse ouverte. Ouvrez une caisse avant de vendre.', 'CASH_CLOSED')

        warehouse = session.register.warehouse
        client = Client.objects.filter(pk=client_id, company=company).first() if client_id else None

        prepared, override_reasons = SalesService._prepare_lines(company, warehouse, client, raw_lines, actor)

        subtotal_ht = sum((l['line_total_ht'] for l in prepared), Decimal('0'))
        tax_total = sum((l['line_tax'] for l in prepared), Decimal('0'))
        discount_total = sum((l['discount_amount'] for l in prepared), Decimal('0'))
        total_ttc = sum((l['line_total_ttc'] for l in prepared), Decimal('0'))

        if not payments:
            raise BusinessRuleError('Au moins un moyen de paiement est requis.')
        clean_payments = []
        for p in payments:
            pm = p.get('payment_method', '')
            if pm not in VALID_PAYMENT_METHODS:
                raise BusinessRuleError(f'Moyen de paiement non reconnu : {pm}', 'INVALID_PAYMENT_METHOD')
            amt = round_fcfa(p.get('amount', 0))
            if amt <= 0:
                raise BusinessRuleError('Chaque montant de paiement doit être strictement positif.')
            clean_payments.append({
                'payment_method': pm,
                'amount': amt,
                'reference': (p.get('reference') or '').strip()[:80],
            })

        pay_sum = sum((p['amount'] for p in clean_payments), Decimal('0'))
        if pay_sum != total_ttc:
            raise BusinessRuleError(
                f'Somme des paiements ({int(pay_sum)} FCFA) différente du total vente ({int(total_ttc)} FCFA).',
                'PAYMENT_MISMATCH',
            )

        credit_amount = sum((p['amount'] for p in clean_payments if p['payment_method'] == 'CREDIT'), Decimal('0'))
        paid_amount = round_fcfa(total_ttc - credit_amount)

        if credit_amount > 0:
            if not client:
                raise BusinessRuleError('Un client identifié est obligatoire pour vendre à crédit.', 'CLIENT_REQUIRED')
            try:
                CreditService.assert_credit_allowed(client.id, credit_amount, approver=None, company=company)
            except ManagerOverrideRequiredError as exc:
                override_reasons.extend(exc.reasons)

        # Vérifie PIN responsable si demandé OU si fourni (ex: déblocage stock négatif ALLOWED_WITH_PERMISSION)
        approver = None
        if override_username and override_secret:
            approver = verify_manager_override(override_username, override_secret)
        elif override_reasons:
            raise ManagerOverrideRequiredError(override_reasons)

        number = next_doc_number(company, 'POS_SALE')
        fne_ref, fne_qr = generate_fne_stamp(company, number, total_ttc) if company.fne_enabled else ('', '')

        doc = SalesDocument.objects.create(
            company=company,
            number=number,
            doc_type='POS_SALE',
            status='PAID' if credit_amount == 0 else ('PARTIALLY_PAID' if paid_amount > 0 else 'INVOICED'),
            client=client,
            warehouse=warehouse,
            cash_session=session,
            seller=actor,
            approved_by=approver,
            subtotal_ht=subtotal_ht,
            discount_total=discount_total,
            tax_total=tax_total,
            total_ttc=total_ttc,
            paid_amount=paid_amount,
            balance_due=credit_amount,
            fne_reference=fne_ref,
            fne_qr_payload=fne_qr,
            notes=notes,
            is_locked=True,
        )

        for l in prepared:
            SalesLine.objects.create(
                document=doc,
                product=l['product'],
                product_name=l['product_name'],
                unit_label=l['unit_label'],
                conversion_factor=l['conversion_factor'],
                quantity=l['quantity'],
                quantity_base=l['quantity_base'],
                base_price=l['base_price'],
                unit_price=l['unit_price'],
                discount_percent=l['discount_percent'],
                tax_rate=l['tax_rate'],
                tax_label=l['tax_label'],
                line_total_ht=l['line_total_ht'],
                line_tax=l['line_tax'],
                line_total_ttc=l['line_total_ttc'],
                cost_price_base=l['cost_price_base'],
                pricing_rule_applied=l['pricing_rule_applied'],
            )
            StockService.deduct(
                product=l['product'],
                warehouse=warehouse,
                qty_base=l['quantity_base'],
                movement_type='OUT_POS',
                unit_label=l['unit_label'],
                conversion_factor=l['conversion_factor'],
                document_type='POS_SALE',
                document_number=number,
                reason=f'Vente comptoir {number}',
                actor=actor,
                approver=approver,
            )

        for p in clean_payments:
            SalesPayment.objects.create(
                document=doc,
                client=client,
                cash_session=None if p['payment_method'] == 'CREDIT' else session,
                payment_method=p['payment_method'],
                amount=p['amount'],
                reference=p['reference'],
                received_by=actor,
            )
            if p['payment_method'] != 'CREDIT':
                CashService.record_sale_inflow(
                    session=session,
                    payment_method=p['payment_method'],
                    amount=p['amount'],
                    reason=f'Vente POS {number}',
                    reference=p['reference'] or number,
                    actor=actor,
                )

        if credit_amount > 0 and client:
            CreditService.charge_credit(
                client_id=client.id,
                amount=credit_amount,
                reference=number,
                note=f'Vente comptoir {number} (part crédit)',
                actor=actor,
                approver=approver,
                company=company,
            )

        acc_lines = [
            {'account_code': treasury_account_for(p['payment_method']), 'label': f"Règlement {p['payment_method']} {number}", 'debit': p['amount']}
            for p in clean_payments
        ]
        acc_lines.append({'account_code': '701000', 'label': f'Vente marchandises HT {number}', 'credit': subtotal_ht})
        if tax_total > 0:
            acc_lines.append({'account_code': '443100', 'label': f'TVA collectée {number}', 'credit': tax_total})
        AccountingBridge.post_entry(
            company=company,
            journal_code='VTE',
            description=f'Vente comptoir {number}',
            source_doc_type='POS_SALE',
            source_doc_number=number,
            actor_name=actor.display_name,
            lines=acc_lines,
        )

        log_business(
            company=company,
            action='POS_OVERRIDE_SALE' if approver else 'POS_SALE_COMPLETED',
            module='SALES',
            entity_type='SalesDocument',
            entity_id=doc.id,
            document_number=number,
            actor=actor,
            approver=approver,
            summary=f'Vente POS {number} ({int(total_ttc)} FCFA)' + (f' — Approuvé par {approver.display_name}' if approver else ''),
            details={'override_reasons': override_reasons, 'credit': int(credit_amount)},
            ip_address=ip_address,
        )
        return doc

    @staticmethod
    @transaction.atomic
    def create_document(*, company, doc_type: str, client: Client, warehouse, raw_lines: list[dict], actor, notes='') -> SalesDocument:
        if doc_type not in ('QUOTE', 'ORDER', 'INVOICE'):
            raise BusinessRuleError('Type de document invalide.')
        prepared, _ = SalesService._prepare_lines(company, warehouse, client, raw_lines, actor, allow_override_check=False)

        subtotal_ht = sum((l['line_total_ht'] for l in prepared), Decimal('0'))
        tax_total = sum((l['line_tax'] for l in prepared), Decimal('0'))
        discount_total = sum((l['discount_amount'] for l in prepared), Decimal('0'))
        total_ttc = sum((l['line_total_ttc'] for l in prepared), Decimal('0'))

        number = next_doc_number(company, doc_type)
        fne_ref, fne_qr = generate_fne_stamp(company, number, total_ttc) if (doc_type == 'INVOICE' and company.fne_enabled) else ('', '')

        doc = SalesDocument.objects.create(
            company=company,
            number=number,
            doc_type=doc_type,
            status='DRAFT' if doc_type == 'QUOTE' else ('CONFIRMED' if doc_type == 'ORDER' else 'INVOICED'),
            client=client,
            warehouse=warehouse,
            seller=actor,
            subtotal_ht=subtotal_ht,
            discount_total=discount_total,
            tax_total=tax_total,
            total_ttc=total_ttc,
            balance_due=total_ttc,
            fne_reference=fne_ref,
            fne_qr_payload=fne_qr,
            notes=notes,
            is_locked=(doc_type == 'INVOICE'),
        )

        for l in prepared:
            SalesLine.objects.create(
                document=doc,
                product=l['product'],
                product_name=l['product_name'],
                unit_label=l['unit_label'],
                conversion_factor=l['conversion_factor'],
                quantity=l['quantity'],
                quantity_base=l['quantity_base'],
                base_price=l['base_price'],
                unit_price=l['unit_price'],
                discount_percent=l['discount_percent'],
                tax_rate=l['tax_rate'],
                tax_label=l['tax_label'],
                line_total_ht=l['line_total_ht'],
                line_tax=l['line_tax'],
                line_total_ttc=l['line_total_ttc'],
                cost_price_base=l['cost_price_base'],
                pricing_rule_applied=l['pricing_rule_applied'],
            )
            if doc_type == 'ORDER':
                StockService.reserve(product=l['product'], warehouse=warehouse, qty_base=l['quantity_base'], actor=actor)
            elif doc_type == 'INVOICE':
                StockService.deduct(
                    product=l['product'],
                    warehouse=warehouse,
                    qty_base=l['quantity_base'],
                    movement_type='OUT_SALE',
                    unit_label=l['unit_label'],
                    conversion_factor=l['conversion_factor'],
                    document_type='INVOICE',
                    document_number=number,
                    reason=f'Facture directe {number}',
                    actor=actor,
                )

        if doc_type == 'INVOICE':
            CreditService.charge_credit(
                client_id=client.id,
                amount=total_ttc,
                reference=number,
                note=f'Facture {number}',
                actor=actor,
                approver=actor if actor.can_approve_override else None,
                company=company,
            )
            acc_lines = [
                {'account_code': '411000', 'label': f'Créance {client.name} ({number})', 'debit': total_ttc},
                {'account_code': '701000', 'label': f'Vente HT {number}', 'credit': subtotal_ht},
            ]
            if tax_total > 0:
                acc_lines.append({'account_code': '443100', 'label': f'TVA collectée {number}', 'credit': tax_total})
            AccountingBridge.post_entry(
                company=company,
                journal_code='VTE',
                description=f'Facture client {number}',
                source_doc_type='INVOICE',
                source_doc_number=number,
                actor_name=actor.display_name,
                lines=acc_lines,
            )

        log_business(
            company=company,
            action=f'SALES_{doc_type}_CREATED',
            module='SALES',
            entity_type='SalesDocument',
            entity_id=doc.id,
            document_number=number,
            actor=actor,
            summary=f'Création {doc.get_doc_type_display()} {number} pour {client.name} ({int(total_ttc)} FCFA)',
        )
        return doc

    @staticmethod
    @transaction.atomic
    def transform_document(*, source_id: int, target_type: str, actor, delivery_address: str = '', company=None) -> SalesDocument:
        qs = SalesDocument.objects.select_for_update().select_related('client', 'warehouse', 'company')
        if company is not None:
            qs = qs.filter(company=company)
        source = qs.filter(pk=source_id).first()
        if not source:
            raise BusinessRuleError('Document source introuvable.')
        if source.status == 'CANCELLED':
            raise BusinessRuleError('Impossible de transformer un document annulé.')
        if target_type not in ALLOWED_TRANSFORMS.get(source.doc_type, set()):
            raise BusinessRuleError(
                f'Transition interdite : impossible de transformer {source.doc_type} en {target_type}.',
                'INVALID_TRANSITION',
            )
        if source.derived_docs.filter(doc_type=target_type).exclude(status='CANCELLED').exists():
            raise BusinessRuleError(f'{source.number} a déjà été transformé en {target_type}.', 'DUPLICATE_TRANSFORM')
        if source.doc_type == 'ORDER' and target_type == 'INVOICE':
            if source.derived_docs.filter(doc_type='DELIVERY_NOTE').exclude(status='CANCELLED').exists():
                raise BusinessRuleError(
                    f'Un Bon de Livraison existe déjà pour {source.number} : facturez depuis le BL pour éviter une double facturation.',
                    'USE_DELIVERY_NOTE',
                )

        lines = list(source.lines.select_related('product'))
        number = next_doc_number(source.company, target_type)
        fne_ref, fne_qr = (
            generate_fne_stamp(source.company, number, source.total_ttc)
            if (target_type == 'INVOICE' and source.company.fne_enabled)
            else ('', '')
        )

        if target_type == 'ORDER':
            for l in lines:
                StockService.reserve(product=l.product, warehouse=source.warehouse, qty_base=l.quantity_base, actor=actor)
            source.status = 'CONFIRMED'
            source.is_locked = True
            source.save(update_fields=['status', 'is_locked', 'updated_at'])

        elif target_type == 'DELIVERY_NOTE':
            for l in lines:
                StockService.deduct(
                    product=l.product,
                    warehouse=source.warehouse,
                    qty_base=l.quantity_base,
                    movement_type='OUT_SALE',
                    unit_label=l.unit_label,
                    conversion_factor=l.conversion_factor,
                    document_type='DELIVERY_NOTE',
                    document_number=number,
                    reason=f'Bon de livraison {number}',
                    actor=actor,
                    release_reserved=(source.doc_type == 'ORDER'),
                )
            source.status = 'DELIVERED'
            source.is_locked = True
            source.save(update_fields=['status', 'is_locked', 'updated_at'])

        elif target_type == 'INVOICE':
            if source.doc_type == 'ORDER' and source.status != 'DELIVERED':
                for l in lines:
                    StockService.deduct(
                        product=l.product,
                        warehouse=source.warehouse,
                        qty_base=l.quantity_base,
                        movement_type='OUT_SALE',
                        unit_label=l.unit_label,
                        conversion_factor=l.conversion_factor,
                        document_type='INVOICE',
                        document_number=number,
                        reason=f'Facture {number}',
                        actor=actor,
                        release_reserved=True,
                    )
            if source.client_id:
                CreditService.charge_credit(
                    client_id=source.client_id,
                    amount=source.total_ttc,
                    reference=number,
                    note=f'Facture {number} (depuis {source.number})',
                    actor=actor,
                    approver=actor if actor.can_approve_override else None,
                    company=source.company,
                )
            source.status = 'INVOICED'
            source.is_locked = True
            source.save(update_fields=['status', 'is_locked', 'updated_at'])
            acc_lines = [
                {'account_code': '411000', 'label': f'Créance facture {number}', 'debit': source.total_ttc},
                {'account_code': '701000', 'label': f'Vente HT {number}', 'credit': source.subtotal_ht},
            ]
            if source.tax_total > 0:
                acc_lines.append({'account_code': '443100', 'label': f'TVA collectée {number}', 'credit': source.tax_total})
            AccountingBridge.post_entry(
                company=source.company,
                journal_code='VTE',
                description=f'Facture {number} (depuis {source.number})',
                source_doc_type='INVOICE',
                source_doc_number=number,
                actor_name=actor.display_name,
                lines=acc_lines,
            )

        target = SalesDocument.objects.create(
            company=source.company,
            number=number,
            doc_type=target_type,
            status='CONFIRMED' if target_type == 'ORDER' else ('DELIVERED' if target_type == 'DELIVERY_NOTE' else 'INVOICED'),
            source_doc=source,
            client=source.client,
            warehouse=source.warehouse,
            seller=actor,
            subtotal_ht=source.subtotal_ht,
            discount_total=source.discount_total,
            tax_total=source.tax_total,
            total_ttc=source.total_ttc,
            balance_due=source.total_ttc,
            fne_reference=fne_ref,
            fne_qr_payload=fne_qr,
            notes=source.notes,
            is_locked=(target_type in ('DELIVERY_NOTE', 'INVOICE')),
        )
        SalesLine.objects.bulk_create([
            SalesLine(
                document=target,
                product=l.product,
                product_name=l.product_name,
                unit_label=l.unit_label,
                conversion_factor=l.conversion_factor,
                quantity=l.quantity,
                quantity_base=l.quantity_base,
                base_price=l.base_price,
                unit_price=l.unit_price,
                discount_percent=l.discount_percent,
                tax_rate=l.tax_rate,
                tax_label=l.tax_label,
                line_total_ht=l.line_total_ht,
                line_tax=l.line_tax,
                line_total_ttc=l.line_total_ttc,
                cost_price_base=l.cost_price_base,
                pricing_rule_applied=l.pricing_rule_applied,
            )
            for l in lines
        ])

        if target_type == 'DELIVERY_NOTE':
            DeliveryOrder.objects.create(
                company=source.company,
                number=next_doc_number(source.company, 'DELIVERY'),
                sales_document=target,
                client=source.client,
                warehouse=source.warehouse,
                delivery_address=delivery_address or (source.client.address if source.client else 'À préciser'),
            )

        log_business(
            company=source.company,
            action='SALES_DOC_TRANSFORMED',
            module='SALES',
            entity_type='SalesDocument',
            entity_id=target.id,
            document_number=target.number,
            actor=actor,
            summary=f'Transformation {source.number} → {target.number} ({target_type})',
        )
        return target

    @staticmethod
    @transaction.atomic
    def cancel_document(*, doc_id: int, reason: str, actor, company=None) -> SalesDocument:
        qs = SalesDocument.objects.select_for_update()
        if company is not None:
            qs = qs.filter(company=company)
        doc = qs.filter(pk=doc_id).first()
        if not doc:
            raise BusinessRuleError('Document introuvable.')
        if not (reason or '').strip():
            raise BusinessRuleError("Un motif d'annulation est obligatoire.")
        if doc.status == 'CANCELLED':
            raise BusinessRuleError('Document déjà annulé.')
        # Conformité fiscale FNE / stock : impossible d'annuler une facture, un ticket ou un BL livré
        if doc.doc_type in ('INVOICE', 'POS_SALE', 'DELIVERY_NOTE'):
            raise BusinessRuleError(
                'Document fiscal ou logistique verrouillé : utilisez le module Retours & Avoirs pour émettre un avoir.',
                'LOCKED_DOCUMENT',
            )

        if doc.doc_type == 'ORDER' and doc.status == 'CONFIRMED':
            for l in doc.lines.select_related('product'):
                StockService.release_reservation(product=l.product, warehouse=doc.warehouse, qty_base=l.quantity_base)

        doc.status = 'CANCELLED'
        doc.cancel_reason = reason.strip()
        doc.is_locked = True
        doc.save(update_fields=['status', 'cancel_reason', 'is_locked', 'updated_at'])
        log_business(
            company=doc.company,
            action='SALES_DOC_CANCELLED',
            module='SALES',
            entity_type='SalesDocument',
            entity_id=doc.id,
            document_number=doc.number,
            actor=actor,
            summary=f'Annulation {doc.number} — Motif : {doc.cancel_reason}',
        )
        return doc

    @staticmethod
    @transaction.atomic
    def pay_invoice(*, doc_id: int, amount, payment_method: str, reference: str, actor, cash_session=None, company=None):
        qs = SalesDocument.objects.select_for_update().select_related('client')
        if company is not None:
            qs = qs.filter(company=company)
        doc = qs.filter(pk=doc_id).first()
        if not doc:
            raise BusinessRuleError('Facture introuvable.')
        if doc.doc_type not in ('INVOICE', 'POS_SALE') or doc.status in ('CANCELLED', 'PAID'):
            raise BusinessRuleError('Ce document ne peut pas recevoir de règlement.')
        if payment_method not in VALID_PAYMENT_METHODS or payment_method == 'CREDIT':
            raise BusinessRuleError('Moyen de paiement invalide (impossible de régler une facture par crédit).')

        amt = round_fcfa(amount)
        if amt <= 0 or amt > doc.balance_due:
            raise BusinessRuleError(f'Montant invalide (solde restant : {int(doc.balance_due)} FCFA).')

        SalesPayment.objects.create(
            document=doc,
            client=doc.client,
            cash_session=cash_session,
            payment_method=payment_method,
            amount=amt,
            reference=reference or doc.number,
            received_by=actor,
        )
        doc.paid_amount = round_fcfa(doc.paid_amount + amt)
        doc.balance_due = max(Decimal('0'), round_fcfa(doc.total_ttc - doc.paid_amount))
        doc.status = 'PAID' if doc.balance_due == 0 else 'PARTIALLY_PAID'
        doc.save(update_fields=['paid_amount', 'balance_due', 'status', 'updated_at'])

        if doc.client_id:
            CreditService.settle_credit(
                client_id=doc.client_id,
                amount=amt,
                payment_method=payment_method,
                reference=reference or doc.number,
                note=f'Règlement {doc.number}',
                actor=actor,
                cash_session=cash_session,
                company=doc.company,
            )
        return doc
