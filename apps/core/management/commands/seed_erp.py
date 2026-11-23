from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.accounting.models import Account
from apps.accounts.models import Role, User
from apps.accounts.services import set_user_pin
from apps.cash.models import CashRegister
from apps.cash.services import CashService
from apps.catalog.models import Brand, Category, Product, ProductPackaging, Unit, UnitFamily
from apps.clients.models import Client
from apps.core.permissions import ALL_PERMISSIONS
from apps.inventory.models import StockLevel
from apps.organization.models import Company, Location, Store, Warehouse, Zone
from apps.pricing.models import PricingRule
from apps.purchases.services import PurchaseService
from apps.sales.services import SalesService
from apps.suppliers.models import Supplier
from apps.taxation.models import TaxRule


class Command(BaseCommand):
    help = 'Peuple la base QuincaPro ERP avec un jeu de données réaliste Abidjan (FCFA, SYSCOHADA, Multi-dépôts).'

    def handle(self, *args, **options):
        self.stdout.write('🌱 Initialisation QuincaPro ERP (Django + HTML/CSS)...')

        # 1. Sociétés (Multi-entreprises — Point 2D)
        company, _ = Company.objects.get_or_create(
            code='QP-ABJ',
            defaults={
                'legal_name': 'QuincaPro Abidjan SARL',
                'trade_name': 'Quincaillerie Générale & Matériaux BTP',
                'ncc': 'CI-ABJ-2026-B-04812',
                'rccm': 'CI-ABJ-03-2026-B12-00419',
                'address': "Boulevard VGE, Zone 4C, Marcory",
                'city': 'Abidjan',
                'phone': '+225 27 21 35 40 00',
                'email': 'contact@quincapro.ci',
                'negative_stock_policy': 'FORBIDDEN',
                'valuation_method': 'CMUP',
                'cash_variance_threshold': 2000,
                'fne_enabled': True,
            },
        )
        Company.objects.get_or_create(
            code='QP-BOU',
            defaults={
                'legal_name': 'QuincaPro Bouaké Distribution SARL',
                'trade_name': 'Matériaux Centre Côte d’Ivoire',
                'ncc': 'CI-BKE-2026-B-01944',
                'address': 'Quartier Commerce, Bouaké',
                'city': 'Bouaké',
                'phone': '+225 27 31 63 10 20',
                'negative_stock_policy': 'ALLOWED_WITH_PERMISSION',
                'valuation_method': 'CMUP',
                'cash_variance_threshold': 2000,
            },
        )

        # 2. Hiérarchie officielle : Société → Magasin → Dépôt → Zone → Emplacement (Point 1C)
        store_marcory, _ = Store.objects.get_or_create(
            company=company, code='MAG-Z4',
            defaults={'name': 'Magasin Principal — Zone 4 Marcory', 'address': 'Bd VGE Marcory', 'is_default': True},
        )
        store_yop, _ = Store.objects.get_or_create(
            company=company, code='MAG-YOP',
            defaults={'name': 'Agence Gros Œuvre — Yopougon ZI', 'address': 'Zone Industrielle Yopougon'},
        )
        wh_main, _ = Warehouse.objects.get_or_create(
            code='DEP-MARCORY',
            defaults={'store': store_marcory, 'name': 'Dépôt Principal Marcory', 'is_default': True},
        )
        wh_yop, _ = Warehouse.objects.get_or_create(
            code='DEP-YOP',
            defaults={'store': store_yop, 'name': 'Dépôt Matériaux Yopougon ZI'},
        )
        z_out, _ = Zone.objects.get_or_create(warehouse=wh_main, code='Z-OUT', defaults={'name': 'Zone Outillage & Visserie'})
        z_gros, _ = Zone.objects.get_or_create(warehouse=wh_main, code='Z-BTP', defaults={'name': 'Zone Gros Œuvre & Peinture'})
        Zone.objects.get_or_create(warehouse=wh_main, code='Z-QUAR', defaults={'name': 'Zone Quarantaine SAV', 'is_quarantine': True})
        loc_a1, _ = Location.objects.get_or_create(zone=z_out, code='RAY-A1', defaults={'label': 'Rayon A1 — Électroportatif'})
        loc_b2, _ = Location.objects.get_or_create(zone=z_out, code='RAY-B2', defaults={'label': 'Rayon B2 — Visserie'})
        loc_c1, _ = Location.objects.get_or_create(zone=z_gros, code='RAY-C1', defaults={'label': 'Rayon C1 — Peintures & Ciment'})

        # 3. Fiscalité Ivoirienne (Point 2A)
        tva18, _ = TaxRule.objects.get_or_create(code='TVA18', defaults={'name': 'TVA Normale CI 18%', 'kind': 'VAT_STANDARD', 'rate': 18, 'is_default': True})
        TaxRule.objects.get_or_create(code='TVA9', defaults={'name': 'TVA Réduite CI 9%', 'kind': 'VAT_REDUCED', 'rate': 9})
        TaxRule.objects.get_or_create(code='TVA0', defaults={'name': 'TVA 0% Export', 'kind': 'VAT_ZERO', 'rate': 0})
        TaxRule.objects.get_or_create(code='EXON', defaults={'name': 'Exonéré de TVA', 'kind': 'EXEMPT', 'rate': 0})

        # 4. Rôles & Utilisateurs (Module 01)
        roles_spec = [
            ('ADMIN', 'Administrateur', 100, True, ALL_PERMISSIONS),
            ('OWNER', 'Propriétaire PME', 50, True, ALL_PERMISSIONS),
            ('MANAGER', 'Responsable Magasin', 25, True, [p for p in ALL_PERMISSIONS if p != 'backups.manage']),
            ('CASHIER', 'Caissier Comptoir', 5, False, [
                'dashboard.view', 'catalog.view', 'pricing.view', 'clients.view',
                'pos.sell', 'sales.view', 'cash.view', 'cash.operate',
            ]),
            ('STOREKEEPER', 'Magasinier', 0, False, [
                'dashboard.view', 'catalog.view', 'inventory.view', 'inventory.transfer',
                'purchases.view', 'purchases.receive', 'stock_counts.view', 'stock_counts.count',
                'deliveries.view', 'deliveries.manage', 'returns.view', 'returns.manage',
            ]),
            ('ACCOUNTANT', 'Comptable SYSCOHADA', 0, False, [
                'dashboard.view', 'reporting.view', 'reporting.view_margins', 'taxation.view',
                'clients.view', 'suppliers.view', 'sales.view', 'purchases.view', 'cash.view',
                'expenses.view', 'expenses.approve', 'accounting.view', 'accounting.manage', 'audit.view',
            ]),
        ]
        role_map = {}
        for code, name, max_disc, can_ov, perms in roles_spec:
            r, _ = Role.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'max_discount_percent': max_disc,
                    'can_approve_override': can_ov,
                    'permissions': perms,
                    'is_system': True,
                },
            )
            role_map[code] = r

        def ensure_user(username, full_name, pwd, role_code, pin='', is_super=False):
            u, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': f'{username}@quincapro.ci',
                    'full_name': full_name,
                    'role': role_map[role_code],
                    'default_company': company,
                    'default_warehouse': wh_main,
                    'is_superuser': is_super,
                    'is_staff': is_super,
                },
            )
            if created:
                u.set_password(pwd)
                u.save()
            if pin and not u.pin_hash:
                set_user_pin(u, pin)
            return u

        admin_u = ensure_user('admin', 'Kouadio Serge (Directeur)', 'Admin123!', 'ADMIN', pin='1234', is_super=True)
        resp_u = ensure_user('responsable', 'Traoré Aminata (Responsable 01)', 'Quinca123!', 'MANAGER', pin='1234')
        cash_u = ensure_user('caissier', 'Koné Moussa (Caissier 02)', 'Quinca123!', 'CASHIER')
        ensure_user('magasinier', 'Yao Franck (Magasinier)', 'Quinca123!', 'STOREKEEPER')
        ensure_user('comptable', 'Brou Nadège (Comptable)', 'Quinca123!', 'ACCOUNTANT')

        # 5. Plan comptable SYSCOHADA (incluant 658000 Déficit / 758000 Excédent de caisse — Audit 3C)
        accounts = [
            ('401000', 'Fournisseurs — Dettes commerciales', 4, 'LIABILITY'),
            ('411000', 'Clients — Créances commerciales', 4, 'ASSET'),
            ('443100', 'TVA facturée sur ventes', 4, 'LIABILITY'),
            ('445200', 'TVA récupérable sur achats', 4, 'ASSET'),
            ('521000', 'Banques locales', 5, 'ASSET'),
            ('522000', 'Monnaie électronique (Wave / OM / MoMo)', 5, 'ASSET'),
            ('571000', 'Caisse Siège', 5, 'ASSET'),
            ('601000', 'Achats de marchandises', 6, 'EXPENSE'),
            ('622000', 'Charges opérationnelles & Loyers', 6, 'EXPENSE'),
            ('658000', 'Charges diverses — Déficits de caisse', 6, 'EXPENSE'),
            ('701000', 'Ventes de marchandises', 7, 'REVENUE'),
            ('758000', 'Produits divers — Excédents de caisse', 7, 'REVENUE'),
        ]
        for code, name, cls, typ in accounts:
            Account.objects.get_or_create(code=code, defaults={'name': name, 'account_class': cls, 'account_type': typ})

        # 6. Unités, Catégories, Marques
        fam_cnt, _ = UnitFamily.objects.get_or_create(code='COUNT', defaults={'name': 'Pièces / Quantité'})
        fam_mass, _ = UnitFamily.objects.get_or_create(code='MASS', defaults={'name': 'Masse / Poids'})
        fam_len, _ = UnitFamily.objects.get_or_create(code='LENGTH', defaults={'name': 'Longueur'})
        fam_vol, _ = UnitFamily.objects.get_or_create(code='VOLUME', defaults={'name': 'Volume'})

        u_unit, _ = Unit.objects.get_or_create(code='U', defaults={'family': fam_cnt, 'name': 'Unité', 'symbol': 'u'})
        u_box, _ = Unit.objects.get_or_create(code='BOITE', defaults={'family': fam_cnt, 'name': 'Boîte', 'symbol': 'bte'})
        u_ctn, _ = Unit.objects.get_or_create(code='CARTON', defaults={'family': fam_cnt, 'name': 'Carton', 'symbol': 'ctn'})
        u_sac, _ = Unit.objects.get_or_create(code='SAC', defaults={'family': fam_mass, 'name': 'Sac 50kg', 'symbol': 'sac'})
        u_m, _ = Unit.objects.get_or_create(code='M', defaults={'family': fam_len, 'name': 'Mètre', 'symbol': 'm', 'allow_fractional': True, 'precision': 2})
        u_rlx, _ = Unit.objects.get_or_create(code='ROULEAU', defaults={'family': fam_len, 'name': 'Rouleau', 'symbol': 'rlx'})
        u_pot, _ = Unit.objects.get_or_create(code='POT', defaults={'family': fam_vol, 'name': 'Pot / Bidon', 'symbol': 'pot'})

        cat_out, _ = Category.objects.get_or_create(code='OUTILLAGE', defaults={'name': 'Outillage Électroportatif'})
        cat_vis, _ = Category.objects.get_or_create(code='VISSERIE', defaults={'name': 'Visserie & Boulonnerie'})
        cat_pnt, _ = Category.objects.get_or_create(code='PEINTURE', defaults={'name': 'Peintures & Solvants'})
        cat_mac, _ = Category.objects.get_or_create(code='MACONNERIE', defaults={'name': 'Gros Œuvre & Maçonnerie'})
        cat_ele, _ = Category.objects.get_or_create(code='ELECTRICITE', defaults={'name': 'Électricité & Câbles'})

        b_bosch, _ = Brand.objects.get_or_create(name='Bosch')
        b_seig, _ = Brand.objects.get_or_create(name='Seigneurie')
        b_soc, _ = Brand.objects.get_or_create(name='SOCIMAT')
        b_ing, _ = Brand.objects.get_or_create(name='Ingco')

        # 7. Fournisseurs & Clients
        sup1, _ = Supplier.objects.get_or_create(
            code='FRN-001',
            defaults={'company': company, 'name': 'Ivoire Outillage Pro', 'contact_name': 'M. Diallo', 'phone': '+225 27 21 24 80 10', 'conformity_rate': 98.5, 'rating': 4.8, 'current_debt': 350000},
        )
        sup2, _ = Supplier.objects.get_or_create(
            code='FRN-002',
            defaults={'company': company, 'name': 'SOCIMAT & Matériaux CI', 'contact_name': 'Mme Bamba', 'phone': '+225 27 23 45 60 90', 'conformity_rate': 95.0, 'rating': 4.4},
        )

        cli_btp, _ = Client.objects.get_or_create(
            code='CLI-001',
            defaults={
                'company': company, 'name': 'Entreprise KOFFI BTP & Travaux', 'segment': 'ENTREPRISE',
                'phone': '+225 07 07 55 12 89', 'address': 'Cocody Angré 8e Tranche',
                'credit_limit': 500000, 'current_balance': 450000, 'payment_term_days': 30,
            },
        )
        Client.objects.get_or_create(
            code='CLI-002',
            defaults={
                'company': company, 'name': 'M. Ouattara Lassina (Artisan Pro)', 'segment': 'PROFESSIONNEL',
                'phone': '+225 05 54 33 21 00', 'address': 'Marcory Remblais',
                'credit_limit': 250000, 'current_balance': 45000, 'payment_term_days': 15,
            },
        )
        Client.objects.get_or_create(
            code='CLI-003',
            defaults={
                'company': company, 'name': 'ONG Bâtir Ensemble CI (Exonéré TVA)', 'segment': 'ENTREPRISE',
                'phone': '+225 27 22 40 11 22', 'address': 'Plateau Abidjan',
                'tax_exempt': True, 'exemption_certificate': 'DGI-EXON-2026-0842', 'credit_limit': 1000000,
            },
        )

        # 8. Articles + Conditionnements + Stocks
        products_spec = [
            ('OUT-BOSCH-550', '3165140842013', 'Perceuse à percussion Bosch GSB 550W', cat_out, b_bosch, u_unit, sup1, loc_a1, 52000, 75000, 4, 15, 6, True),
            ('VIS-BOIS-4X40', '6180001000401', 'Vis à bois acier zingué 4x40mm', cat_vis, b_ing, u_unit, sup1, loc_b2, 12, 25, 1000, 4700, 10000, True),
            ('PNT-BLC-5L', '6180002005001', 'Peinture acrylique blanc mat 5L Seigneurie', cat_pnt, b_seig, u_pot, sup2, loc_c1, 7200, 10000, 15, 48, 60, True),
            ('CIM-CPJ-50KG', '6180003050001', 'Ciment CPJ 42.5 — Sac 50 kg', cat_mac, b_soc, u_sac, sup2, loc_c1, 4450, 5200, 50, 30, 180, True),
            ('FER-HA12-12M', '6180003012001', 'Fer à béton HA12 (barre 12m)', cat_mac, b_soc, u_unit, sup2, loc_c1, 5400, 6800, 40, 100, 200, True),
            ('ELE-CAB-25', '6180004025001', 'Câble électrique rigide RO2V 3G2.5mm²', cat_ele, b_ing, u_m, sup1, loc_a1, 480, 750, 100, 340, 500, False),
        ]
        pmap = {}
        for sku, ean, name, cat, br, u, sup, loc, cost, price, min_s, q_main, q_yop, fav in products_spec:
            p, _ = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    'barcode': ean, 'name': name, 'category': cat, 'brand': br, 'base_unit': u,
                    'tax_rule': tva18, 'default_supplier': sup, 'default_location': loc,
                    'purchase_cost': cost, 'standard_price': price, 'min_stock': min_s, 'is_favorite': fav,
                },
            )
            pmap[sku] = p
            StockLevel.objects.get_or_create(product=p, warehouse=wh_main, defaults={'physical_qty': q_main, 'cmup': cost, 'location': loc})
            StockLevel.objects.get_or_create(product=p, warehouse=wh_yop, defaults={'physical_qty': q_yop, 'cmup': cost})

        # Conditionnements (Module 03 : Carton de 1000 & Boîte de 100)
        ProductPackaging.objects.get_or_create(
            product=pmap['VIS-BOIS-4X40'], label='Boîte de 100',
            defaults={'unit': u_box, 'conversion_factor': 100, 'standard_price': 2200, 'barcode': '6180001000402', 'is_sale_default': True},
        )
        ProductPackaging.objects.get_or_create(
            product=pmap['VIS-BOIS-4X40'], label='Carton de 1 000',
            defaults={'unit': u_ctn, 'conversion_factor': 1000, 'standard_price': 19500, 'barcode': '6180001000403', 'is_purchase_default': True},
        )
        ProductPackaging.objects.get_or_create(
            product=pmap['ELE-CAB-25'], label='Rouleau de 100 m',
            defaults={'unit': u_rlx, 'conversion_factor': 100, 'standard_price': 68000, 'barcode': '6180004025002', 'is_purchase_default': True},
        )

        # 9. Règles tarifaires indépendantes (Module 04)
        if not PricingRule.objects.exists():
            PricingRule.objects.bulk_create([
                PricingRule(company=company, name='Tarif Contrat KOFFI BTP — Ciment 5 000 FCFA', rule_type='CLIENT_SPECIFIC', priority=10, product=pmap['CIM-CPJ-50KG'], client=cli_btp, min_quantity=1, price_mode='FIXED_PRICE', value=5000),
                PricingRule(company=company, name='Tarif Artisan Pro — Outillage (−7%)', rule_type='SEGMENT', priority=25, category=cat_out, client_segment='PROFESSIONNEL', min_quantity=1, price_mode='PERCENT_DISCOUNT', value=7),
                PricingRule(company=company, name='Palier Peinture — Dès 10 pots (9 000 FCFA)', rule_type='QUANTITY_TIER', priority=35, product=pmap['PNT-BLC-5L'], min_quantity=10, price_mode='FIXED_PRICE', value=9000),
                PricingRule(company=company, name='Palier Peinture — Dès 5 pots (9 500 FCFA)', rule_type='QUANTITY_TIER', priority=40, product=pmap['PNT-BLC-5L'], min_quantity=5, price_mode='FIXED_PRICE', value=9500),
            ])

        # 10. Caisses & Session ouverte
        reg1, _ = CashRegister.objects.get_or_create(code='CAISSE-01', defaults={'store': store_marcory, 'warehouse': wh_main, 'name': 'Caisse Comptoir Principale #1'})
        CashRegister.objects.get_or_create(code='CAISSE-02', defaults={'store': store_marcory, 'warehouse': wh_main, 'name': 'Caisse Pro & Chantiers #2'})
        ses = CashService.get_open_session(company, register=reg1)
        if not ses:
            ses = CashService.open_session(register=reg1, opening_float=50000, actor=cash_u)

        # 11. Une vente POS d'exemple & une commande fournisseur
        if not company.salesdocument_set.exists():
            SalesService.pos_checkout(
                company=company,
                session_id=ses.id,
                client_id=None,
                raw_lines=[
                    {'product_id': pmap['OUT-BOSCH-550'].id, 'quantity': 1, 'unit_price': 75000, 'discount_percent': 10},
                    {'product_id': pmap['PNT-BLC-5L'].id, 'quantity': 10, 'unit_price': 9000, 'discount_percent': 0},
                ],
                payments=[
                    {'payment_method': 'CASH', 'amount': 100000},
                    {'payment_method': 'WAVE', 'amount': 57500, 'reference': 'WV-2026-INIT'},
                ],
                actor=cash_u,
                override_username=resp_u.username,
                override_secret='1234',
                notes='Vente démonstration paiement mixte + approbation PIN',
            )
            SalesService.create_document(
                company=company,
                doc_type='QUOTE',
                client=cli_btp,
                warehouse=wh_main,
                raw_lines=[
                    {'product_id': pmap['CIM-CPJ-50KG'].id, 'quantity': 20},
                    {'product_id': pmap['FER-HA12-12M'].id, 'quantity': 15},
                ],
                actor=admin_u,
                notes='Devis chantier Angré 8e Tranche',
            )
            PurchaseService.create_order(
                company=company,
                supplier=sup2,
                warehouse=wh_main,
                lines_input=[{'product_id': pmap['CIM-CPJ-50KG'].id, 'ordered_qty': 100, 'unit_cost': 4450}],
                actor=admin_u,
                notes='Réapprovisionnement urgent Ciment CPJ',
            )

        self.stdout.write(self.style.SUCCESS('✅ Base QuincaPro ERP (Django) prête !'))
        self.stdout.write('   Comptes : admin/Admin123! (PIN 1234) · responsable/Quinca123! (PIN 1234) · caissier/Quinca123! · magasinier/Quinca123! · comptable/Quinca123!')
