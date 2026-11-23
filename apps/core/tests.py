import hashlib
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import Client as HttpClient, TestCase, override_settings
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry
from apps.accounts.models import Role, User
from apps.accounts.services import authenticate_with_lockout, set_user_pin
from apps.backups.models import BackupRecord
from apps.backups.services import _sha256_file, create_backup, restore_backup
from apps.cash.models import CashRegister, CashSession
from apps.cash.services import CashService
from apps.catalog.models import Category, Product, ProductPackaging, Unit, UnitFamily
from apps.clients.models import Client, CreditLedgerEntry
from apps.core.exceptions import BusinessRuleError, ManagerOverrideRequiredError
from apps.core.permissions import ALL_PERMISSIONS
from apps.data_import.services import run_import
from apps.documents.models import DocumentAttachment, store_attachment
from apps.inventory.models import StockLevel, StockMovement
from apps.inventory.services import StockService
from apps.organization.models import Company, Store, Warehouse
from apps.pricing.models import PricingRule
from apps.pricing.services import PricingEngine
from apps.sales.models import SalesDocument
from apps.sales.services import SalesService
from apps.taxation.models import TaxRule


class ERPBaseTestCase(TestCase):
    """Socle commun réaliste (FCFA, SYSCOHADA, rôles, catalogue, stocks, clients, caisse)."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            code='QP-TEST',
            legal_name='QuincaPro Abidjan Test SARL',
            ncc='CI-ABJ-2026-B-04812',
            negative_stock_policy='FORBIDDEN',
            valuation_method='CMUP',
            cash_variance_threshold=Decimal('2000'),
            fne_enabled=True,
        )
        cls.store = Store.objects.create(
            company=cls.company, code='MAG-T1', name='Magasin Zone 4', is_default=True
        )
        cls.warehouse = Warehouse.objects.create(
            store=cls.store, code='DEP-T1', name='Dépôt Principal', is_default=True
        )

        # Fiscalité CI
        cls.tva18 = TaxRule.objects.create(
            code='TVA18', name='TVA Normale CI 18%', kind='VAT_STANDARD', rate=Decimal('18'), is_default=True
        )
        cls.exon = TaxRule.objects.create(
            code='EXON', name='Exonéré de TVA', kind='EXEMPT', rate=Decimal('0')
        )

        # Plan comptable SYSCOHADA
        for code, name, acc_cls, acc_type in [
            ('401000', 'Fournisseurs', 4, 'LIABILITY'),
            ('411000', 'Clients', 4, 'ASSET'),
            ('443100', 'TVA facturée', 4, 'LIABILITY'),
            ('445200', 'TVA récupérable', 4, 'ASSET'),
            ('521000', 'Banques', 5, 'ASSET'),
            ('522000', 'Mobile Money (Wave / OM)', 5, 'ASSET'),
            ('571000', 'Caisse Siège', 5, 'ASSET'),
            ('601000', 'Achats marchandises', 6, 'EXPENSE'),
            ('622000', 'Charges opérationnelles', 6, 'EXPENSE'),
            ('658000', 'Charges diverses — Déficits de caisse', 6, 'EXPENSE'),
            ('701000', 'Ventes de marchandises', 7, 'REVENUE'),
            ('758000', 'Produits divers — Excédents de caisse', 7, 'REVENUE'),
        ]:
            Account.objects.create(code=code, name=name, account_class=acc_cls, account_type=acc_type)

        # Rôles & Utilisateurs
        cls.role_admin = Role.objects.create(
            code='ADMIN', name='Administrateur', max_discount_percent=100,
            can_approve_override=True, permissions=ALL_PERMISSIONS, is_system=True,
        )
        cls.role_manager = Role.objects.create(
            code='MANAGER', name='Responsable Magasin', max_discount_percent=25,
            can_approve_override=True,
            permissions=[p for p in ALL_PERMISSIONS if p != 'backups.manage'],
            is_system=True,
        )
        cls.role_cashier = Role.objects.create(
            code='CASHIER', name='Caissier Comptoir', max_discount_percent=5,
            can_approve_override=False,
            permissions=[
                'dashboard.view', 'catalog.view', 'pricing.view', 'clients.view',
                'pos.sell', 'sales.view', 'cash.view', 'cash.operate',
            ],
            is_system=True,
        )

        cls.admin = User.objects.create_user(
            username='admin', email='admin@quincapro.ci', password='Admin123!',
            full_name='Directeur Admin', role=cls.role_admin,
            default_company=cls.company, default_warehouse=cls.warehouse,
            is_superuser=True, is_staff=True,
        )
        set_user_pin(cls.admin, '1234')

        cls.manager = User.objects.create_user(
            username='responsable', email='resp@quincapro.ci', password='Manager123!',
            full_name='Traoré Aminata (Resp)', role=cls.role_manager,
            default_company=cls.company, default_warehouse=cls.warehouse,
        )
        set_user_pin(cls.manager, '1234')

        cls.cashier = User.objects.create_user(
            username='caissier', email='caissier@quincapro.ci', password='Cashier123!',
            full_name='Koné Moussa (Caissier)', role=cls.role_cashier,
            default_company=cls.company, default_warehouse=cls.warehouse,
        )

        # Unités & Familles
        cls.fam_count = UnitFamily.objects.create(code='COUNT', name='Pièces')
        cls.fam_mass = UnitFamily.objects.create(code='MASS', name='Masse')
        cls.fam_len = UnitFamily.objects.create(code='LENGTH', name='Longueur')
        cls.fam_vol = UnitFamily.objects.create(code='VOLUME', name='Volume')

        cls.u_unit = Unit.objects.create(family=cls.fam_count, code='U', name='Unité', symbol='u')
        cls.u_box = Unit.objects.create(family=cls.fam_count, code='BOITE', name='Boîte', symbol='bte')
        cls.u_kg = Unit.objects.create(family=cls.fam_mass, code='KG', name='Kilogramme', symbol='kg', allow_fractional=True, precision=2)
        cls.u_m = Unit.objects.create(family=cls.fam_len, code='M', name='Mètre', symbol='m', allow_fractional=True, precision=2)
        cls.u_pot = Unit.objects.create(family=cls.fam_vol, code='POT', name='Pot 5L', symbol='pot')

        # Catégories & Articles
        cls.cat_out = Category.objects.create(code='OUTILLAGE', name='Outillage')
        cls.cat_pnt = Category.objects.create(code='PEINTURE', name='Peintures')
        cls.cat_mac = Category.objects.create(code='MACONNERIE', name='Maçonnerie')

        cls.drill = Product.objects.create(
            sku='OUT-BOSCH-550', barcode='3165140842013', name='Perceuse Bosch GSB 550W',
            category=cls.cat_out, base_unit=cls.u_unit, tax_rule=cls.tva18,
            purchase_cost=Decimal('52345'), standard_price=Decimal('75000'), min_stock=4,
        )
        cls.paint = Product.objects.create(
            sku='PNT-BLC-5L', barcode='6180002005001', name='Peinture Blanc Mat 5L',
            category=cls.cat_pnt, base_unit=cls.u_pot, tax_rule=cls.tva18,
            purchase_cost=Decimal('7234'), standard_price=Decimal('10000'), min_stock=10,
        )
        cls.cement = Product.objects.create(
            sku='CIM-CPJ-50KG', barcode='6180003050001', name='Ciment CPJ 42.5',
            category=cls.cat_mac, base_unit=cls.u_kg, tax_rule=cls.tva18,
            purchase_cost=Decimal('4450'), standard_price=Decimal('5200'), min_stock=20,
        )

        # Stocks initiaux
        StockLevel.objects.create(product=cls.drill, warehouse=cls.warehouse, physical_qty=Decimal('10'), cmup=Decimal('52345'))
        StockLevel.objects.create(product=cls.paint, warehouse=cls.warehouse, physical_qty=Decimal('50'), cmup=Decimal('7234'))
        StockLevel.objects.create(product=cls.cement, warehouse=cls.warehouse, physical_qty=Decimal('30'), cmup=Decimal('4450'))

        # Clients
        cls.cli_btp = Client.objects.create(
            company=cls.company, code='CLI-001', name='Entreprise KOFFI BTP',
            segment='ENTREPRISE', credit_limit=Decimal('500000'), current_balance=Decimal('100000'),
        )
        cls.cli_pro = Client.objects.create(
            company=cls.company, code='CLI-002', name='Artisan Ouattara Pro',
            segment='PROFESSIONNEL', credit_limit=Decimal('250000'), current_balance=Decimal('0'),
        )
        cls.cli_pro_vip = Client.objects.create(
            company=cls.company, code='CLI-004', name='Artisan VIP Contrat',
            segment='PROFESSIONNEL', credit_limit=Decimal('250000'), current_balance=Decimal('0'),
        )
        cls.cli_ong = Client.objects.create(
            company=cls.company, code='CLI-003', name='ONG Bâtir Ensemble CI',
            segment='ENTREPRISE', tax_exempt=True, exemption_certificate='DGI-EXON-2026-0842',
            credit_limit=Decimal('1000000'), current_balance=Decimal('0'),
        )

        # Caisse
        cls.register = CashRegister.objects.create(
            store=cls.store, warehouse=cls.warehouse, code='CAISSE-01', name='Caisse Comptoir #1'
        )


# =============================================================================
# 1. AUTHENTIFICATION, VERROUILLAGE, PERMISSIONS & RÉVOCATION DE SESSION
# =============================================================================
class AuthPermissionsSecurityTests(ERPBaseTestCase):
    def test_login_lockout_after_five_failed_attempts(self):
        http = HttpClient()
        for attempt in range(5):
            resp = http.post('/login/', {'username': 'caissier', 'password': f'WrongPwd{attempt}!'})
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn('_auth_user_id', http.session)

        self.cashier.refresh_from_db()
        self.assertIsNotNone(self.cashier.locked_until)
        self.assertGreater(self.cashier.locked_until, timezone.now())
        self.assertEqual(self.cashier.failed_attempts, 0)

        # 6e tentative même avec le BON mot de passe → refusée (compte verrouillé)
        resp = http.post('/login/', {'username': 'caissier', 'password': 'Cashier123!'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', http.session)
        self.assertContains(resp, 'verrouillé')

        # Vérification explicite du code d'erreur métier ACCOUNT_LOCKED
        with self.assertRaises(BusinessRuleError) as ctx:
            authenticate_with_lockout(resp.wsgi_request, 'caissier', 'Cashier123!')
        self.assertEqual(ctx.exception.code, 'ACCOUNT_LOCKED')

    def test_cashier_cannot_see_cost_price_or_access_backups_and_accounting(self):
        cashier_http = HttpClient()
        self.assertTrue(cashier_http.login(username='caissier', password='Cashier123!'))

        # /catalog/ accessible en lecture, mais coût d'achat strictement masqué
        cat_resp = cashier_http.get('/catalog/')
        self.assertEqual(cat_resp.status_code, 200)
        self.assertFalse(cat_resp.context['can_view_cost'])
        html = cat_resp.content.decode('utf-8')
        self.assertNotIn('Coût CMUP', html)
        self.assertNotIn('52 345', html)
        self.assertNotIn('52345', html)
        self.assertNotIn('7 234', html)
        # Mutation catalogue interdite au caissier (403)
        self.assertEqual(cashier_http.post('/catalog/', {'action': 'toggle_active', 'product_id': self.drill.id}).status_code, 403)

        # Accès strictement interdit à /backups/ et /accounting/ (403)
        self.assertEqual(cashier_http.get('/backups/').status_code, 403)
        self.assertEqual(cashier_http.get('/accounting/').status_code, 403)

        # Contrôle positif : le responsable (catalog.view_cost) voit bien le coût CMUP
        mgr_http = HttpClient()
        self.assertTrue(mgr_http.login(username='responsable', password='Manager123!'))
        mgr_cat = mgr_http.get('/catalog/')
        self.assertEqual(mgr_cat.status_code, 200)
        self.assertTrue(mgr_cat.context['can_view_cost'])
        self.assertContains(mgr_cat, 'Coût CMUP')

    def test_soft_user_deactivation_revokes_active_session(self):
        cashier_http = HttpClient()
        self.assertTrue(cashier_http.login(username='caissier', password='Cashier123!'))
        self.assertEqual(cashier_http.get('/catalog/').status_code, 200)
        cashier_session_key = cashier_http.session.session_key
        self.assertTrue(Session.objects.filter(session_key=cashier_session_key).exists())

        # L'administrateur désactive le caissier via /users/
        admin_http = HttpClient()
        self.assertTrue(admin_http.login(username='admin', password='Admin123!'))
        resp = admin_http.post('/users/', {'action': 'toggle', 'user_id': self.cashier.id})
        self.assertEqual(resp.status_code, 302)

        self.cashier.refresh_from_db()
        self.assertFalse(self.cashier.is_active)
        self.assertFalse(Session.objects.filter(session_key=cashier_session_key).exists())

        # La session active du caissier est immédiatement révoquée → redirection vers /login/
        after = cashier_http.get('/catalog/')
        self.assertEqual(after.status_code, 302)
        self.assertIn('/login/', after['Location'])


# =============================================================================
# 2. CATALOGUE & FAMILLES D'UNITÉS
# =============================================================================
class CatalogUnitValidationTests(ERPBaseTestCase):
    def test_incompatible_unit_family_conversion_blocked(self):
        # Ciment est en KG (MASS) ; tenter de créer un conditionnement en M (LENGTH) doit échouer
        with self.assertRaises(ValidationError) as ctx:
            ProductPackaging.objects.create(
                product=self.cement,
                unit=self.u_m,
                label='Rouleau aberrant 10m',
                conversion_factor=Decimal('10'),
            )
        self.assertIn('Unités incompatibles', str(ctx.exception))

        # Facteur de conversion <= 0 également bloqué
        with self.assertRaises(ValidationError):
            ProductPackaging.objects.create(
                product=self.drill,
                unit=self.u_box,
                label='Boîte nulle',
                conversion_factor=Decimal('0'),
            )

        # Contrôle positif : même famille COUNT (Unité -> Boîte de 10) autorisée
        pkg = ProductPackaging.objects.create(
            product=self.drill,
            unit=self.u_box,
            label='Boîte de 10',
            conversion_factor=Decimal('10'),
            standard_price=Decimal('700000'),
        )
        self.assertEqual(pkg.conversion_factor, Decimal('10'))


# =============================================================================
# 3. MOTEUR DE TARIFICATION DÉTERMINISTE
# =============================================================================
class PricingEngineTests(ERPBaseTestCase):
    def setUp(self):
        # Règles tarifaires sur Peinture (prix standard = 10 000 FCFA)
        PricingRule.objects.create(
            company=self.company,
            name='Contrat VIP Peinture 8 000 FCFA',
            rule_type='CLIENT_SPECIFIC',
            priority=10,
            product=self.paint,
            client=self.cli_pro_vip,
            min_quantity=1,
            price_mode='FIXED_PRICE',
            value=Decimal('8000'),
        )
        PricingRule.objects.create(
            company=self.company,
            name='Tarif Segment Pro (−15%)',
            rule_type='SEGMENT',
            priority=25,
            category=self.cat_pnt,
            client_segment='PROFESSIONNEL',
            min_quantity=1,
            price_mode='PERCENT_DISCOUNT',
            value=Decimal('15'),
        )
        PricingRule.objects.create(
            company=self.company,
            name='Palier Peinture — Dès 10 pots (9 000 FCFA)',
            rule_type='QUANTITY_TIER',
            priority=35,
            product=self.paint,
            min_quantity=10,
            price_mode='FIXED_PRICE',
            value=Decimal('9000'),
        )

    def test_deterministic_priority_client_contract_gt_segment_gt_tier_gt_standard(self):
        # 4. Prix standard (client comptoir, qté < 10)
        res_std = PricingEngine.resolve(self.company, self.paint, quantity=3, client=None)
        self.assertEqual(res_std['resolved_price'], Decimal('10000'))
        self.assertEqual(res_std['applied_rule'], '')

        # 3. Palier quantité : 10 pots = 9 000 FCFA
        res_tier = PricingEngine.resolve(self.company, self.paint, quantity=10, client=None)
        self.assertEqual(res_tier['resolved_price'], Decimal('9000'))
        self.assertIn('10 pots', res_tier['applied_rule'])

        # 2. Segment client (PROFESSIONNEL −15% = 8 500 FCFA) > Palier quantité (9 000 FCFA) même à qté=10
        res_seg = PricingEngine.resolve(self.company, self.paint, quantity=10, client=self.cli_pro)
        self.assertEqual(res_seg['resolved_price'], Decimal('8500'))
        self.assertIn('Segment Pro', res_seg['applied_rule'])

        # 1. Contrat client (8 000 FCFA) > Segment (8 500) > Palier quantité (9 000) > Standard (10 000)
        res_contract = PricingEngine.resolve(self.company, self.paint, quantity=10, client=self.cli_pro_vip)
        self.assertEqual(res_contract['resolved_price'], Decimal('8000'))
        self.assertIn('Contrat VIP', res_contract['applied_rule'])

        # Vérification E2E via l'API JSON POS
        http = HttpClient()
        self.assertTrue(http.login(username='caissier', password='Cashier123!'))
        api_resp = http.post(
            '/api/pos/resolve-price/',
            data=json.dumps({'product_id': self.paint.id, 'quantity': 10}),
            content_type='application/json',
        )
        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual(api_resp.json()['resolved_price'], 9000)


# =============================================================================
# 4. POS E2E : CAISSE FERMÉE, PIN OVERRIDE, PAIEMENT MIXTE, STOCK, CRÉDIT, SYSCOHADA, FNE
# =============================================================================
class PosE2ETests(ERPBaseTestCase):
    def setUp(self):
        self.cashier_http = HttpClient()
        self.assertTrue(self.cashier_http.login(username='caissier', password='Cashier123!'))

    def test_closed_register_blocks_sale(self):
        # Aucune session ouverte (ID inexistant ou session clôturée)
        ses = CashService.open_session(register=self.register, opening_float=50000, actor=self.cashier)
        CashService.close_session(
            session=ses, counted_cash=50000, counted_mobile=0, counted_card=0, variance_notes='', actor=self.cashier
        )

        payload = {
            'session_id': ses.id,
            'lines': [{'product_id': self.paint.id, 'quantity': 1, 'unit_price': 10000}],
            'payments': [{'payment_method': 'CASH', 'amount': 10000}],
        }
        resp = self.cashier_http.post('/api/pos/checkout/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 'CASH_CLOSED')
        self.assertEqual(StockLevel.objects.get(product=self.paint, warehouse=self.warehouse).physical_qty, Decimal('50'))

    def test_discount_over_5_percent_requires_manager_override_and_succeeds_with_pin_mixed_payment(self):
        ses = CashService.open_session(register=self.register, opening_float=50000, actor=self.cashier)

        # Panier : 1 perceuse @ 75 000 (−10% = 67 500) + 10 pots peinture @ 9 000 = 90 000 → Total TTC = 157 500 FCFA
        base_payload = {
            'session_id': ses.id,
            'client_id': self.cli_btp.id,
            'lines': [
                {'product_id': self.drill.id, 'quantity': 1, 'unit_price': 75000, 'discount_percent': 10},
                {'product_id': self.paint.id, 'quantity': 10, 'unit_price': 9000, 'discount_percent': 0},
            ],
            'payments': [
                {'payment_method': 'CASH', 'amount': 70000},
                {'payment_method': 'WAVE', 'amount': 57500, 'reference': 'WV-2026-QA01'},
                {'payment_method': 'CREDIT', 'amount': 30000},
            ],
            'notes': 'Vente E2E mixte Cash+Wave+Credit',
        }

        # Étape 1 : Sans PIN responsable → 403 MANAGER_OVERRIDE_REQUIRED
        resp_blocked = self.cashier_http.post(
            '/api/pos/checkout/', data=json.dumps(base_payload), content_type='application/json'
        )
        self.assertEqual(resp_blocked.status_code, 403)
        data_blocked = resp_blocked.json()
        self.assertEqual(data_blocked['code'], 'MANAGER_OVERRIDE_REQUIRED')
        self.assertFalse(SalesDocument.objects.exists())

        # Étape 2 : Mauvais PIN → 400 INVALID_PIN
        bad_pin_payload = {**base_payload, 'override_username': 'responsable', 'override_secret': '9999'}
        resp_bad = self.cashier_http.post(
            '/api/pos/checkout/', data=json.dumps(bad_pin_payload), content_type='application/json'
        )
        self.assertEqual(resp_bad.status_code, 400)
        self.assertEqual(resp_bad.json()['code'], 'INVALID_PIN')

        # Étape 3 : Avec PIN 1234 du responsable → 201 OK + vérifications inter-modules complètes
        ok_payload = {**base_payload, 'override_username': 'responsable', 'override_secret': '1234'}
        resp_ok = self.cashier_http.post(
            '/api/pos/checkout/', data=json.dumps(ok_payload), content_type='application/json'
        )
        self.assertEqual(resp_ok.status_code, 201)
        body = resp_ok.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['total_ttc'], 157500)
        self.assertTrue(body['fne_reference'].startswith('FNE-CI-'))

        doc = SalesDocument.objects.get(pk=body['id'])
        self.assertEqual(doc.approved_by_id, self.manager.id)
        self.assertEqual(doc.status, 'PARTIALLY_PAID')
        self.assertEqual(doc.paid_amount, Decimal('127500'))
        self.assertEqual(doc.balance_due, Decimal('30000'))
        self.assertTrue(doc.fne_qr_payload.startswith('DGI-CI|'))

        # Stock déduit atomiquement
        self.assertEqual(StockLevel.objects.get(product=self.drill, warehouse=self.warehouse).physical_qty, Decimal('9'))
        self.assertEqual(StockLevel.objects.get(product=self.paint, warehouse=self.warehouse).physical_qty, Decimal('40'))
        self.assertEqual(StockMovement.objects.filter(document_number=doc.number, movement_type='OUT_POS').count(), 2)

        # Caisse mise à jour (Cash +70 000, Mobile +57 500 ; Crédit non compté en caisse)
        ses.refresh_from_db()
        self.assertEqual(ses.expected_cash, Decimal('120000'))
        self.assertEqual(ses.expected_mobile, Decimal('57500'))

        # Crédit client mis à jour via CreditService + Grand-livre CreditLedgerEntry
        self.cli_btp.refresh_from_db()
        self.assertEqual(self.cli_btp.current_balance, Decimal('130000'))
        ledger = CreditLedgerEntry.objects.get(client=self.cli_btp, reference=doc.number)
        self.assertEqual(ledger.entry_type, 'CHARGE')
        self.assertEqual(ledger.amount, Decimal('30000'))
        self.assertEqual(ledger.balance_after, Decimal('130000'))
        self.assertEqual(ledger.approver_id, self.manager.id)

        # Écriture comptable SYSCOHADA équilibrée (Débit == Crédit == 157 500 FCFA)
        entry = JournalEntry.objects.get(source_doc_type='POS_SALE', source_doc_number=doc.number)
        totals = entry.lines.aggregate(td=Sum('debit'), tc=Sum('credit'))
        self.assertEqual(totals['td'], Decimal('157500'))
        self.assertEqual(totals['tc'], Decimal('157500'))
        by_acc = {l.account.code: (l.debit, l.credit) for l in entry.lines.select_related('account')}
        self.assertEqual(by_acc['571000'], (Decimal('70000'), Decimal('0')))
        self.assertEqual(by_acc['522000'], (Decimal('57500'), Decimal('0')))
        self.assertEqual(by_acc['411000'], (Decimal('30000'), Decimal('0')))
        self.assertEqual(by_acc['701000'][1] + by_acc['443100'][1], Decimal('157500'))


# =============================================================================
# 5. CONFLITS INTER-MODULES : STOCK NÉGATIF, DOUBLE TRANSFO, ANNULATION, ÉCART CAISSE
# =============================================================================
class InterModuleConflictTests(ERPBaseTestCase):
    def test_negative_stock_policy_forbidden_blocks_overselling(self):
        ses = CashService.open_session(register=self.register, opening_float=10000, actor=self.cashier)
        # Stock physique perceuse = 10 ; tenter de vendre 11 doit bloquer (FORBIDDEN)
        with self.assertRaises(BusinessRuleError) as ctx:
            SalesService.pos_checkout(
                company=self.company,
                session_id=ses.id,
                client_id=None,
                raw_lines=[{'product_id': self.drill.id, 'quantity': 11, 'unit_price': 75000}],
                payments=[{'payment_method': 'CASH', 'amount': 825000}],
                actor=self.cashier,
            )
        self.assertEqual(ctx.exception.code, 'INSUFFICIENT_STOCK')
        # Atomicité garantie : aucun document, aucune écriture, stock intact à 10
        self.assertEqual(StockLevel.objects.get(product=self.drill, warehouse=self.warehouse).physical_qty, Decimal('10'))
        self.assertFalse(SalesDocument.objects.exists())
        self.assertFalse(JournalEntry.objects.exists())

    def test_duplicate_quote_to_order_transformation_blocked(self):
        quote = SalesService.create_document(
            company=self.company,
            doc_type='QUOTE',
            client=self.cli_btp,
            warehouse=self.warehouse,
            raw_lines=[{'product_id': self.cement.id, 'quantity': 5}],
            actor=self.manager,
        )
        order = SalesService.transform_document(source_id=quote.id, target_type='ORDER', actor=self.manager)
        self.assertEqual(order.doc_type, 'ORDER')
        self.assertEqual(StockLevel.objects.get(product=self.cement, warehouse=self.warehouse).reserved_qty, Decimal('5'))

        # Deuxième transformation du même Devis en Commande → bloquée (DUPLICATE_TRANSFORM)
        with self.assertRaises(BusinessRuleError) as ctx:
            SalesService.transform_document(source_id=quote.id, target_type='ORDER', actor=self.manager)
        self.assertEqual(ctx.exception.code, 'DUPLICATE_TRANSFORM')
        self.assertEqual(StockLevel.objects.get(product=self.cement, warehouse=self.warehouse).reserved_qty, Decimal('5'))

    def test_order_cancellation_releases_reserved_stock(self):
        order = SalesService.create_document(
            company=self.company,
            doc_type='ORDER',
            client=self.cli_btp,
            warehouse=self.warehouse,
            raw_lines=[{'product_id': self.cement.id, 'quantity': 8}],
            actor=self.manager,
        )
        level = StockLevel.objects.get(product=self.cement, warehouse=self.warehouse)
        self.assertEqual(level.reserved_qty, Decimal('8'))
        self.assertEqual(level.available_qty, Decimal('22'))

        # Annulation sans motif refusée
        with self.assertRaises(BusinessRuleError):
            SalesService.cancel_document(doc_id=order.id, reason='', actor=self.manager)

        # Annulation motivée → libère immédiatement le stock réservé
        cancelled = SalesService.cancel_document(doc_id=order.id, reason='Chantier annulé par client', actor=self.manager)
        self.assertEqual(cancelled.status, 'CANCELLED')
        level.refresh_from_db()
        self.assertEqual(level.reserved_qty, Decimal('0'))
        self.assertEqual(level.available_qty, Decimal('30'))

    def test_cash_closing_variance_threshold_pending_validation_and_658000_758000_accounting(self):
        # --- Scénario A : Déficit de −5 000 FCFA (> seuil 2 000) → PENDING_VALIDATION → 658000 ---
        ses_def = CashService.open_session(register=self.register, opening_float=50000, actor=self.cashier)

        # Justification obligatoire dès qu'un écart != 0 existe
        with self.assertRaises(BusinessRuleError):
            CashService.close_session(
                session=ses_def, counted_cash=45000, counted_mobile=0, counted_card=0,
                variance_notes='', actor=self.cashier,
            )

        ses_def = CashService.close_session(
            session=ses_def, counted_cash=45000, counted_mobile=0, counted_card=0,
            variance_notes='Manquant 5 000 FCFA rendu monnaie', actor=self.cashier,
        )
        self.assertEqual(ses_def.status, 'PENDING_VALIDATION')
        self.assertEqual(ses_def.cash_variance, Decimal('-5000'))
        # Aucune écriture comptable tant que le responsable n'a pas validé
        self.assertFalse(JournalEntry.objects.filter(source_doc_type='CASH_VARIANCE', source_doc_number=ses_def.number).exists())

        # Validation responsable → CLOSED + Débit 658000 / Crédit 571000
        ses_def = CashService.validate_closure(session=ses_def, approver=self.manager)
        self.assertEqual(ses_def.status, 'CLOSED')
        self.assertEqual(ses_def.validated_by_id, self.manager.id)
        entry_def = JournalEntry.objects.get(source_doc_type='CASH_VARIANCE', source_doc_number=ses_def.number)
        lines_def = {l.account.code: (l.debit, l.credit) for l in entry_def.lines.select_related('account')}
        self.assertEqual(lines_def['658000'], (Decimal('5000'), Decimal('0')))
        self.assertEqual(lines_def['571000'], (Decimal('0'), Decimal('5000')))

        # --- Scénario B : Excédent de +3 500 FCFA (> seuil 2 000) → PENDING_VALIDATION → 758000 ---
        ses_exc = CashService.open_session(register=self.register, opening_float=50000, actor=self.cashier)
        ses_exc = CashService.close_session(
            session=ses_exc, counted_cash=53500, counted_mobile=0, counted_card=0,
            variance_notes='Excédent comptage soir', actor=self.cashier,
        )
        self.assertEqual(ses_exc.status, 'PENDING_VALIDATION')
        self.assertEqual(ses_exc.cash_variance, Decimal('3500'))

        ses_exc = CashService.validate_closure(session=ses_exc, approver=self.manager)
        self.assertEqual(ses_exc.status, 'CLOSED')
        entry_exc = JournalEntry.objects.get(source_doc_type='CASH_VARIANCE', source_doc_number=ses_exc.number)
        lines_exc = {l.account.code: (l.debit, l.credit) for l in entry_exc.lines.select_related('account')}
        self.assertEqual(lines_exc['571000'], (Decimal('3500'), Decimal('0')))
        self.assertEqual(lines_exc['758000'], (Decimal('0'), Decimal('3500')))


# =============================================================================
# 6. NOUVEAUX MODULES D'AUDIT : EXONÉRATION TVA ONG, IMPORT CSV, PIÈCES JOINTES, BACKUPS SHA-256
# =============================================================================
class NewAuditModulesTests(ERPBaseTestCase):
    def test_tax_exempt_ong_client_pays_zero_tva(self):
        # Prix catalogue TTC = 10 000 FCFA (TVA 18%). Client ONG exonéré paie HT : 20 000 / 1.18 = 16 949 FCFA, TVA = 0
        ses = CashService.open_session(register=self.register, opening_float=20000, actor=self.cashier)
        doc = SalesService.pos_checkout(
            company=self.company,
            session_id=ses.id,
            client_id=self.cli_ong.id,
            raw_lines=[{'product_id': self.paint.id, 'quantity': 2}],
            payments=[{'payment_method': 'CASH', 'amount': 16949}],
            actor=self.cashier,
        )
        self.assertEqual(doc.tax_total, Decimal('0'))
        self.assertEqual(doc.subtotal_ht, Decimal('16949'))
        self.assertEqual(doc.total_ttc, Decimal('16949'))

        line = doc.lines.first()
        self.assertEqual(line.tax_rate, Decimal('0'))
        self.assertEqual(line.line_tax, Decimal('0'))
        self.assertIn('Exonéré', line.tax_label)
        self.assertIn('DGI-EXON-2026-0842', line.tax_label)

        # Comptabilité : aucune ligne 443100 (TVA collectée), écriture équilibrée 571000 / 701000
        entry = JournalEntry.objects.get(source_doc_type='POS_SALE', source_doc_number=doc.number)
        codes = {l.account.code for l in entry.lines.select_related('account')}
        self.assertEqual(codes, {'571000', '701000'})
        self.assertNotIn('443100', codes)
        totals = entry.lines.aggregate(td=Sum('debit'), tc=Sum('credit'))
        self.assertEqual(totals['td'], totals['tc'])

    def test_csv_data_import_dry_run_simulation_and_commit(self):
        valid_csv = (
            'sku;name;standard_price;purchase_cost\n'
            'IMP-MARTEAU-01;Marteau Arrache-Clous 500g;4500;2800\n'
            'IMP-CLE-12;Clé Mixte Chrome 12mm;2500;1500\n'
        ).encode('utf-8')

        # 1. Simulation (Dry-run) : aucune écriture en base
        f_sim = SimpleUploadedFile('produits.csv', valid_csv, content_type='text/csv')
        job_sim = run_import(
            company=self.company, target='PRODUCTS', uploaded_file=f_sim, commit=False, actor=self.admin
        )
        self.assertEqual(job_sim.status, 'SIMULATED')
        self.assertEqual(job_sim.total_rows, 2)
        self.assertEqual(job_sim.valid_rows, 2)
        self.assertEqual(job_sim.error_rows, 0)
        self.assertFalse(Product.objects.filter(sku='IMP-MARTEAU-01').exists())

        # 2. Fichier invalide même avec commit=True → reste SIMULATED et n'insère rien
        bad_csv = (
            'sku;name;standard_price;purchase_cost\n'
            ';Article sans SKU;1000;500\n'
            'IMP-NEG;Prix négatif;-500;200\n'
        ).encode('utf-8')
        f_bad = SimpleUploadedFile('bad.csv', bad_csv, content_type='text/csv')
        job_bad = run_import(
            company=self.company, target='PRODUCTS', uploaded_file=f_bad, commit=True, actor=self.admin
        )
        self.assertEqual(job_bad.status, 'SIMULATED')
        self.assertEqual(job_bad.error_rows, 2)
        self.assertFalse(Product.objects.filter(sku='IMP-NEG').exists())

        # 3. Import définitif (commit=True) → COMMITTED et articles créés
        f_commit = SimpleUploadedFile('produits.csv', valid_csv, content_type='text/csv')
        job_ok = run_import(
            company=self.company, target='PRODUCTS', uploaded_file=f_commit, commit=True, actor=self.admin
        )
        self.assertEqual(job_ok.status, 'COMMITTED')
        self.assertEqual(job_ok.valid_rows, 2)
        self.assertEqual(job_ok.error_rows, 0)
        p = Product.objects.get(sku='IMP-MARTEAU-01')
        self.assertEqual(p.standard_price, Decimal('4500'))
        self.assertEqual(p.purchase_cost, Decimal('2800'))

    def test_secure_attachment_rejects_exe_html_and_over_5mb(self):
        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=Path(tmp_media)):
                # Rejet .exe
                exe_file = SimpleUploadedFile('malware.exe', b'MZ\x90\x00', content_type='application/octet-stream')
                with self.assertRaises(BusinessRuleError) as ctx_exe:
                    store_attachment(
                        company=self.company, uploaded_file=exe_file,
                        title='Payload', entity_type='GENERAL', entity_ref='REF-1', actor=self.admin,
                    )
                self.assertEqual(ctx_exe.exception.code, 'INVALID_FILE_TYPE')

                # Rejet .html (anti-XSS stocké)
                html_file = SimpleUploadedFile('xss.html', b'<script>alert(1)</script>', content_type='text/html')
                with self.assertRaises(BusinessRuleError) as ctx_html:
                    store_attachment(
                        company=self.company, uploaded_file=html_file,
                        title='Page', entity_type='GENERAL', entity_ref='REF-2', actor=self.admin,
                    )
                self.assertEqual(ctx_html.exception.code, 'INVALID_FILE_TYPE')

                # Rejet > 5 Mo (5 Mo + 1 octet)
                oversized = SimpleUploadedFile(
                    'huge.pdf', b'%PDF-1.4\n' + b'0' * settings.MAX_ATTACHMENT_SIZE_BYTES, content_type='application/pdf'
                )
                with self.assertRaises(BusinessRuleError) as ctx_size:
                    store_attachment(
                        company=self.company, uploaded_file=oversized,
                        title='Gros PDF', entity_type='INVOICE', entity_ref='FAC-001', actor=self.admin,
                    )
                self.assertEqual(ctx_size.exception.code, 'FILE_TOO_LARGE')

                # Contrôle positif : PDF valide → archivé, SHA-256 vérifié, versionné & rétention 10 ans
                pdf_bytes = b'%PDF-1.4\nFacture fournisseur certifiee\n%%EOF'
                ok_pdf = SimpleUploadedFile('facture.pdf', pdf_bytes, content_type='application/pdf')
                att1 = store_attachment(
                    company=self.company, uploaded_file=ok_pdf,
                    title='Facture SOCIMAT', entity_type='PURCHASE', entity_ref='ACH-2026-00001', actor=self.admin,
                )
                self.assertEqual(att1.version, 1)
                self.assertEqual(att1.sha256, hashlib.sha256(pdf_bytes).hexdigest())
                self.assertGreaterEqual(att1.retention_until, (timezone.now() + timedelta(days=3649)).date())

                # Re-téléversement même référence/titre → incrément de version v2
                ok_pdf_v2 = SimpleUploadedFile('facture_v2.pdf', pdf_bytes + b'\n', content_type='application/pdf')
                att2 = store_attachment(
                    company=self.company, uploaded_file=ok_pdf_v2,
                    title='Facture SOCIMAT', entity_type='PURCHASE', entity_ref='ACH-2026-00001', actor=self.admin,
                )
                self.assertEqual(att2.version, 2)
                self.assertEqual(DocumentAttachment.objects.count(), 2)

    def test_backup_creation_and_sha256_integrity_verification(self):
        with tempfile.TemporaryDirectory() as tmp_backup:
            backup_root = Path(tmp_backup)
            with override_settings(BACKUP_ROOT=backup_root):
                rec = create_backup(
                    company=self.company, actor=self.admin, backup_type='JSON_DUMP', notes='Sauvegarde QA'
                )
                self.assertIsInstance(rec, BackupRecord)
                self.assertEqual(rec.backup_type, 'JSON_DUMP')
                backup_file = backup_root / rec.filename
                self.assertTrue(backup_file.exists())
                self.assertGreater(rec.file_size, 0)
                self.assertEqual(len(rec.sha256), 64)
                self.assertEqual(_sha256_file(backup_file), rec.sha256)

                # Altération malveillante du fichier → la restauration doit rejeter avec CORRUPTED_BACKUP
                backup_file.write_bytes(backup_file.read_bytes() + b'\nCORRUPTED')
                with self.assertRaises(BusinessRuleError) as ctx:
                    restore_backup(record=rec, actor=self.admin)
                self.assertEqual(ctx.exception.code, 'CORRUPTED_BACKUP')


# =============================================================================
# 7. TESTS DE RÉGRESSION SÉCURITÉ RED TEAM & ARCHITECTURE (C1-C9, H1-H7)
# =============================================================================
class SecurityAndAuditRemediationTests(ERPBaseTestCase):
    def test_pos_price_tampering_blocked_without_manager_pin(self):
        """Red Team C1 : un caissier ne peut pas envoyer unit_price=1 FCFA sans PIN responsable."""
        ses = CashService.open_session(register=self.register, opening_float=10000, actor=self.cashier)
        self.client.force_login(self.cashier)
        resp = self.client.post(
            '/api/pos/checkout/',
            data=json.dumps({
                'session_id': ses.id,
                'lines': [{'product_id': self.drill.id, 'quantity': 1, 'unit_price': 1, 'discount_percent': 0}],
                'payments': [{'payment_method': 'CASH', 'amount': 1}],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 'MANAGER_OVERRIDE_REQUIRED')

    def test_sales_state_machine_blocks_quote_to_invoice_and_invoice_cancel(self):
        """Architect C4 : QUOTE -> INVOICE interdit (bypass stock) et annulation facture verrouillée interdite."""
        quote = SalesService.create_document(
            company=self.company, doc_type='QUOTE', client=self.cli_btp,
            warehouse=self.warehouse, raw_lines=[{'product_id': self.cement.id, 'quantity': 2}], actor=self.manager,
        )
        with self.assertRaises(BusinessRuleError) as ctx:
            SalesService.transform_document(source_id=quote.id, target_type='INVOICE', actor=self.manager)
        self.assertEqual(ctx.exception.code, 'INVALID_TRANSITION')

        inv = SalesService.create_document(
            company=self.company, doc_type='INVOICE', client=self.cli_btp,
            warehouse=self.warehouse, raw_lines=[{'product_id': self.cement.id, 'quantity': 2}], actor=self.manager,
        )
        with self.assertRaises(BusinessRuleError) as ctx2:
            SalesService.cancel_document(doc_id=inv.id, reason='Tentative suppression fiscale', actor=self.manager)
        self.assertEqual(ctx2.exception.code, 'LOCKED_DOCUMENT')

    def test_multi_company_document_numbers_do_not_collide_and_cashier_cannot_switch(self):
        """Architect C1 & Red Team C3 : numérotation isolée par société + cloisonnement caissier."""
        comp2 = Company.objects.create(code='QP-BOU', legal_name='QuincaPro Bouaké SARL')
        store2 = Store.objects.create(company=comp2, code='MAG-BOU', name='Magasin Bouaké')
        wh2 = Warehouse.objects.create(store=store2, code='DEP-BOU', name='Dépôt Bouaké')
        reg2 = CashRegister.objects.create(store=store2, warehouse=wh2, code='CAISSE-BOU', name='Caisse Bouaké')

        s1 = CashService.open_session(register=self.register, opening_float=10000, actor=self.admin)
        s2 = CashService.open_session(register=reg2, opening_float=10000, actor=self.admin)
        self.assertNotEqual(s1.number, s2.number)
        self.assertTrue(s1.number.startswith('QP-TEST-'))
        self.assertTrue(s2.number.startswith('QP-BOU-'))

        # Un caissier ne peut pas basculer sur la société 2
        self.client.force_login(self.cashier)
        self.client.post('/organization/switch-company/', {'company_id': comp2.id})
        self.assertIsNone(self.client.session.get('active_company_id'))

    def test_manager_pin_bruteforce_locks_approver_account(self):
        """Red Team H1 : 5 mauvais codes PIN verrouillent le compte approbateur."""
        from apps.accounts.services import verify_manager_override
        for _ in range(5):
            with self.assertRaises(BusinessRuleError) as ctx:
                verify_manager_override('responsable', '9999')
            self.assertEqual(ctx.exception.code, 'INVALID_PIN')

        with self.assertRaises(BusinessRuleError) as ctx_locked:
            verify_manager_override('responsable', '1234')
        self.assertEqual(ctx_locked.exception.code, 'ACCOUNT_LOCKED')

    def test_organization_post_requires_manage_permission(self):
        """Red Team C4 : un utilisateur sans organization.manage reçoit 403 sur POST /organization/."""
        ro_role = Role.objects.create(code='ORG_READ', name='Lecture Orga', permissions=['organization.view'])
        ro_user = User.objects.create_user(username='orgreader', password='User123!', role=ro_role, default_company=self.company)
        self.client.force_login(ro_user)
        resp = self.client.post('/organization/', {'action': 'update_rules', 'negative_stock_policy': 'ALWAYS_ALLOWED'})
        self.assertEqual(resp.status_code, 403)
        self.company.refresh_from_db()
        self.assertEqual(self.company.negative_stock_policy, 'FORBIDDEN')
