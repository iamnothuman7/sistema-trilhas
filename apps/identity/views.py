from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from apps.core.rate_limits import rate_limit_request
from .authentication import normalize_login_identifier
from .models import User, Person, UserPersonLink


def safe_next(request):
    destination = request.GET.get("next") or request.POST.get("next")
    if destination and url_has_allowed_host_and_scheme(
        destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return destination
    return ""

@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def user_login(request):
    """
    Login rápido, responsivo e seguro no padrão NaBio.
    Permite entrar com e-mail completo ou apenas o nome de usuário (ex: 'kelvim' ou 'admin').
    """
    if request.user.is_authenticated:
        if safe_next(request):
            return redirect(safe_next(request))
        if request.user.is_staff or request.user.event_memberships.filter(revoked_at__isnull=True).exists():
            return redirect("gestao:dashboard")
        return redirect("minha_conta:dashboard")

    if request.method == "POST":
        login_input = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")

        email_to_auth = normalize_login_identifier(login_input)

        user = authenticate(request, email=email_to_auth, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f"Bem-vindo de volta, {user.full_name}!")
            next_url = request.GET.get("next") or request.POST.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            if user.is_staff or user.event_memberships.filter(revoked_at__isnull=True).exists():
                return redirect("gestao:dashboard")
            return redirect("minha_conta:dashboard")
        else:
            messages.error(request, "Credenciais não conferem. Verifique seu login e senha.")

    return render(request, "identity/login.html", {"next_url": safe_next(request)})

def user_logout(request):
    logout(request)
    messages.info(request, "Você encerrou sua sessão com sucesso.")
    return redirect("core:home")

@sensitive_post_parameters("password", "confirm_password")
@require_http_methods(["GET", "POST"])
def user_register(request):
    if request.user.is_authenticated:
        return redirect(safe_next(request) or "minha_conta:dashboard")

    if request.method == "POST":
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()
        city = request.POST.get("city", "Tianguá").strip()
        state = request.POST.get("state", "CE").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        decision = rate_limit_request(request, "signup", ip_limit=20, account=email, account_limit=5)
        if not decision.allowed:
            messages.error(request, "Muitas tentativas de cadastro. Aguarde alguns minutos e tente novamente.")
            response = render(request, "identity/register.html", {"next_url": safe_next(request)}, status=429)
            response["Retry-After"] = str(decision.retry_after)
            response["Cache-Control"] = "private, no-store"
            return response

        password_errors = []
        if len(password) <= 4096:
            try:
                validate_password(password, user=User(email=email, full_name=full_name))
            except ValidationError as exc:
                password_errors = exc.messages

        try:
            validate_email(email)
            valid_email = True
        except ValidationError:
            valid_email = False

        if not full_name or not email or not password:
            messages.error(request, "Por favor, preencha todos os campos obrigatórios.")
        elif not valid_email or len(email) > 254:
            messages.error(request, "Informe um e-mail válido.")
        elif len(full_name) > 150 or len(phone) > 30 or len(city) > 100 or len(state) != 2:
            messages.error(request, "Revise seu nome, telefone, cidade e a sigla do estado.")
        elif password != confirm_password:
            messages.error(request, "As senhas digitadas não coincidem.")
        elif len(password) > 4096:
            messages.error(request, "A senha é muito longa. Use até 4096 caracteres.")
        elif password_errors:
            for error in password_errors:
                messages.error(request, error)
        elif User.objects.filter(email=email).exists():
            messages.error(request, "Já existe uma conta cadastrada com este e-mail.")
        else:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(email=email, password=password, full_name=full_name, phone=phone)
                    # A typed email is not proof of ownership of an existing ticket/person.
                    person = Person.objects.create(email=email, full_name=full_name, phone=phone, city=city, state=state.upper())
                    UserPersonLink.objects.create(user=user, person=person)
            except IntegrityError:
                messages.error(request, "Já existe uma conta cadastrada com este e-mail.")
                return render(request, "identity/register.html", {"next_url": safe_next(request)})
            login(request, user)
            messages.success(request, "Cadastro realizado com sucesso!")
            return redirect(safe_next(request) or "minha_conta:dashboard")

    return render(request, "identity/register.html", {"next_url": safe_next(request)})
