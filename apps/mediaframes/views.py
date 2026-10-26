"""
Views da API de remoção de fundo por IA (U2Net) e backup das montagens 3D Pop-Out (estilo Flávio Folia / NaBio).
"""
import hashlib
import logging
import warnings
from io import BytesIO
from time import perf_counter

from django.contrib.auth import get_user_model
from django.core.exceptions import RequestDataTooBig, TooManyFilesSent
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.core.rate_limits import consume_rate_limit
from .background import PROCESSING_LOCK, remove_background
from .models import GeneratedMontage
from .access import authorized_pilot_art
from .image_validation import (
    InvalidArtwork, MAX_UPLOAD_BYTES, MAX_REQUEST_BYTES, MAX_IMAGE_PIXELS, canonical_png,
)

logger = logging.getLogger(__name__)
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _client_ip(request):
    # The edge owns REMOTE_ADDR. Untrusted forwarded values are not identity.
    return request.META.get("REMOTE_ADDR")


def _limit(request, scope, *, limit, window_seconds):
    result = consume_rate_limit(scope, str(request.user.pk), limit=limit, window_seconds=window_seconds)
    if result.allowed:
        return None
    response = _error("Você fez muitas solicitações de arte. Aguarde um pouco e tente novamente.", 429)
    response["Retry-After"] = str(result.retry_after)
    return response


def _artwork_upload(request):
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_REQUEST_BYTES:
            raise InvalidArtwork("A imagem deve ter até 12 MB.", 413)
        return canonical_png(request.FILES.get("image"))
    except (RequestDataTooBig, TooManyFilesSent) as exc:
        raise InvalidArtwork("Envie uma única imagem de até 12 MB.", 413) from exc
    except (ValueError, MultiPartParserError) as exc:
        if isinstance(exc, InvalidArtwork):
            raise
        raise InvalidArtwork("Não foi possível receber essa imagem.") from exc


def _delete_unused_artwork(storage, name):
    # Old installations used flat names. Accept only this managed directory,
    # and never delete a file still referenced by any montage.
    if (not name or not name.startswith("frames/generated/") or "\\" in name
            or ".." in name.split("/") or not name.endswith(".png")):
        return
    if not GeneratedMontage.objects.filter(generated_image=name).exists():
        try:
            storage.delete(name)
        except OSError:
            logger.exception("montage_previous_artwork_cleanup_failed")


def _save_artwork(montage, raw, *, version, team_name, downloaded=False):
    """Serialize metadata/file replacement and remove superseded files on commit."""
    new_name = None
    storage = montage.generated_image.storage
    try:
        with transaction.atomic():
            # Acquire the write lock on SQLite too (select_for_update is a no-op
            # there), before loading the file currently owned by the row.
            GeneratedMontage.objects.filter(pk=montage.pk).update(downloads_count=F("downloads_count"))
            locked = GeneratedMontage.objects.select_for_update().get(pk=montage.pk)
            old_name = locked.generated_image.name
            digest = hashlib.sha256(raw).hexdigest()
            locked.version = slugify(version)[:60] or "trilhao-fogo"
            locked.pilot_name = montage.registration.person.full_name[:120]
            locked.team_name = team_name.strip()[:120]
            locked.downloads_count += int(downloaded)
            if digest != locked.sha256 or not old_name or not storage.exists(old_name):
                locked.generated_image.save(f"{locked.pk}/{digest[:24]}.png", ContentFile(raw), save=False)
                new_name = locked.generated_image.name
            locked.sha256 = digest
            locked.file_size = len(raw)
            locked.save()
            if old_name and old_name != locked.generated_image.name:
                transaction.on_commit(lambda: _delete_unused_artwork(storage, old_name))
        return digest
    except Exception:
        if new_name:
            _delete_unused_artwork(storage, new_name)
        raise


def _authorized_montage(request, montage_id):
    if not request.user.is_authenticated or not request.user.is_active:
        return None
    montage = GeneratedMontage.objects.filter(pk=montage_id, owner=request.user, registration__isnull=False).first()
    if not montage or not authorized_pilot_art(request.user, montage.registration_id):
        return None
    return montage


@never_cache
def story_access(request, registration_id):
    if request.method != "GET":
        return _error("Método não permitido.", 405)
    if not authorized_pilot_art(request.user, registration_id):
        return _error("A arte exige uma inscrição de piloto paga e vinculada à sua conta.", 403)
    return JsonResponse({"allowed": True})


