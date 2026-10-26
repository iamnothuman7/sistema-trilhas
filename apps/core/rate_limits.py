"""Database-backed fixed windows shared by all workers; no process-local cache.

Each UPDATE tests the limit and increments it in the database, so concurrent
workers cannot spend the same slot. Expiration is fixed, not extended by abuse.
"""
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address
from math import ceil
import secrets

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import RateLimitBucket


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


def _key(scope, identity):
    return salted_hmac("morcego-rate-limit-v1", f"{scope}\0{identity}", algorithm="sha256").hexdigest()


def client_ip(request):
    """Only the local reverse proxy may supply X-Real-IP; ignore X-Forwarded-For."""
    try:
        peer = ip_address(request.META.get("REMOTE_ADDR", ""))
    except ValueError:
        return "unknown"
    if peer.is_loopback:
        try:
            return str(ip_address(request.META.get("HTTP_X_REAL_IP", "")))
        except ValueError:
            pass
    return str(peer)


def consume_rate_limit(scope, identity, *, limit, window_seconds, cost=1):
    """Spend one slot or return the retry delay; stores only a keyed digest."""
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate limits and windows must be positive.")
    if not isinstance(cost, int) or isinstance(cost, bool) or not 1 <= cost <= limit:
        raise ValueError("Rate-limit cost must be an integer between 1 and the limit.")
    key = _key(scope, identity)
    now = timezone.now()
    expires_at = now + timedelta(seconds=window_seconds)
    try:
        # Periodically prune a bounded batch; even blocked windows do not grow forever.
        if secrets.randbelow(128) == 0:
            expired = list(RateLimitBucket.objects.filter(expires_at__lte=now).values_list("pk", flat=True)[:500])
            RateLimitBucket.objects.filter(pk__in=expired, expires_at__lte=now).delete()
        for _ in range(3):
            if RateLimitBucket.objects.filter(pk=key, expires_at__lte=now).update(count=cost, expires_at=expires_at):
                return RateLimitDecision(True)
            if RateLimitBucket.objects.filter(pk=key, expires_at__gt=now, count__lte=limit-cost).update(count=F("count") + cost):
                return RateLimitDecision(True)
            bucket = RateLimitBucket.objects.filter(pk=key).values("expires_at").first()
            if bucket:
                return RateLimitDecision(False, max(1, ceil((bucket["expires_at"] - now).total_seconds())))
            try:
                with transaction.atomic():
                    RateLimitBucket.objects.create(key=key, count=cost, expires_at=expires_at)
                return RateLimitDecision(True)
            except IntegrityError:
                # Another worker inserted the same bucket. Retry the conditional UPDATE.
                continue
    except OperationalError:
        # A transient locked/unavailable DB cannot silently disable brute-force protection.
        return RateLimitDecision(False, 5)
    return RateLimitDecision(False, 5)


def reset_rate_limit(scope, identity):
    try:
        with transaction.atomic():
            return bool(RateLimitBucket.objects.filter(pk=_key(scope, identity)).update(count=0))
    except OperationalError:
        # A successful authentication remains valid if this optional reset fails.
        # The existing counter stays in force; there is no fail-open allowance.
        return False


def rate_limit_request(request, scope, *, ip_limit, window_seconds=900, account=None, account_limit=None):
    decision = consume_rate_limit(scope + ":ip", client_ip(request), limit=ip_limit, window_seconds=window_seconds)
    if not decision.allowed or account is None or account_limit is None:
        return decision
    return consume_rate_limit(scope + ":account", account, limit=account_limit, window_seconds=window_seconds)
