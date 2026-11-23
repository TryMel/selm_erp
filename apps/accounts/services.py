from datetime import timedelta
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from apps.audit.services import log_business, log_technical
from apps.core.exceptions import BusinessRuleError
from .models import User

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


def set_user_pin(user: User, raw_pin: str):
    user.pin_hash = make_password(raw_pin) if raw_pin else ''
    user.save(update_fields=['pin_hash'])


def _record_failed_attempt(user_id: int, category_msg: str):
    """Incrémente et committe atomiquement le compteur d'échecs AVANT de lever l'exception."""
    with transaction.atomic():
        u = User.objects.select_for_update().select_related('default_company').get(pk=user_id)
        u.failed_attempts += 1
        if u.failed_attempts >= MAX_FAILED_ATTEMPTS:
            u.failed_attempts = 0
            u.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)
        u.save(update_fields=['failed_attempts', 'locked_until'])
        log_technical(
            company=u.default_company,
            category='AUTH',
            level='WARNING',
            message=category_msg,
            actor_name=u.username,
        )


def authenticate_with_lockout(request, username: str, password: str) -> User:
    clean = (username or '').strip().lower()
    user = User.objects.select_related('role', 'default_company').filter(username__iexact=clean).first()
    if not user:
        log_technical(category='AUTH', level='WARNING', message=f'Tentative de connexion identifiant inconnu: {clean}')
        raise BusinessRuleError('Identifiant ou mot de passe incorrect.', 'INVALID_CREDENTIALS')

    if not user.is_active:
        raise BusinessRuleError('Ce compte est désactivé. Contactez un administrateur.', 'ACCOUNT_INACTIVE')

    if user.locked_until and user.locked_until > timezone.now():
        mins = max(1, int((user.locked_until - timezone.now()).total_seconds() // 60) + 1)
        raise BusinessRuleError(f'Compte temporairement verrouillé. Réessayez dans {mins} min.', 'ACCOUNT_LOCKED')

    auth_user = authenticate(request, username=user.username, password=password)
    if not auth_user:
        _record_failed_attempt(user.id, f'Échec mot de passe pour @{user.username}')
        raise BusinessRuleError('Identifiant ou mot de passe incorrect.', 'INVALID_CREDENTIALS')

    User.objects.filter(pk=user.id).update(failed_attempts=0, locked_until=None)
    log_business(
        company=user.default_company,
        action='LOGIN_SUCCESS',
        module='ACCOUNTS',
        entity_type='User',
        entity_id=user.id,
        actor=user,
        summary=f'Connexion réussie de {user.display_name}',
        ip_address=request.META.get('REMOTE_ADDR', ''),
    )
    return auth_user


def verify_manager_override(username: str, secret: str) -> User:
    """Module 01 F04 : Vérifie PIN responsable avec verrouillage anti-bruteforce committé même en cas d'erreur."""
    clean = (username or '').strip().lower()
    approver = User.objects.select_related('role', 'default_company').filter(username__iexact=clean).first()
    if not approver or not approver.is_active:
        raise BusinessRuleError('Compte responsable invalide ou inactif.', 'INVALID_APPROVER')
    if not approver.can_approve_override:
        raise BusinessRuleError(f"{approver.display_name} n'est pas habilité à accorder cette autorisation.", 'FORBIDDEN')
    if approver.locked_until and approver.locked_until > timezone.now():
        raise BusinessRuleError('Compte responsable temporairement verrouillé après trop de tentatives.', 'ACCOUNT_LOCKED')

    pin_ok = bool(approver.pin_hash and check_password(secret, approver.pin_hash))
    pwd_ok = (not approver.pin_hash) and approver.check_password(secret)
    if not (pin_ok or pwd_ok):
        _record_failed_attempt(approver.id, f'Échec code PIN responsable pour @{approver.username}')
        raise BusinessRuleError('Code PIN responsable incorrect.', 'INVALID_PIN')

    if approver.failed_attempts:
        User.objects.filter(pk=approver.id).update(failed_attempts=0)
    return approver


def revoke_user_sessions(user: User):
    """Révoque immédiatement toutes les sessions actives lors d'une désactivation."""
    for ses in Session.objects.filter(expire_date__gte=timezone.now()):
        data = ses.get_decoded()
        if str(data.get('_auth_user_id')) == str(user.id):
            ses.delete()
