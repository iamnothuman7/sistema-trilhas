"""Explicit, verified migration of legacy financial files out of public media."""
import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.private_storage import private_document_storage
from apps.finance.models import Expense
from apps.payments.models import ManualPaymentEvidence


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


class Command(BaseCommand):
    help = "Confere comprovantes antigos; --apply copia, verifica SHA-256 e remove somente a cópia pública."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Executa a migração; sem esta opção, apenas confere.")

    def handle(self, *args, **options):
        public_root = Path(settings.MEDIA_ROOT).resolve()
        private_root = Path(private_document_storage.location)
        names = set()
        for model in (Expense, ManualPaymentEvidence):
            names.update(model.objects.exclude(proof_asset="").exclude(proof_asset__isnull=True)
                         .values_list("proof_asset", flat=True))
        pending, protected, missing = 0, 0, 0
        for name in sorted(names):
            normalized = PurePosixPath(name.replace("\\", "/"))
            if (normalized.is_absolute() or ".." in normalized.parts
                    or not str(normalized).startswith(("expenses/receipts/", "evidences/"))):
                raise CommandError("Referência de comprovante fora das pastas autorizadas; revise o cadastro antes de migrar.")
            source = public_root / normalized
            destination = Path(private_document_storage.path(str(normalized)))
            if not source.resolve().is_relative_to(public_root):
                raise CommandError("Arquivo legado aponta para fora de MEDIA_ROOT; nenhuma remoção foi feita neste arquivo.")
            if not source.is_file():
                if destination.is_file():
                    protected += 1
                else:
                    missing += 1
                continue
            original_digest = digest(source)
            if destination.exists() and (not destination.is_file() or digest(destination) != original_digest):
                raise CommandError("Conflito entre cópia pública e privada; os dois arquivos foram preservados.")
            pending += 1
            if not options["apply"]:
                continue
            if not destination.exists():
                destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                for folder in (destination.parent, *destination.parent.parents):
                    if folder.is_relative_to(private_root):
                        os.chmod(folder, 0o700)
                try:
                    with destination.open("xb") as target, source.open("rb") as original:
                        shutil.copyfileobj(original, target)
                        target.flush()
                        os.fsync(target.fileno())
                    os.chmod(destination, 0o600)
                except Exception:
                    # A concurrent invocation may have created it: do not delete an
                    # existing file here. The next run verifies any partial copy.
                    raise CommandError("Falha ao copiar comprovante; a origem pública foi preservada.") from None
            if digest(destination) != original_digest or digest(source) != original_digest:
                raise CommandError("O conteúdo mudou durante a migração; a origem pública foi preservada.")
            os.chmod(destination, 0o600)
            source.unlink()
        verb = "Migrados" if options["apply"] else "A migrar (simulação)"
        self.stdout.write(f"{verb}: {pending}. Já privados: {protected}. Arquivos ausentes: {missing}.")
        if missing:
            raise CommandError("Há referências sem arquivo; recupere-as do backup antes de considerar a migração completa.")
