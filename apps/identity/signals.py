from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from apps.core.rate_limits import reset_rate_limit
from .authentication import normalize_login_identifier


@receiver(user_logged_in, dispatch_uid="identity.reset_successful_login_rate_limit")
def reset_authenticated_account(sender, request, user, **kwargs):
    # Only a real login event may reset the authenticated account's budget. A
    # redirect from an already authenticated browser proves no submitted identity.
    reset_rate_limit("login:account", normalize_login_identifier(user.email))
