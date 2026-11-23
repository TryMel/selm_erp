from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError
from apps.core.money import parse_int
from apps.core.permissions import require_perm, user_has_perm
from .models import Role, User
from .services import authenticate_with_lockout, revoke_user_sessions, set_user_pin


def login_view(request):
    if request.user.is_authenticated:
        return redirect('/')
    error = None
    username = ''
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        try:
            user = authenticate_with_lockout(request, username, password)
            login(request, user)
            next_url = request.GET.get('next') or '/'
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                next_url = '/'
            return redirect(next_url)
        except BusinessRuleError as exc:
            error = exc.message
    return render(request, 'accounts/login.html', {'error': error, 'username': username})


def logout_view(request):
    if request.method == 'POST':
        logout(request)
    return redirect('/login/')


@require_perm('users.view')
def users_list_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'users.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['users.manage']}, status=403)
        action = request.POST.get('action')
        try:
            if action == 'create':
                uname = request.POST.get('username', '').strip().lower()
                if not uname or User.objects.filter(username__iexact=uname).exists():
                    raise BusinessRuleError('Identifiant invalide ou déjà utilisé.')
                pwd = request.POST.get('password', '')
                if len(pwd) < 6:
                    raise BusinessRuleError('Mot de passe de 6 caractères minimum.')
                role = get_object_or_404(Role, pk=parse_int(request.POST.get('role_id'), 'rôle'))
                # Empêche l'escalade de privilèges par un non-superuser/non-admin
                if role.code in ('ADMIN', 'OWNER') and not (request.user.is_superuser or (request.user.role and request.user.role.code in ('ADMIN', 'OWNER'))):
                    raise BusinessRuleError('Seul un Administrateur peut attribuer un rôle Direction / Admin.')
                u = User.objects.create(
                    username=uname,
                    email=request.POST.get('email', '').strip(),
                    full_name=request.POST.get('full_name', '').strip() or uname,
                    role=role,
                    default_company=request.company,
                )
                u.set_password(pwd)
                u.save()
                if request.POST.get('pin'):
                    set_user_pin(u, request.POST['pin'].strip())
                log_business(company=request.company, action='USER_CREATED', module='ACCOUNTS', entity_type='User', entity_id=u.id, actor=request.user, summary=f'Création compte @{u.username} ({role.name})')
                messages.success(request, f'Compte @{u.username} créé.')
            elif action == 'toggle':
                qs = User.objects.all() if request.user.is_superuser else User.objects.filter(default_company=request.company)
                u = get_object_or_404(qs, pk=parse_int(request.POST.get('user_id'), 'utilisateur'))
                if u.id == request.user.id:
                    raise BusinessRuleError('Impossible de désactiver votre propre compte.')
                if (u.is_superuser or (u.role and u.role.code in ('ADMIN', 'OWNER'))) and not request.user.is_superuser:
                    raise BusinessRuleError('Action interdite sur un compte Administrateur / Direction.')
                u.is_active = not u.is_active
                u.save(update_fields=['is_active'])
                if not u.is_active:
                    revoke_user_sessions(u)
                log_business(company=request.company, action='USER_TOGGLED', module='ACCOUNTS', entity_type='User', entity_id=u.id, actor=request.user, summary=f"{'Réactivation' if u.is_active else 'Désactivation + révocation sessions'} @{u.username}")
                messages.success(request, f"Statut de @{u.username} mis à jour.")
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/users/')

    users_qs = User.objects.select_related('role', 'default_warehouse').order_by('-is_active', 'username')
    if not request.user.is_superuser:
        users_qs = users_qs.filter(default_company=request.company)
    roles_qs = Role.objects.all()
    if not (request.user.is_superuser or (request.user.role and request.user.role.code in ('ADMIN', 'OWNER'))):
        roles_qs = roles_qs.exclude(code__in=['ADMIN', 'OWNER'])
    return render(request, 'accounts/users.html', {'users': users_qs, 'roles': roles_qs})
