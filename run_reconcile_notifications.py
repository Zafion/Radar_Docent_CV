"""Reconcile detection/validation into eligible outbox rows; never sends."""
from app.services.notification_eligibility import reconcile_notifications
if __name__=='__main__':
    print(f'Avisos validados reconciliados: {len(reconcile_notifications())}')
