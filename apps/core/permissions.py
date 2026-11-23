from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

ALL_PERMISSIONS = [
    'dashboard.view',
    'reporting.view',
    'reporting.view_margins',
    'organization.view',
    'organization.manage',
    'taxation.view',
    'taxation.manage',
    'catalog.view',
    'catalog.manage',
    'catalog.view_cost',
    'pricing.view',
    'pricing.manage',
    'clients.view',
    'clients.manage',
    'clients.override_credit',
    'suppliers.view',
    'suppliers.manage',
    'inventory.view',
    'inventory.adjust',
    'inventory.transfer',
    'inventory.allow_negative',
    'stock_counts.view',
    'stock_counts.count',
    'stock_counts.validate',
    'cash.view',
    'cash.operate',
    'cash.validate_close',
    'pos.sell',
    'sales.view',
    'sales.manage',
    'deliveries.view',
    'deliveries.manage',
    'purchases.view',
    'purchases.manage',
    'purchases.receive',
    'returns.view',
    'returns.manage',
    'expenses.view',
    'expenses.create',
    'expenses.approve',
    'accounting.view',
    'accounting.manage',
    'documents.view',
    'documents.manage',
    'data_import.manage',
    'backups.manage',
    'users.view',
    'users.manage',
    'audit.view',
]


def user_has_perm(user, perm_code: str) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    role = getattr(user, 'role', None)
    if not role:
        return False
    if role.code in ('ADMIN', 'OWNER'):
        return True
    return perm_code in (role.permissions or [])


def require_perm(*perms: str):
    """Décorateur de restriction par module & action (HTML 403 ou JSON 403)."""

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if all(user_has_perm(request.user, p) for p in perms):
                return view_func(request, *args, **kwargs)
            is_json = request.headers.get('Accept', '').startswith('application/json') or request.path.startswith('/api/')
            if is_json:
                return JsonResponse(
                    {'ok': False, 'code': 'FORBIDDEN', 'message': f"Permission requise : {', '.join(perms)}"},
                    status=403,
                )
            return render(
                request,
                'errors/403.html',
                {'required_perms': perms},
                status=403,
            )

        return _wrapped

    return decorator
