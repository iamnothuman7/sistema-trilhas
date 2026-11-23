"""Importa o backup extraído pelo navegador do site antigo (admin + gestão).

Uso:
    python manage.py import_site_backup <pasta_do_backup> [--dry-run]

A pasta deve conter admin_dump.json, gestao_paginas.json, gestao_detalhes.json
e a pasta arquivos/ (mídia pública e documentos privados).

Senhas não fazem parte do backup: cada usuário recebe uma senha aleatória e
entra pela recuperação de senha.
"""
import json
import mimetypes
import re
import secrets
import shutil
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.utils import timezone

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MONTHS = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
     "setembro", "outubro", "novembro", "dezembro"], start=1)}
# Ordem de importação: pais antes de filhos (as checagens de chave ficam para o fim).
ADMIN_ORDER = [
    "core/organization", "identity/user", "identity/person", "events/event", "events/route",
    "events/legaldocumentversion", "events/sponsor", "events/motorcycleraffle",
    "catalog/product", "catalog/lot", "catalog/coupon", "catalog/capacityresource",
    "catalog/capacityhold", "catalog/inventoryitem", "identity/eventmembership",
    "checkout/order", "checkout/receivabledue", "registrations/registration",
    "finance/vendor", "finance/expense", "mediaframes/frametemplate",
    "payments/feestatement", "payments/providerevent", "finance/financialentry", "core/auditlog",
]
SKIP_ADMIN = {"catalog/capacityhold"}  # reservas temporárias expiram; não migram


class Skip(Exception):
    pass


