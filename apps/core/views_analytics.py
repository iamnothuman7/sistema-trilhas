"""
Métricas de Acessos, Tráfego e Visualizações do Site e Painel da Equipe Morcego.
Permite acompanhar quantas pessoas acessaram, quais abas abriram, dispositivos e origens.
"""
from datetime import timedelta
from django.db.models import Count
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from apps.core.models import PageViewRecord
from django.core.exceptions import PermissionDenied
from apps.core.permissions import management_required, is_administrator
from apps.events.models import Event


@management_required
@never_cache
def analytics_overview(request):
    """Dashboard de Métricas e Tráfego do Sistema (somente administração geral: mostra o tráfego de todo o site)."""
    if not is_administrator(request.user):
        raise PermissionDenied("As métricas de acesso do site são restritas à administração geral.")
    event_id = request.GET.get("event_id")
    event = None
    if event_id:
        try:
            event = Event.objects.get(id=event_id)
        except (Event.DoesNotExist, ValueError):
            pass


    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Filtro de período
    period = request.GET.get("period", "7d")
    if period == "today":
        since = today_start
        period_label = "Hoje"
    elif period == "24h":
        since = now - timedelta(hours=24)
        period_label = "Últimas 24 horas"
    elif period == "30d":
        since = now - timedelta(days=30)
        period_label = "Últimos 30 dias"
    elif period == "all":
        since = now - timedelta(days=365)
        period_label = "Todo o histórico"
    else: # 7d padrão
        since = now - timedelta(days=7)
        period_label = "Últimos 7 dias"

    # Filtro de tipo de tráfego
    traffic_type = request.GET.get("traffic", "public")
    qs = PageViewRecord.objects.filter(created_at__gte=since)
    
    if traffic_type == "public":
        qs = qs.filter(is_staff_view=False)
    elif traffic_type == "staff":
        qs = qs.filter(is_staff_view=True)

    if event:
        qs = qs.filter(event_id=event.id)

    # Métricas Principais
    total_views = qs.count()
    unique_visitors = qs.values("visitor_hash").distinct().count()

    today_views = PageViewRecord.objects.filter(created_at__gte=today_start)
    if traffic_type == "public":
        today_views = today_views.filter(is_staff_view=False)
    today_count = today_views.count()

    # Dispositivos
    mobile_count = qs.filter(device_type="MOBILE").count()
    desktop_count = qs.filter(device_type="DESKTOP").count()
    tablet_count = qs.filter(device_type="TABLET").count()
    
    mobile_pct = round((mobile_count / total_views * 100), 1) if total_views else 0
    desktop_pct = round((desktop_count / total_views * 100), 1) if total_views else 0
    tablet_pct = round((tablet_count / total_views * 100), 1) if total_views else 0

    # Páginas / Abas Mais Acessadas
    top_pages_raw = (
        qs.values("page_name", "path")
        .annotate(views=Count("id"), uniques=Count("visitor_hash", distinct=True))
        .order_by("-views")[:12]
    )
    max_page_views = top_pages_raw[0]["views"] if top_pages_raw else 1
    top_pages = []
    for item in top_pages_raw:
        top_pages.append({
            "name": item["page_name"],
            "path": item["path"],
            "views": item["views"],
            "uniques": item["uniques"],
            "pct": round((item["views"] / total_views * 100), 1) if total_views else 0,
            "bar_width": round((item["views"] / max_page_views * 100), 1),
        })

    # Origens de Tráfego (WhatsApp, Instagram, Direto, etc.)
    top_sources_raw = (
        qs.values("referrer_source")
        .annotate(views=Count("id"))
        .order_by("-views")[:8]
    )
    max_source_views = top_sources_raw[0]["views"] if top_sources_raw else 1
    top_sources = []
    for item in top_sources_raw:
        source_name = item["referrer_source"]
        icon = "share-2"
        if "instagram" in source_name.lower():
            icon = "camera"
        elif "whatsapp" in source_name.lower():
            icon = "message-circle"
        elif "google" in source_name.lower():
            icon = "search"
        elif "direto" in source_name.lower():
            icon = "link"

        top_sources.append({
            "source": source_name,
            "icon": icon,
            "views": item["views"],
            "pct": round((item["views"] / total_views * 100), 1) if total_views else 0,
            "bar_width": round((item["views"] / max_source_views * 100), 1),
        })

    # Navegadores
    browsers = (
        qs.values("browser")
        .annotate(views=Count("id"))
        .order_by("-views")[:6]
    )

    # Acessos Recentes ao Vivo (Últimos 25)
    recent_views = qs.select_related().order_by("-created_at")[:25]

    # Gráfico Diário Simples (Últimos 7 dias)
    daily_stats = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).date()
        d_start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
        d_end = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.max.time()))
        
        day_qs = PageViewRecord.objects.filter(created_at__range=(d_start, d_end))
        if traffic_type == "public":
            day_qs = day_qs.filter(is_staff_view=False)
        
        day_views = day_qs.count()
        day_uniques = day_qs.values("visitor_hash").distinct().count()
        daily_stats.append({
            "label": day.strftime("%d/%m"),
            "views": day_views,
            "uniques": day_uniques,
        })
    
    max_day_views = max([d["views"] for d in daily_stats] or [1]) or 1
    for d in daily_stats:
        d["bar_pct"] = max(10, round((d["views"] / max_day_views * 100), 1))

    return render(request, "gestao/analytics/overview.html", {
        "event": event,
        "period": period,
        "period_label": period_label,
        "traffic_type": traffic_type,
        "total_views": total_views,
        "unique_visitors": unique_visitors,
        "today_count": today_count,
        "mobile_pct": mobile_pct,
        "desktop_pct": desktop_pct,
        "tablet_pct": tablet_pct,
        "mobile_count": mobile_count,
        "desktop_count": desktop_count,
        "top_pages": top_pages,
        "top_sources": top_sources,
        "browsers": browsers,
        "recent_views": recent_views,
        "daily_stats": daily_stats,
    })
