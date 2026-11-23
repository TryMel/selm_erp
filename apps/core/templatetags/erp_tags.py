from decimal import Decimal
from django import template
from apps.core.permissions import user_has_perm

register = template.Library()


@register.filter
def fcfa(value):
    if value is None or value == '':
        return '—'
    try:
        num = int(round(Decimal(str(value))))
        return f'{num:,}'.replace(',', ' ') + ' FCFA'
    except Exception:
        return str(value)


@register.filter
def qty(value):
    if value is None or value == '':
        return '—'
    try:
        val = Decimal(str(value)).normalize()
        txt = format(val, 'f')
        return txt.rstrip('0').rstrip('.') if '.' in txt else txt
    except Exception:
        return str(value)


@register.filter
def has_perm(user, perm_code):
    return user_has_perm(user, perm_code)
