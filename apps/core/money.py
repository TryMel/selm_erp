from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from apps.core.exceptions import BusinessRuleError

ZERO = Decimal('0')


def round_fcfa(amount) -> Decimal:
    """Arrondi monétaire FCFA (entier sans décimales)."""
    try:
        val = Decimal(str(amount if amount not in (None, '') else 0))
    except (InvalidOperation, ValueError, TypeError):
        raise BusinessRuleError('Montant numérique invalide.', 'INVALID_AMOUNT')
    return val.quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def round_cost(amount) -> Decimal:
    """Arrondi coût unitaire CMUP à 2 décimales pour préserver la précision sur visserie/cartons."""
    try:
        val = Decimal(str(amount if amount not in (None, '') else 0))
    except (InvalidOperation, ValueError, TypeError):
        raise BusinessRuleError('Coût numérique invalide.', 'INVALID_COST')
    return val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def round_qty(value, precision: int = 4) -> Decimal:
    try:
        val = Decimal(str(value if value not in (None, '') else 0))
    except (InvalidOperation, ValueError, TypeError):
        raise BusinessRuleError('Quantité numérique invalide.', 'INVALID_QTY')
    fmt = '1' if precision <= 0 else '0.' + '0' * precision
    return val.quantize(Decimal(fmt), rounding=ROUND_HALF_UP)


def parse_int(value, field_name: str = 'valeur', default=None) -> int:
    if value in (None, '') and default is not None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        raise BusinessRuleError(f'Paramètre entier invalide ({field_name}).', 'INVALID_INPUT')


def sanitize_csv_cell(value):
    """Neutralise l'injection de formules Excel (=, +, -, @, tab, CR) dans les exports CSV."""
    if value is None:
        return ''
    s = str(value)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


def compute_line_totals(quantity, unit_price, discount_pct=0, tax_rate=18, tax_exempt=False, base_tax_rate=18):
    """
    Calcule HT, TVA, Remise et TTC.
    Si le client est exonéré de TVA (tax_exempt=True) alors que le prix catalogue est TTC (base_tax_rate > 0),
    extrait le prix HT afin de ne pas surfacturer la TVA au client exonéré.
    """
    qty = round_qty(quantity)
    pu = round_fcfa(unit_price)
    disc = max(ZERO, min(Decimal('100'), round_qty(discount_pct, 2)))
    rate = ZERO if tax_exempt else max(ZERO, round_qty(tax_rate, 2))
    base_rate = max(ZERO, round_qty(base_tax_rate, 2))

    if tax_exempt and base_rate > ZERO:
        pu_effective = pu / (Decimal('1') + base_rate / Decimal('100'))
        gross = qty * pu_effective
    else:
        gross = qty * pu

    discount_amount = round_fcfa(gross * disc / Decimal('100'))
    total_ttc = round_fcfa(gross - discount_amount)
    if rate > ZERO:
        total_ht = round_fcfa(total_ttc / (Decimal('1') + rate / Decimal('100')))
    else:
        total_ht = total_ttc
    tax_amount = round_fcfa(total_ttc - total_ht)
    return {
        'discount_amount': discount_amount,
        'total_ht': total_ht,
        'tax_amount': tax_amount,
        'total_ttc': total_ttc,
    }
