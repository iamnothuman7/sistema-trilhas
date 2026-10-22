from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance", "0002_initial")]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="idempotency_key",
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True, verbose_name="Identificação do lançamento"),
        ),
        migrations.AddField(
            model_name="expense",
            name="submission_hash",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AlterField(
            model_name="expense",
            name="status",
            field=models.CharField(choices=[("PENDING", "A pagar"), ("PAID", "Paga"), ("CANCELED", "Cancelada")], default="PAID", max_length=20, verbose_name="Situação"),
        ),
    ]