class Command(BaseCommand):
    help = "Importa o backup do site antigo (admin + gestão) para o banco atual."

    def add_arguments(self, parser):
        parser.add_argument("backup_dir")
        parser.add_argument("--dry-run", action="store_true")

    # ------------------------------------------------------------------ util
    def tz(self):
        return timezone.get_default_timezone()

    def parse_dt_parts(self, d, t):
        if not d:
            return None
        dd = datetime.strptime(d.strip(), "%d/%m/%Y")
        if t:
            parts = [int(x) for x in t.strip().split(":")]
            dd = dd.replace(hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0)
        return timezone.make_aware(dd, self.tz())

    def parse_pt_long(self, s):
        """'18 de Novembro de 2026 às 15:15' -> datetime; '18 de Novembro de 2026' -> date."""
        if not s or s.strip() in ("-", ""):
            return None
        m = re.match(r"(\d{1,2}) de (\w+) de (\d{4})(?: (?:às|as) (\d{1,2}):(\d{2})(?::(\d{2}))?)?", s.strip(), re.I)
        if not m:
            return None
        day, month, year = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
        if m.group(4) is None:
            return date(year, month, day)
        return timezone.make_aware(datetime(year, month, day, int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)), self.tz())

    def brl_cents(self, s):
        if s in (None, ""):
            return None
        s = str(s).replace("R$", "").strip().replace(".", "").replace(",", ".")
        return int((Decimal(s) * 100).quantize(Decimal("1")))

    def media_path(self, url):
        if not url:
            return ""
        path = unquote(urlsplit(url).path)
        return path[len("/media/"):] if path.startswith("/media/") else ""

    def convert(self, field, raw, values, prefix=""):
        """Converte o valor do formulário para o tipo do campo."""
        if isinstance(field, models.DateTimeField):
            if field.name in values or prefix + field.name in values:
                v = values.get(prefix + field.name)
                if v in (None, ""):
                    return None
                try:
                    return timezone.make_aware(datetime.fromisoformat(v), self.tz()) if "T" in v or "-" in v[:5] else self.parse_dt_parts(v, None)
                except ValueError:
                    return self.parse_dt_parts(v, None)
            return self.parse_dt_parts(values.get(prefix + field.name + "_0"), values.get(prefix + field.name + "_1"))
        if raw is None:
            raise Skip
        if isinstance(field, models.BooleanField):
            return raw is True or str(raw).lower() in ("true", "on", "1", "sim")
        if isinstance(field, models.DateField):
            if raw == "":
                return None
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt).date()
                except ValueError:
                    pass
            raise CommandError(f"Data inválida em {field}: {raw!r}")
        if isinstance(field, models.JSONField):
            if isinstance(raw, (dict, list)):
                return raw
            return json.loads(raw) if raw not in ("", None) else field.get_default()
        if isinstance(field, models.FileField):
            return raw
        if field.is_relation:
            if raw in ("", None):
                return None
            return field.target_field.to_python(raw)
        if isinstance(field, models.DecimalField):
            if raw == "":
                return None
            try:
                return Decimal(str(raw).replace(",", "."))
            except InvalidOperation:
                return Decimal(str(raw).replace(".", "").replace(",", "."))
        if raw == "" and field.null and not isinstance(field, (models.CharField, models.TextField)):
            return None
        return field.to_python(raw)

    def apply_values(self, obj, values, files, prefix=""):
        for field in obj._meta.concrete_fields:
            if field.primary_key:
                continue
            key = prefix + field.name
            if isinstance(field, models.FileField):
                if key in files:
                    setattr(obj, field.attname, self.media_path(files[key]))
                continue
            has = key in values or (isinstance(field, models.DateTimeField) and key + "_0" in values)
            if not has:
                continue
            try:
                setattr(obj, field.attname, self.convert(field, values.get(key), values, prefix))
            except Skip:
                pass

    def apply_readonly(self, obj, readonly):
        labels = {k.rstrip(":").strip().lower(): v for k, v in readonly.items() if not re.fullmatch(r"ro\d+", k)}
        for field in obj._meta.concrete_fields:
            label = str(field.verbose_name).strip().lower()
            if label not in labels:
                continue
            raw = labels[label]
            if raw in ("-", ""):
                if field.null:
                    setattr(obj, field.attname, None)
                continue
            if field.primary_key:
                continue
            if field.is_relation:
                setattr(obj, field.attname, self.resolve_fk(field, raw))
            elif isinstance(field, models.DateTimeField):
                setattr(obj, field.attname, self.parse_pt_long(raw))
            elif isinstance(field, models.DateField):
                v = self.parse_pt_long(raw)
                setattr(obj, field.attname, v.date() if isinstance(v, datetime) else v)
            elif isinstance(field, models.BooleanField):
                setattr(obj, field.attname, raw.strip().lower() in ("sim", "true", "yes"))
            elif isinstance(field, models.JSONField):
                try:
                    setattr(obj, field.attname, json.loads(raw))
                except ValueError:
                    pass
            else:
                setattr(obj, field.attname, field.to_python(raw))

    def resolve_fk(self, field, text):
        target = field.related_model
        m = UUID_RE.search(text)
        if m and target._meta.pk.get_internal_type() == "UUIDField":
            return m.group(0)
        email = re.search(r"\(([^()]+@[^()]+)\)", text) or re.fullmatch(r"\s*(\S+@\S+)\s*", text)
        if email and hasattr(target, "email"):
            found = target.objects.filter(email__iexact=email.group(1)).values_list("pk", flat=True).first()
            if found:
                return found
        if target._meta.label == "events.Event":
            for pk, title in target.objects.values_list("pk", "title"):
                if text.startswith(title):
                    return pk
        self.warn(f"Não consegui ligar {field} ao texto {text!r}; ficou vazio.")
        return None

    def fill_required(self, obj, created=None):
        now = timezone.now()
        for field in obj._meta.concrete_fields:
            if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
                if getattr(obj, field.attname) is None:
                    setattr(obj, field.attname, created or now)

    def raw_save(self, obj):
        self.fill_required(obj)
        obj.save_base(raw=True)
        self.count[obj._meta.label] = self.count.get(obj._meta.label, 0) + 1

    def warn(self, msg):
        self.warnings.append(msg)

    # ----------------------------------------------------------------- admin
    def import_admin(self, dump):
        models_data = dump["models"]
        keys = sorted(models_data, key=lambda u: ADMIN_ORDER.index(u.strip("/").split("/", 1)[1])
                      if u.strip("/").split("/", 1)[1] in ADMIN_ORDER else 999)
        for url in keys:
            app_model = url.strip("/").split("/", 1)[1]
            if app_model in SKIP_ADMIN:
                continue
            app_label, model_name = app_model.split("/")
            if app_label == "auth":
                continue
            Model = apps.get_model(app_label, model_name)
            for item in models_data[url]:
                path = urlsplit(item["url"]).path.strip("/").split("/")
                pk = Model._meta.pk.to_python(unquote(path[3]))
                obj = Model(pk=pk)
                self.apply_values(obj, item["fields"], item.get("files", {}))
                self.apply_readonly(obj, item.get("readonly", {}))
                if Model._meta.label == "identity.User":
                    obj.set_password(secrets.token_urlsafe(32))
                try:
                    with transaction.atomic():
                        self.raw_save(obj)
                except Exception as exc:  # registra e segue: o relatório final mostra tudo
                    self.warn(f"{Model._meta.label} {pk}: {exc}")
                    continue
                self.save_m2m(obj, item["fields"])
                self.import_inlines(Model, obj, item)

    def save_m2m(self, obj, values, prefix=""):
        for field in obj._meta.many_to_many:
            raw = values.get(prefix + field.name)
            if isinstance(raw, list) and raw:
                getattr(obj, field.name).set(raw)

    def import_inlines(self, Model, parent, item):
        values, files = item["fields"], item.get("files", {})
        prefixes = sorted({m.group(1) for k in values for m in [re.match(r"^([a-z_]+)-(\d+)-", k)] if m})
        for prefix in prefixes:
            if prefix.startswith("initial"):
                continue
            rel = next((r for r in Model._meta.related_objects
                        if r.get_accessor_name() == prefix or r.related_model._meta.model_name == prefix
                        or r.name == prefix), None)
            if rel is None:
                self.warn(f"Inline {prefix} de {Model._meta.label} sem modelo correspondente; ignorado.")
                continue
            Child = rel.related_model
            total = int(values.get(f"{prefix}-TOTAL_FORMS", 0) or 0)
            initial = int(values.get(f"{prefix}-INITIAL_FORMS", 0) or 0)
            for n in range(total):
                p = f"{prefix}-{n}-"
                row = {k: v for k, v in values.items() if k.startswith(p)}
                row_files = {k: v for k, v in files.items() if k.startswith(p)}
                pk_name = Child._meta.pk.name
                pk_raw = values.get(p + pk_name) or values.get(p + "id")
                if pk_name == rel.field.name:  # OneToOne com pk = pai (ex.: tema)
                    pk_raw = pk_raw or parent.pk
                if not pk_raw and n >= initial and not row_files:
                    continue  # formulário extra vazio
                if not pk_raw:
                    self.warn(f"Linha {p} de {Model._meta.label} {parent.pk} sem id; ignorada.")
                    continue
                child = Child(pk=Child._meta.pk.to_python(pk_raw))
                self.apply_values(child, row, row_files, prefix=p)
                setattr(child, rel.field.attname, parent.pk)
                try:
                    with transaction.atomic():
                        self.raw_save(child)
                except Exception as exc:
                    self.warn(f"{Child._meta.label} {pk_raw}: {exc}")
                    continue
                self.save_m2m(child, row, prefix=p)

    # ---------------------------------------------------------------- gestão
    def form(self, page, idx=0):
        forms = (page or {}).get("forms") or []
        return forms[idx]["fields"] if len(forms) > idx else {}

    def import_gestao(self, pages, details, backup):
        Event = apps.get_model("events", "Event")
        Organization = apps.get_model("core", "Organization")
        org = Organization.objects.first()

        def matching(pattern):
            for url, page in pages.items():
                m = re.search(pattern, urlsplit(url).path)
                if m:
                    yield url, page, m

        # Perfis de patrocinadores (marcas)
        Profile = apps.get_model("events", "SponsorProfile")
        archived = {int(m.group(1)) for _, _, m in matching(r"^/gestao/patrocinadores/(\d+)/restaurar/$")}
        for url, page, m in matching(r"^/gestao/patrocinadores/(\d+)/editar/$"):
            f = self.form(page)
            pk = int(m.group(1))
            obj = Profile.objects.filter(pk=pk).first() or Profile(pk=pk)
            obj.organization = org
            for name in ("name", "website", "instagram_url", "whatsapp_number", "contact_name",
                         "contact_phone", "contact_email", "notes"):
                if name in f:
                    setattr(obj, name, f[name] or "")
            logo = self.file_input(details, url, "logo_image")
            if logo:
                obj.logo_image = logo
            if pk in archived:
                obj.archived_at = obj.archived_at or timezone.now()
            self.raw_save(obj)

        # Patrocínios do evento: acordo, marca vinculada e logo
        Sponsor = apps.get_model("events", "Sponsor")
        for url, page, m in matching(r"^/gestao/eventos/([0-9a-f-]{36})/patrocinadores/(\d+)/editar/$"):
            f = self.form(page)
            sponsor = Sponsor.objects.filter(pk=int(m.group(2))).first()
            if not sponsor:
                self.warn(f"Patrocínio {m.group(2)} não existe no admin; ignorado.")
                continue
            sponsor.agreed_amount_cents = self.brl_cents(f.get("agreement-amount")) or 0
            due = f.get("agreement-due_at")
            sponsor.due_at = datetime.strptime(due, "%Y-%m-%d").date() if due else None
            sponsor.agreement_notes = f.get("agreement-agreement_notes", "")
            for name in ("instagram_url", "whatsapp_number", "website"):
                if name in f:
                    setattr(sponsor, name, f[name] or "")
            logo = self.file_input(details, url, "logo_image")
            if logo:
                sponsor.logo_image = logo
            detail = urlsplit(url).path.rsplit("editar/", 1)[0]
            for _, link in (details.get(detail) or {}).get("links", []):
                pm = re.match(r"^/gestao/patrocinadores/(\d+)/editar/$", link or "")
                if pm and Profile.objects.filter(pk=int(pm.group(1))).exists():
                    sponsor.profile_id = int(pm.group(1))
            if not sponsor.profile_id:
                sponsor.profile = Profile.objects.filter(name__iexact=sponsor.name).first()
            self.raw_save(sponsor)

        # Recebimentos de patrocínio (casados com os lançamentos do extrato)
        Entry = apps.get_model("finance", "FinancialEntry")
        Payment = apps.get_model("finance", "SponsorPayment")
        method_field = Payment._meta.get_field("method")
        seen_payments = set()
        for url, page, m in matching(r"/patrocinadores/(\d+)/recebimentos/([0-9a-f-]{36})/editar/$"):
            f = self.form(page)
            pid = m.group(2)
            entry = Entry.objects.filter(source_reference=f"SPONSOR-{pid}").first()
            if not entry:
                self.warn(f"Recebimento {pid} sem lançamento no extrato; ignorado.")
                continue
            self.save_payment(Payment, entry, int(m.group(1)), pid,
                              amount=self.brl_cents(f.get("amount")), received_on=f.get("received_on"),
                              method=f.get("method"), reference=f.get("reference", ""), notes=f.get("notes", ""),
                              key=f.get("idempotency_key"))
            seen_payments.add(pid)
        for entry in Entry.objects.filter(source_reference__regex=r"^SPONSOR-[0-9a-f-]{36}$"):
            pid = entry.source_reference.split("-", 1)[1]
            if pid in seen_payments:
                continue
            name = entry.description.split(":", 1)[-1].strip()
            sponsor = Sponsor.objects.filter(event_id=entry.event_id, name__iexact=name).first()
            if not sponsor:
                self.warn(f"Recebimento {pid} ({entry.description}) sem patrocinador; ignorado.")
                continue
            fallback = "OTHER" if "OTHER" in dict(method_field.choices or []) else method_field.default
            self.save_payment(Payment, entry, sponsor.pk, pid, amount=entry.amount_cents_signed,
                              received_on=timezone.localtime(entry.recognized_at).date().isoformat(),
                              method=fallback, reference="", notes="Recuperado do extrato (forma de recebimento não registrada no backup).",
                              key=None)
            self.warn(f"Recebimento {pid} ({entry.description}) recuperado só pelo extrato: confira a forma de pagamento.")

        # Interessados em patrocinar
        Lead = apps.get_model("events", "SponsorLead")
        for url, page, m in matching(r"^/gestao/eventos/([0-9a-f-]{36})/patrocinadores/interessados/(\d+)/editar/$"):
            f = self.form(page)
            obj = Lead(pk=int(m.group(2)), event_id=m.group(1))
            for name in ("name", "contact_name", "phone", "address", "notes", "previous_edition", "previous_notes", "status"):
                if name in f:
                    setattr(obj, name, f[name] or "")
            obj.previous_amount_cents = self.brl_cents(f.get("previous_amount")) if f.get("previous_amount") else None
            fu = f.get("follow_up_at")
            obj.follow_up_at = datetime.strptime(fu[:10], "%Y-%m-%d").date() if fu else None
            if isinstance(Lead._meta.get_field("follow_up_at"), models.DateTimeField) and obj.follow_up_at:
                obj.follow_up_at = timezone.make_aware(datetime.combine(obj.follow_up_at, datetime.min.time()), self.tz())
            obj.revision = int(f.get("revision") or 1)
            obj.name_key = self.name_key(Lead, obj)
            self.raw_save(obj)

        # Produtos extras (adicionais)
        AddOn = apps.get_model("catalog", "AddOn")
        for url, page, m in matching(r"^/gestao/eventos/([0-9a-f-]{36})/catalogo/adicional/([0-9a-f-]{36})/editar/$"):
            f = self.form(page)
            obj = AddOn(pk=m.group(2), event_id=m.group(1), title=f.get("title", ""), description=f.get("description", ""),
                        variant_code=f.get("variant_code", ""), price_cents=self.brl_cents(f.get("price")) or 0,
                        max_quantity=int(f.get("max_quantity") or 0), active=bool(f.get("active")))
            img = self.file_input(details, url, "image")
            if img:
                obj.image = img
            self.raw_save(obj)

        # Movimentações manuais
        Movement = apps.get_model("finance", "ManualMovement")
        for url, page, m in matching(r"^/gestao/eventos/([0-9a-f-]{36})/financeiro/movimentacoes/([0-9a-f-]{36})/editar/$"):
            f = self.form(page)
            entry = Entry.objects.filter(movement_id=m.group(2)).order_by("created_at").first()
            amount = f.get("amount_cents") or ""
            cents = int(amount) if str(amount).isdigit() else self.brl_cents(amount)
            obj = Movement(pk=m.group(2), event_id=m.group(1), kind=f.get("kind", ""), description=f.get("description", ""),
                           amount_cents=cents or 0, occurred_on=f.get("occurred_on") or None, method=f.get("method", ""),
                           reference=f.get("reference", ""), notes=f.get("notes", ""), revision=int(f.get("revision") or 1),
                           request_key=secrets.token_hex(16), submission_hash="", proof_asset="",
                           created_by_id=entry.posted_by_id if entry else None,
                           created_at=entry.created_at if entry else None)
            self.raw_save(obj)

        # Comprovantes privados das despesas
        Expense = apps.get_model("finance", "Expense")
        storage = Expense._meta.get_field("proof_asset").storage
        for doc in sorted((backup / "arquivos").glob("gestao/eventos/*/documentos/despesa/*")):
            expense = Expense.objects.filter(pk=doc.name).first()
            if not expense or not doc.is_file():
                continue
            data = doc.read_bytes()
            ext = ".pdf" if data[:4] == b"%PDF" else ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if data[:3] == b"\xff\xd8\xff" else ".webp" if data[8:12] == b"WEBP" else ""
            expense.proof_asset = storage.save(f"expenses/receipts/{doc.name}{ext}", ContentFile(data))
            self.raw_save(expense)

        # Política de taxas do Mercado Pago
        fees_page = next((p for u, p, _ in matching(r"^/gestao/integracoes/mercado-pago/taxas/$")), None)
        if fees_page:
            self.import_fee_policy(self.form(fees_page))

    def save_payment(self, Payment, entry, sponsor_id, pid, *, amount, received_on, method, reference, notes, key):
        Entry = type(entry)
        reversal = Entry.objects.filter(source_reference=f"SPONSOR-REVERSAL-{pid}").first()
        obj = Payment(pk=pid, sponsorship_id=sponsor_id, amount_cents=amount, received_on=received_on,
                      method=method, reference=reference or "", notes=notes or "", created_by_id=entry.posted_by_id,
                      created_at=entry.created_at, idempotency_key=key or pid, submission_hash="",
                      ledger_entry_id=entry.pk)
        if reversal:
            obj.reversed_at = reversal.created_at
            obj.reversed_by_id = reversal.posted_by_id
            obj.reversal_entry_id = reversal.pk
            obj.reversal_reason = reversal.description
        self.raw_save(obj)

    def name_key(self, Model, obj):
        for attr in ("build_name_key", "make_name_key", "normalize_name"):
            fn = getattr(Model, attr, None)
            if callable(fn):
                try:
                    return fn(obj.name)
                except TypeError:
                    pass
        import unicodedata
        text = unicodedata.normalize("NFKD", obj.name or "").encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    def file_input(self, details, url, name):
        for fi in (details.get(urlsplit(url).path) or details.get(url) or {}).get("fileInputs", []):
            if fi.get("name") == name and fi.get("href"):
                return self.media_path(fi["href"])
        return ""

    def import_fee_policy(self, f):
        Policy = apps.get_model("payments", "PaymentFeePolicy")
        obj = Policy.objects.first() or Policy()
        fields = {fl.name: fl for fl in Policy._meta.concrete_fields}
        applied = []
        for key, raw in f.items():
            if not key.startswith("fees-"):
                continue
            name = key[5:]
            if name in fields and not fields[name].primary_key:
                try:
                    setattr(obj, fields[name].attname, self.convert(fields[name], raw, {}))
                    applied.append(name)
                except Exception:
                    pass
        # Campos em % e R$ na tela viram pontos-base e centavos no modelo (mesma regra de apps/payments/fees.py).
        def num(key):
            raw = f.get(f"fees-{key}")
            return None if raw in (None, "") else Decimal(str(raw).replace(",", "."))
        for target, source in [("pix_bps", "pix_percent"), ("card_bps", "card_percent"), ("pix_fixed_cents", "pix_fixed"),
                               ("card_fixed_cents", "card_fixed"), ("commission_bps", "commission_percent"),
                               ("commission_fixed_cents", "commission_fixed")]:
            if num(source) is not None:
                setattr(obj, target, int(num(source) * 100))
                applied.append(source)
        obj.installment_rates = {str(n): int(num(f"installment_{n}") * 100) for n in range(2, 13) if num(f"installment_{n}") is not None}
        if applied:
            self.fill_required(obj)
            obj.save()
            self.count["payments.PaymentFeePolicy"] = 1

    # ------------------------------------------------------------------ main
    def copy_media(self, backup):
        source = backup / "arquivos" / "media"
        if not source.is_dir():
            return 0
        target = Path(settings.MEDIA_ROOT)
        copied = 0
        for path in source.rglob("*"):
            if path.is_file():
                dest = target / path.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                copied += 1
        return copied

    def handle(self, backup_dir, dry_run=False, **options):
        backup = Path(backup_dir)
        dump = json.loads((backup / "admin_dump.json").read_text(encoding="utf-8"))
        pages = json.loads((backup / "gestao_paginas.json").read_text(encoding="utf-8"))["pages"]
        details_file = backup / "gestao_detalhes.json"
        details = json.loads(details_file.read_text(encoding="utf-8")) if details_file.exists() else {}
        self.count, self.warnings = {}, []

        with transaction.atomic():
            with connection.constraint_checks_disabled():
                self.import_admin(dump)
                self.import_gestao(pages, details, backup)
            connection.check_constraints()
            if dry_run:
                self.report()
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Simulação: nada foi gravado."))
                return
        copied = self.copy_media(backup)
        self.report()
        self.stdout.write(self.style.SUCCESS(f"Importação concluída. Arquivos de mídia copiados: {copied}."))

    def report(self):
        for label, n in sorted(self.count.items()):
            self.stdout.write(f"  {label}: {n}")
        for w in self.warnings:
            self.stdout.write(self.style.WARNING("  ! " + w))
