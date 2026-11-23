from django.conf import settings
from apps.core.permissions import user_has_perm


def erp_context(request):
    """Contexte global injecté proprement dans tous les templates HTML."""
    user = getattr(request, 'user', None)
    company = getattr(request, 'company', None)
    companies = getattr(request, 'available_companies', [])
    perms = set()
    if user and user.is_authenticated:
        if user.is_superuser or (getattr(user, 'role', None) and user.role.code in ('ADMIN', 'OWNER')):
            from apps.core.permissions import ALL_PERMISSIONS
            perms = set(ALL_PERMISSIONS)
        elif getattr(user, 'role', None):
            perms = set(user.role.permissions or [])

    return {
        'debug': settings.DEBUG,
        'active_company': company,
        'available_companies': companies,
        'user_perms': perms,
        'can_view_cost': user_has_perm(user, 'catalog.view_cost'),
        'can_view_margins': user_has_perm(user, 'reporting.view_margins'),
    }
