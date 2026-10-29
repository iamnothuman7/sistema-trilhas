from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from apps.finance.test_corrections import correct_identified_test_refund
from apps.reporting.selectors import get_event_kpi_summary,get_financial_summary
from apps.payments.services import reconcile_payment
from apps.payments.models import Refund
from tests import test_payments as fixtures
from tests import test_refund_channels as channels


@override_settings(**fixtures.MP_TEST_SETTINGS,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class IdentifiedTestCleanupTests(TestCase):
    setUp_payment=channels.RefundChannelTests.setUp_payment
    setUp=channels.RefundChannelTests.setUp
    payment=channels.RefundChannelTests.payment
    pay=channels.RefundChannelTests.pay
    ready=channels.RefundChannelTests.ready
    refund_data=channels.RefundChannelTests.refund_data
    legacy=channels.RefundChannelTests.legacy

    def test_void_fictitious_refund_preserves_real_money_and_never_resurrects_test(self):
        refund=self.legacy()
        data=dict(event=self.event,refund_id=refund.pk,charge_id=self.charge.pk,authenticated_payment=self.payment(),actor=self.owner,reason='Teste identificado pelo dono, sem devolução real.')
        self.assertTrue(correct_identified_test_refund(**data));self.assertFalse(correct_identified_test_refund(**data))
        summary=get_financial_summary(self.event)
        self.assertEqual(summary['refunds_cents'],0);self.assertEqual(summary['revenue_cents'],18001);self.assertEqual(summary['fees_cents'],180)
        self.order.refresh_from_db();self.assertTrue(self.order.is_test)
        kpi=get_event_kpi_summary(self.event);self.assertEqual((kpi['active_confirmed'],kpi['shirts_total'],kpi['shirts_delivered']),(0,0,0))
        reconcile_payment(self.payment(date_last_updated=(self.now+timedelta(minutes=1)).isoformat()))
        self.registration.refresh_from_db();self.assertIsNotNone(self.registration.archived_at);self.assertEqual(self.registration.commercial_state,'CANCELED')

    def test_real_refund_cannot_be_called_a_fictitious_note(self):
        refund=self.legacy()
        with self.assertRaises(ValidationError):correct_identified_test_refund(event=self.event,refund_id=refund.pk,charge_id=self.charge.pk,
            authenticated_payment=self.payment('refunded',transaction_amount_refunded=180.01),actor=self.owner,reason='Nunca deve remover devolução real.')
        refund.refresh_from_db();self.assertEqual(refund.state,'MANUAL_COMPLETED')
