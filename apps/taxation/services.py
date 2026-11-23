import hashlib
from decimal import Decimal
from django.utils import timezone


def resolve_effective_tax(product, client=None) -> tuple[Decimal, bool, str]:
    """Résout le taux de TVA applicable en combinant l'article et le statut fiscal du client."""
    if client and getattr(client, 'tax_exempt', False):
        ref = f"Exonéré ({client.exemption_certificate or 'Attestation'})"
        return Decimal('0'), True, ref
    tax_rule = getattr(product, 'tax_rule', None)
    if tax_rule:
        is_exempt = tax_rule.kind == 'EXEMPT' or tax_rule.rate == 0
        return Decimal(str(tax_rule.rate)), is_exempt, tax_rule.name
    return Decimal('18'), False, 'TVA 18%'


def generate_fne_stamp(company, document_number: str, total_ttc) -> tuple[str, str]:
    """Génère la référence & empreinte FNE (Facture Normalisée Électronique — DGI CI ready)."""
    ts = timezone.now().strftime('%Y%m%d%H%M%S')
    raw = f'{company.ncc}|{document_number}|{int(round(Decimal(str(total_ttc))))}|{ts}'
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16].upper()
    fne_ref = f'FNE-CI-{timezone.now().year}-{digest[:10]}'
    qr_payload = f'DGI-CI|NCC:{company.ncc}|DOC:{document_number}|TTC:{int(round(Decimal(str(total_ttc))))}|SIG:{digest}'
    return fne_ref, qr_payload