@never_cache
def remove_photo_background(request):
    if request.method != "POST":
        response = _error("Envie sua foto para criar o recorte.", 405)
        response["Allow"] = "POST"
        return response

    if not request.user.is_authenticated or not request.user.is_active:
        return _error("Entre na sua conta para criar a arte do piloto confirmado.", 403)

    limited = _limit(request, "artwork-segmentation-minute", limit=5, window_seconds=60)
    if limited is not None:
        return limited
    limited = _limit(request, "artwork-segmentation-day", limit=30, window_seconds=86400)
    if limited is not None:
        return limited
    if GeneratedMontage.objects.filter(owner=request.user).count() >= 200:
        return _error("Você atingiu o limite de artes salvas. Fale com a organização para liberar espaço.", 429)

    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_REQUEST_BYTES:
            return _error("A foto é muito grande. Escolha uma imagem de até 12 MB.", 413)
        registration = authorized_pilot_art(request.user, request.POST.get("registration_id"))
        if not registration:
            return _error("A arte exige uma inscrição de piloto paga e vinculada à sua conta.", 403)
        photo = request.FILES.get("photo")
    except (RequestDataTooBig, TooManyFilesSent):
        return _error("Envie uma única foto de até 12 MB.", 413)
    except (ValueError, MultiPartParserError):
        return _error("Não foi possível receber essa foto. Tente novamente.")

    if photo is None:
        return _error("Escolha uma foto para continuar.")
    if photo.size > MAX_UPLOAD_BYTES:
        return _error("A foto é muito grande. Escolha uma imagem de até 12 MB.", 413)

    try:
        raw = photo.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            return _error("A foto é muito grande. Escolha uma imagem de até 12 MB.", 413)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in ALLOWED_FORMATS:
                    return _error("Use uma foto JPG, PNG ou WebP.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    return _error("Escolha uma foto com até 12 megapixels.", 413)
                if getattr(source, "is_animated", False):
                    return _error("Escolha uma foto estática JPG, PNG ou WebP.")
                source.verify()
            with Image.open(BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return _error("A resolução da foto é muito grande. Escolha outra imagem.", 413)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return _error("Não foi possível abrir essa foto. Tente outra imagem.")

    if not PROCESSING_LOCK.acquire(blocking=False):
        image.close()
        response = _error("Já estamos preparando uma foto. Tente novamente em instantes.", 503)
        response["Retry-After"] = "3"
        return response

    try:
        inference_started = perf_counter()
        cutout = remove_background(image)
        inference_ms = (perf_counter() - inference_started) * 1000
        encode_started = perf_counter()
        cutout.info.clear()
        output = BytesIO()
        cutout.save(output, format="PNG", compress_level=3)
        cutout.close()
        encode_ms = (perf_counter() - encode_started) * 1000
    except Exception:
        logger.exception("Local background removal failed")
        return _error("Não foi possível preparar o recorte agora. Tente novamente.", 503)
    finally:
        PROCESSING_LOCK.release()
        image.close()

    try:
        with transaction.atomic():
            get_user_model().objects.filter(pk=request.user.pk).update(last_login=F("last_login"))
            if GeneratedMontage.objects.filter(owner=request.user).count() >= 200:
                return _error("Você atingiu o limite de artes salvas. Fale com a organização para liberar espaço.", 429)
            montage = GeneratedMontage.objects.create(
                event=registration.event, owner=request.user, registration=registration,
                pilot_name=registration.person.full_name[:120], ip_address=_client_ip(request),
            )
    except Exception:
        logger.exception("montage_start_log_failed")
        return _error("Não foi possível salvar sua arte agora. Tente novamente.", 503)

    response = HttpResponse(output.getvalue(), content_type="image/png")
    response["Server-Timing"] = f"segmentation;dur={inference_ms:.1f}, encode;dur={encode_ms:.1f}"
    response["X-Montage-ID"] = str(montage.pk)
    response["Content-Disposition"] = 'inline; filename="foto-sem-fundo.png"'
    return response


@never_cache
def backup_montage(request, montage_id):
    if request.method != "POST":
        return _error("Método não permitido.", 405)

    montage = _authorized_montage(request, montage_id)
    if not montage:
        return _error("Montagem não encontrada.", 404)

    limited = _limit(request, "artwork-files", limit=30, window_seconds=60)
    if limited is not None:
        return limited
    try:
        raw = _artwork_upload(request)
    except InvalidArtwork as exc:
        return _error(str(exc), exc.status)
    digest = _save_artwork(montage, raw, version=request.POST.get("version", "trilhao-fogo"),
        team_name=request.POST.get("team_name", ""))

    return JsonResponse({"ok": True, "archive_id": digest})


@never_cache
def deliver_download(request, montage_id):
    if request.method != "POST":
        return _error("Método não permitido.", 405)

    montage = _authorized_montage(request, montage_id)
    if not montage:
        return _error("Montagem indisponível para esta conta ou inscrição.", 403)
    limited = _limit(request, "artwork-files", limit=30, window_seconds=60)
    if limited is not None:
        return limited
    try:
        raw = _artwork_upload(request)
    except InvalidArtwork as exc:
        return _error(str(exc), exc.status)
    _save_artwork(montage, raw, version=request.POST.get("version", "trilhao-fogo"),
        team_name=request.POST.get("team_name", ""), downloaded=True)

    response = HttpResponse(raw, content_type="image/png")
    response["Content-Disposition"] = f'attachment; filename="piloto-confirmado-{montage.pk}.png"'
    response["X-Content-Type-Options"] = "nosniff"
    download_id = request.POST.get("download_id", "")[:120]
    download_id = "".join(character for character in download_id
        if (character.isascii() and character.isalnum()) or character in "-_")
    if download_id:
        response["X-Download-ID"] = download_id
    return response
