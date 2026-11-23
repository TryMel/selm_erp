from .models import Company


def user_can_switch_companies(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return bool(getattr(user, 'role', None) and user.role.code in ('ADMIN', 'OWNER'))


class ActiveCompanyMiddleware:
    """Injecte la société active avec isolation stricte par rôle (multi-entreprises sécurisé)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        all_companies = list(Company.objects.filter(is_active=True))

        if user and user.is_authenticated and not user_can_switch_companies(user):
            # Un employé non-direction reste strictement cloisonné sur sa société d'affectation
            own = next((c for c in all_companies if c.id == user.default_company_id), None)
            request.available_companies = [own] if own else all_companies[:1]
            request.company = request.available_companies[0] if request.available_companies else None
            return self.get_response(request)

        request.available_companies = all_companies
        active_id = request.session.get('active_company_id')
        active = next((c for c in all_companies if c.id == active_id), None)
        if not active and user and user.is_authenticated and getattr(user, 'default_company_id', None):
            active = next((c for c in all_companies if c.id == user.default_company_id), None)
        if not active and all_companies:
            active = all_companies[0]
        request.company = active
        return self.get_response(request)
