from .models import BusinessAuditLog, TechnicalLog


def log_business(
    *,
    action: str,
    module: str,
    entity_type: str,
    summary: str,
    company=None,
    entity_id='',
    document_number='',
    actor=None,
    approver=None,
    details=None,
    ip_address='',
):
    return BusinessAuditLog.objects.create(
        company=company,
        action=action,
        module=module,
        entity_type=entity_type,
        entity_id=str(entity_id or ''),
        document_number=document_number or '',
        actor=actor,
        approver=approver,
        summary=summary,
        details=details or {},
        ip_address=ip_address or '',
    )


def log_technical(*, category: str, message: str, level: str = 'INFO', company=None, payload=None, actor_name: str = ''):
    return TechnicalLog.objects.create(
        company=company,
        level=level,
        category=category,
        message=message,
        payload=payload or {},
        actor_name=actor_name,
    )
