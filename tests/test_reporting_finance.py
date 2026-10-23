import csv
import io
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product
from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import AuditLog, Organization
from apps.events.models import Event
from apps.finance.forms import ExpenseForm, parse_amount_cents
from apps.finance.models import Expense, ExpenseCategory, ExpenseStatus, FinancialEntry, FinancialEntryKind, Vendor
from apps.finance.services import cancel_pending_expense, create_expense, settle_expense
from apps.identity.models import EventMembership, Person, RoleChoices, User
from apps.registrations.models import CommercialState, OperationalState, Registration
from apps.reporting.exports import decimal_amount, export_financial_csv
from apps.reporting.selectors import get_event_kpi_summary, get_event_report, get_financial_summary


class ReportingFinanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Organização de teste")
        cls.other_org = Organization.objects.create(name="Outra organização")
        now = timezone.now()
        attrs = dict(starts_at=now + timedelta(days=10), ends_at=now + timedelta(days=11),
                     registration_opens_at=now - timedelta(days=10), registration_closes_at=now + timedelta(days=9))
        cls.event = Event.objects.create(organization=cls.org, title="Edição teste", slug="edicao-teste-finance", **attrs)
        cls.other_event = Event.objects.create(organization=cls.other_org, title="Outra edição", slug="outra-edicao-finance", **attrs)
        cls.user = User.objects.create_user(email="finance@example.test", full_name="Tesouraria")
        cls.membership = EventMembership.objects.create(user=cls.user, event=cls.event, role_code=RoleChoices.FINANCE)
        cls.vendor = Vendor.objects.create(organization=cls.org, name="Fornecedor da edição")
        cls.other_vendor = Vendor.objects.create(organization=cls.other_org, name="Fornecedor externo")
        cls.product = Product.objects.create(event=cls.event, name="Piloto")

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def expense_payload(self, **overrides):
        data = dict(description="Alimentação da equipe", category=ExpenseCategory.ALIMENTACAO,
                    vendor_id=str(self.vendor.pk), amount="0,29", due_at=self.today.isoformat(),
                    paid_at=self.today.isoformat(), status=ExpenseStatus.PAID, idempotency_key=str(uuid.uuid4()))
        data.update(overrides)
        return data

    def create(self, **overrides):
        form = ExpenseForm(self.expense_payload(**overrides), event=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        return create_expense(event=self.event, data=form.cleaned_data, actor=self.user)[0]

    def entry(self, kind, amount, **overrides):
        data = dict(event=self.event, kind=kind, amount_cents_signed=amount,
                    source_reference=str(uuid.uuid4()), description="Movimento de teste")
        data.update(overrides)
        return FinancialEntry.objects.create(**data)

    def order(self, **overrides):
        data = dict(event=self.event, merchant_reference=str(uuid.uuid4()), purchaser_name="Piloto de teste",
                    purchaser_email="piloto@example.test", status=OrderStatus.PAID, total_cents=10000,
                    gross_cents=10000, expires_at=timezone.now() + timedelta(days=1))
        data.update(overrides)
        return Order.objects.create(**data)

    def registration(self, **overrides):
        order = self.order()
        line = OrderLine.objects.create(order=order, product=self.product, intended_person_name="Piloto",
                                        unit_cents=10000, net_cents=10000)
        person = Person.objects.create(full_name="Piloto de teste")
        data = dict(event=self.event, person=person, source_order_line=line,
                    commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.READY)
        data.update(overrides)
        return Registration.objects.create(**data)

    def test_money_parsing_is_exact_and_accepts_brazilian_and_decimal_input(self):
        for value, cents in [("0,29", 29), ("0.29", 29), ("R$ 1.234,56", 123456), ("1.234", 123400), ("5", 500)]:
            with self.subTest(value=value):
                self.assertEqual(parse_amount_cents(value), cents)
        self.assertEqual(decimal_amount(9223372036854775807), "92233720368547758,07")

    def test_money_rejects_invalid_zero_negative_and_fractional_cents(self):
        for value in ["", "0", "-10", "NaN", "inf", "1,234", "2.3456", "1e5", "9999999999999999999999"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_amount_cents(value)

    def test_expense_form_restricts_vendor_and_validates_choices_and_dates(self):
        for changes, field in [({"vendor_id": self.other_vendor.pk}, "vendor_id"),
                               ({"category": "UNKNOWN"}, "category"), ({"status": "CANCELED"}, "status"),
                               ({"due_at": "invalid"}, "due_at"),
                               ({"paid_at": (self.today + timedelta(days=1)).isoformat()}, "paid_at")]:
            form = ExpenseForm(self.expense_payload(**changes), event=self.event)
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)

    def test_service_also_rejects_vendor_from_another_organization(self):
        form = ExpenseForm(self.expense_payload(), event=self.event)
        self.assertTrue(form.is_valid())
        form.cleaned_data["vendor_id"] = self.other_vendor
        with self.assertRaises(ValidationError):
            create_expense(event=self.event, data=form.cleaned_data, actor=self.user)
        self.assertFalse(Expense.objects.exists())

    def test_paid_expense_posts_exact_amount_and_audit_once(self):
        form = ExpenseForm(self.expense_payload(), event=self.event)
        self.assertTrue(form.is_valid())
        expense, created = create_expense(event=self.event, data=form.cleaned_data, actor=self.user)
        duplicate, repeated = create_expense(event=self.event, data=form.cleaned_data, actor=self.user)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(expense.pk, duplicate.pk)
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(FinancialEntry.objects.get().amount_cents_signed, -29)
        self.assertEqual(AuditLog.objects.filter(action="EXPENSE_CREATED").count(), 1)

    def test_idempotency_key_cannot_be_reused_with_changed_payload(self):
        form = ExpenseForm(self.expense_payload(), event=self.event)
        self.assertTrue(form.is_valid())
        create_expense(event=self.event, data=form.cleaned_data, actor=self.user)
        form.cleaned_data["amount"] = 200
        with self.assertRaises(ValidationError):
            create_expense(event=self.event, data=form.cleaned_data, actor=self.user)
        self.assertEqual(Expense.objects.count(), 1)

    def test_expense_and_ledger_roll_back_if_audit_fails(self):
        with patch("apps.finance.services.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(Expense.objects.exists())
        self.assertFalse(FinancialEntry.objects.exists())

    def test_pending_expense_has_no_ledger_and_settlement_is_idempotent(self):
        expense = self.create(status=ExpenseStatus.PENDING, amount="150,25")
        self.assertIsNone(expense.paid_at)
        self.assertFalse(FinancialEntry.objects.exists())
        self.assertEqual(get_financial_summary(self.event)["pending_expenses_cents"], 15025)
        self.assertEqual(get_financial_summary(self.event)["net_cents"], 0)
        first, created = settle_expense(event=self.event, expense_id=expense.pk, actor=self.user, paid_at=self.today)
        _, repeated = settle_expense(event=self.event, expense_id=expense.pk, actor=self.user, paid_at=self.today)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.status, ExpenseStatus.PAID)
        self.assertEqual(FinancialEntry.objects.count(), 1)
        self.assertEqual(get_financial_summary(self.event)["net_cents"], -15025)

    def test_settlement_rolls_back_on_ledger_error(self):
        expense = self.create(status=ExpenseStatus.PENDING)
        with patch("apps.finance.services._post_expense", side_effect=RuntimeError("ledger unavailable")):
            with self.assertRaises(RuntimeError):
                settle_expense(event=self.event, expense_id=expense.pk, actor=self.user, paid_at=self.today)
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PENDING)
        self.assertIsNone(expense.paid_at)

    def test_cancel_pending_preserves_history_without_posting_ledger(self):
        expense = self.create(status=ExpenseStatus.PENDING)
        cancel_pending_expense(event=self.event, expense_id=expense.pk, actor=self.user, reason="Serviço não contratado")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.CANCELED)
        self.assertFalse(FinancialEntry.objects.exists())
        self.assertEqual(AuditLog.objects.get(action="EXPENSE_CANCELED").reason, "Serviço não contratado")

    def test_paid_expense_cannot_be_canceled_and_future_settlement_rejected(self):
        paid = self.create()
        with self.assertRaises(ValidationError):
            cancel_pending_expense(event=self.event, expense_id=paid.pk, actor=self.user, reason="Teste")
        pending = self.create(status=ExpenseStatus.PENDING)
        with self.assertRaises(ValidationError):
            settle_expense(event=self.event, expense_id=pending.pk, actor=self.user, paid_at=self.today + timedelta(days=1))

    def test_cash_result_uses_ledger_including_refunds_and_excludes_transfers(self):
        self.entry(FinancialEntryKind.RECEITA_INSCRICAO, 10000)
        self.entry(FinancialEntryKind.REEMBOLSO, -2000)
        self.entry(FinancialEntryKind.TAXA_MERCADO_PAGO, -350)
        self.entry(FinancialEntryKind.TRANSFERENCIA_BANCO, -5000)
        self.entry(FinancialEntryKind.RECEITA_INSCRICAO, 50000, event=self.other_event)
        self.create(amount="15,00")
        finance = get_financial_summary(self.event)
        self.assertEqual(finance["net_cents"], 6150)
        self.assertEqual(finance["paid_expenses_cents"], 1500)
        self.assertEqual(finance["expense_reconciliation_delta_cents"], 0)
        self.assertEqual(get_event_kpi_summary(self.event)["net_profit_cents"], 6150)
        self.assertEqual(get_event_report(self.event)["daily"][0]["net_cents"], 6150)

    def test_financial_date_filter_includes_local_day_boundaries(self):
        first = timezone.make_aware(datetime.combine(self.today, datetime.min.time()))
        last = first + timedelta(days=1) - timedelta(microseconds=1)
        for when, value in [(first - timedelta(microseconds=1), 10000), (first, 100), (last, 200), (last + timedelta(microseconds=1), 10000)]:
            self.entry(FinancialEntryKind.RECEITA_MANUAL, value, recognized_at=when)
        self.assertEqual(get_financial_summary(self.event, self.today, self.today)["revenue_cents"], 300)

    def test_report_counts_creation_period_and_current_registration_states(self):
        registration = self.registration(operational_state=OperationalState.CHECKED_IN, shirt_size="G")
        old = self.registration(commercial_state=CommercialState.CANCELED)
        Registration.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        self.order(event=self.other_event)
        report = get_event_report(self.event, self.today, self.today)
        self.assertEqual(report["registrations"], dict(total=1, active=1, checked_in=1, awaiting_terms=0))
        self.assertEqual(report["shirts"], [{"shirt_size": "G", "count": 1}])
        self.assertEqual(report["orders_count"], 2)

    def test_dashboard_kpis_exclude_inactive_registrations_and_expired_receivables(self):
        self.registration(operational_state=OperationalState.READY)
        self.registration(operational_state=OperationalState.CHECKED_IN)
        canceled_ready = self.registration(commercial_state=CommercialState.CANCELED, operational_state=OperationalState.READY)
        canceled_checked_in = self.registration(commercial_state=CommercialState.CANCELED, operational_state=OperationalState.CHECKED_IN)
        pending = self.order(status=OrderStatus.AWAITING_PAYMENT, total_cents=12000)
        self.order(status=OrderStatus.RESERVED, total_cents=6000)
        for status in (OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT):
            self.order(status=status, total_cents=50000, expires_at=timezone.now()-timedelta(seconds=1))
        self.order(event=self.other_event, status=OrderStatus.AWAITING_PAYMENT, total_cents=99000)
        summary = get_event_kpi_summary(self.event)
        self.assertEqual(summary["ready_for_checkin"], 1)
        self.assertEqual(summary["checked_in_count"], 1)
        self.assertEqual(summary["pending_to_receive_cents"], 18000)
        canceled_ready.refresh_from_db()
        canceled_checked_in.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(canceled_ready.operational_state, OperationalState.READY)
        self.assertEqual(canceled_checked_in.operational_state, OperationalState.CHECKED_IN)
        self.assertEqual(pending.status, OrderStatus.AWAITING_PAYMENT)

    def test_team_and_city_groups_use_only_active_registrations_in_event_and_local_period(self):
        from datetime import timezone as dt_timezone
        self.event.timezone = "America/Manaus"
        self.event.save(update_fields=["timezone"])
        first = datetime(2027, 4, 18, 4, tzinfo=dt_timezone.utc)
        last = first + timedelta(days=1) - timedelta(microseconds=1)

        def add(team="Trilha do Sol", city="Ubajara", state="CE", when=first, **overrides):
            registration = self.registration(team_name=team, **overrides)
            Person.objects.filter(pk=registration.person_id).update(city=city, state=state)
            Registration.objects.filter(pk=registration.pk).update(created_at=when)
            return registration

        add(team="  Trilha do Sol  ", city=" Ubajara ")
        add(when=first + timedelta(hours=1))
        add(when=last)
        add(team="Serra", city="Tianguá")
        add(team="Serra", state="PI")
        add(commercial_state=CommercialState.CANCELED)
        add(commercial_state=CommercialState.RESERVED)
        add(event=self.other_event)
        add(when=first - timedelta(microseconds=1))
        add(when=last + timedelta(microseconds=1))
        for missing in (None, "", "   ", "Não informado", " NÃO INFORMADO ", "Nãoinformado", "Não informada"):
            add(team=missing, city=missing or "")

        query = {"date_start": "2027-04-18", "date_end": "2027-04-18"}
        response = self.client.get(reverse("gestao:reports", args=[self.event.pk]), query)
        self.assertEqual(response.status_code, 200)
        report = response.context["report"]
        self.assertEqual(report["teams"], [{"name": "Trilha do Sol", "count": 3}, {"name": "Serra", "count": 2}])
        self.assertEqual(report["cities"], [
            {"name": "Ubajara", "state": "CE", "count": 3},
            {"name": "Tianguá", "state": "CE", "count": 1},
            {"name": "Ubajara", "state": "PI", "count": 1},
        ])
        self.assertContains(response, "Inscrições por equipe")
        self.assertContains(response, "Ubajara / PI")
        self.assertNotContains(response, "Piloto de teste")
        self.assertNotContains(response, "piloto@example.test")

    def test_team_and_city_tables_explain_empty_period(self):
        response = self.client.get(reverse("gestao:reports", args=[self.event.pk]))
        self.assertEqual(response.context["report"]["teams"], [])
        self.assertEqual(response.context["report"]["cities"], [])
        self.assertContains(response, "Nenhuma equipe informada nas inscrições ativas do período.")
        self.assertContains(response, "Nenhuma cidade informada nas inscrições ativas do período.")

    def test_csv_contains_numeric_signed_money_and_escapes_text_formulas(self):
        self.entry(FinancialEntryKind.DESPESA, -29, description="  =1+1", source_reference="@SUM(A1)")
        self.entry(FinancialEntryKind.RECEITA_MANUAL, 9999, event=self.other_event)
        response = export_financial_csv(self.event)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2], "-0,29")
        self.assertEqual(rows[1][3], "'@SUM(A1)")
        self.assertEqual(rows[1][4], "'  =1+1")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_finance_membership_can_export_finance_without_participant_access(self):
        self.registration()
        self.create()
        url = reverse("gestao:reports", args=[self.event.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relatórios da edição")
        self.assertNotContains(response, "Participantes CSV")
        self.assertEqual(self.client.get(url, {"export": "participants"}).status_code, 403)
        for kind in ["financial", "orders", "summary"]:
            response = self.client.get(url, {"export": kind})
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/csv", response["Content-Type"])
        self.assertEqual(AuditLog.objects.filter(action="EXPORT_EVENT_REPORT").count(), 3)
        EventMembership.objects.create(user=self.user, event=self.event, role_code=RoleChoices.REGISTRATION_AGENT)
        self.assertContains(self.client.get(url), "Participantes CSV")
        self.assertEqual(self.client.get(url, {"export": "participants"}).status_code, 200)
        self.assertEqual(AuditLog.objects.filter(action="EXPORT_EVENT_REPORT").count(), 4)

    def test_reports_and_exports_use_event_timezone_at_day_boundary(self):
        from datetime import timezone as dt_timezone
        self.event.timezone = "America/Manaus"
        self.event.save(update_fields=["timezone"])
        when = datetime(2027, 4, 18, 3, 30, tzinfo=dt_timezone.utc)
        self.entry(FinancialEntryKind.RECEITA_MANUAL, 12000, recognized_at=when)
        day = when.date() - timedelta(days=1)
        report = get_event_report(self.event, day, day)
        self.assertEqual(report["finance"]["revenue_cents"], 12000)
        self.assertEqual(report["daily"][0]["day"], day)
        self.assertEqual(get_event_report(self.event, when.date(), when.date())["daily"], [])
        response = export_financial_csv(self.event, day, day)
        self.assertIn("17/04/2027 23:30", response.content.decode())

    def test_reports_reject_other_event_and_checkin_role(self):
        self.assertEqual(self.client.get(reverse("gestao:reports", args=[self.other_event.pk])).status_code, 403)
        EventMembership.objects.filter(pk=self.membership.pk).update(role_code=RoleChoices.CHECKIN_AGENT)
        self.assertEqual(self.client.get(reverse("gestao:reports", args=[self.event.pk])).status_code, 403)

    def test_reports_reject_invalid_dates_and_unknown_export(self):
        url = reverse("gestao:reports", args=[self.event.pk])
        for query in [{"date_start": "invalid"}, {"date_start": "2026-10-23", "date_end": "2026-10-22"}, {"export": "unknown"}]:
            self.assertEqual(self.client.get(url, query).status_code, 400)

    def test_report_finance_and_exports_reject_dates_outside_supported_years(self):
        report_url = reverse("gestao:reports", args=[self.event.pk])
        routes = [(report_url, {}), (report_url, {"export": "financial"}),
                  (report_url, {"export": "orders"}), (report_url, {"export": "summary"}),
                  (reverse("gestao:finance", args=[self.event.pk]), {}),
                  (reverse("gestao:export_financial", args=[self.event.pk]), {})]
        for invalid in ({"date_start": "0001-01-01"}, {"date_end": "9999-12-31"},
                        {"date_start": "1899-12-31"}, {"date_end": "2200-01-01"}):
            for url, options in routes:
                with self.subTest(query=invalid, route=url, options=options):
                    response = self.client.get(url, invalid | options)
                    self.assertEqual(response.status_code, 400)
        response = self.client.get(report_url, {"date_end": "9999-12-31"})
        self.assertContains(response, "Informe uma data entre 01/01/1900 e 31/12/2199.", status_code=400)
        self.assertIsNone(response.context["report"])
        self.assertFalse(AuditLog.objects.filter(action="EXPORT_EVENT_REPORT").exists())
        valid = {"date_start": "1900-01-01", "date_end": "2199-12-31"}
        for url, options in routes:
            with self.subTest(valid_route=url, options=options):
                self.assertEqual(self.client.get(url, valid | options).status_code, 200)

    def test_finance_form_preserves_invalid_values_and_rejects_get_mutation(self):
        url = reverse("gestao:add_expense", args=[self.event.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, self.expense_payload(amount="-1"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["expense_form"]["description"].value(), "Alimentação da equipe")
        self.assertFalse(Expense.objects.exists())

    def test_finance_post_is_event_scoped_and_repeated_submission_is_safe(self):
        payload = self.expense_payload()
        url = reverse("gestao:add_expense", args=[self.event.pk])
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(FinancialEntry.objects.count(), 1)
        self.assertEqual(self.client.post(reverse("gestao:add_expense", args=[self.other_event.pk]), payload).status_code, 403)

    def test_expense_payment_endpoint_rejects_get_and_foreign_expense(self):
        expense = self.create(status=ExpenseStatus.PENDING)
        pay_url = reverse("gestao:pay_expense", args=[self.event.pk, expense.pk])
        self.assertEqual(self.client.get(pay_url).status_code, 405)
        self.assertEqual(self.client.post(pay_url).status_code, 302)
        self.assertEqual(self.client.post(pay_url).status_code, 302)
        self.assertEqual(FinancialEntry.objects.count(), 1)
        Expense.objects.filter(pk=expense.pk).update(event=self.other_event)
        self.assertEqual(self.client.post(pay_url).status_code, 404)

    def test_finance_filters_and_export_use_same_period(self):
        self.entry(FinancialEntryKind.RECEITA_MANUAL, 29)
        self.entry(FinancialEntryKind.RECEITA_MANUAL, 9000, recognized_at=timezone.now() - timedelta(days=3))
        query = {"date_start": self.today.isoformat(), "date_end": self.today.isoformat()}
        response = self.client.get(reverse("gestao:finance", args=[self.event.pk]), query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["finance"]["revenue_cents"], 29)
        exported = self.client.get(reverse("gestao:export_financial", args=[self.event.pk]), query)
        self.assertEqual(exported.status_code, 200)
        self.assertIn("0,29", exported.content.decode())
        self.assertNotIn("90,00", exported.content.decode())

    def test_finance_pagination_renders_only_selected_rows_for_both_tables(self):
        for index in range(21):
            Expense.objects.create(event=self.event, description=f"compromisso-{index:02d}-teste", amount_cents=100,
                                   due_at=self.today - timedelta(days=index), status=ExpenseStatus.PENDING)
        for index in range(31):
            self.entry(FinancialEntryKind.RECEITA_MANUAL, 100, description=f"movimento-{index:02d}-teste",
                       recognized_at=timezone.now() - timedelta(days=index))
        url = reverse("gestao:finance", args=[self.event.pk])
        first = self.client.get(url)
        second = self.client.get(url, {"expenses_page": 2, "entries_page": 2})
        self.assertEqual(len(first.context["expenses"]), 20)
        self.assertEqual(len(first.context["entries"]), 30)
        self.assertEqual(len(second.context["expenses"]), 1)
        self.assertEqual(len(second.context["entries"]), 1)
        self.assertNotContains(first, "compromisso-20-teste")
        self.assertNotContains(first, "movimento-30-teste")
        self.assertContains(second, "compromisso-20-teste")
        self.assertContains(second, "movimento-30-teste")
        self.assertNotContains(second, "compromisso-00-teste")
        self.assertNotContains(second, "movimento-00-teste")
