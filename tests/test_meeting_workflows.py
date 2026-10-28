"""Meeting changes: money, scoped access, paid-only products and numbered tickets."""
import uuid
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.core.management import call_command
from io import StringIO
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.catalog.models import AddOn, CapacityResource
from apps.checkout.addons import reserve_addons, inventory_items
from apps.checkout.models import Order, OrderStatus
from apps.events.models import MotorcycleRaffle, SponsorTier
from apps.events.models_raffles import RaffleTicket, RaffleSalesPoint
from apps.events.raffle_operations import create_paper_batch, issue_registration_ticket, record_paper_ticket
from apps.finance.models import FinancialEntry
from apps.identity.models import User, UserPersonLink, EventMembership, RoleChoices
from apps.identity.onboarding import link_buyer_registration
from apps.payments.fees import gross_up, quote_for, submitted_quote
from apps.payments.models import PaymentFeePolicy, ChargeAttempt
from apps.payments.services import reconcile_payment
from apps.registrations.models import Entitlement
from apps.registrations.views_cleanup import archive_unpaid_order
from tests import test_payments as fixtures


class GrossUpTests(SimpleTestCase):
    def test_cent_rounding_preserves_net_and_uses_smallest_valid_total(self):
        for base in [1, 100, 12000, 39999, 10000000]:
            for rate in [0, 99, 498, 751, 2226]:
                for fixed in [0, 124]:
                    total = gross_up(base, rate, fixed)
                    net = lambda value: value - (value*rate+5000)//10000 - fixed
                    self.assertGreaterEqual(net(total), base)
                    self.assertLess(net(total-1), base)

    def test_standard_120_registration(self):
        self.assertEqual(gross_up(12000,99),12120)
        self.assertEqual(gross_up(12000,498),12629)


@override_settings(**fixtures.MP_TEST_SETTINGS)
class MeetingWorkflowTests(TestCase):
    setUp = fixtures.PaymentReconciliationTests.setUp
    payment = fixtures.PaymentReconciliationTests.payment

    def owner(self):
        user = User.objects.create_user(email='meeting-owner@example.test', full_name='Gestor')
        EventMembership.objects.create(user=user, role_code=RoleChoices.OWNER)
        self.client.force_login(user)
        return user

    def paid_buyer(self):
        self.event.status='PUBLISHED';self.event.save()
        buyer=User.objects.create_user(email='paid-extras@example.test', full_name='Piloto', password='local-test-password')
        self.order.purchaser_user=buyer;self.order.save()
        reconcile_payment(self.payment())
        self.order.refresh_from_db();self.registration.refresh_from_db()
        return buyer

    def test_method_specific_fee_and_optional_commission_never_routes_money(self):
        config=PaymentFeePolicy.objects.create(enabled=True,commission_requested=True,commission_bps=1000,
            commission_recipient='Plataforma',commission_account_id='7654321')
        quote=quote_for(self.order,'pix')
        self.assertEqual(quote.total,gross_up(18001,99))
        config.pix_pass_through=False;config.save()
        self.assertEqual(quote_for(self.order,'pix').total,18001)
        self.assertEqual(quote_for(self.order,'credit_card').total,gross_up(18001,498))
        self.charge.amount_cents=quote.total;self.charge.passed_fee_cents=quote.fee
        gateway=fixtures.MercadoPagoGateway()
        self.assertNotIn('application_fee',gateway._base_payload(self.charge))

    def test_signed_quote_cannot_be_changed_or_reused_for_other_order_or_method(self):
        PaymentFeePolicy.objects.create(enabled=True)
        quote=quote_for(self.order,'pix')
        self.assertEqual(submitted_quote(self.order,'pix',1,quote.token).total,quote.total)
        for method,token in [('credit_card',quote.token),('pix',quote.token+'x'),('pix','')]:
            with self.assertRaises(ValidationError): submitted_quote(self.order,method,1,token)
        other=SimpleNamespace(pk=uuid.uuid4(),base_total_cents=self.order.base_total_cents)
        with self.assertRaises(ValidationError): submitted_quote(other,'pix',1,quote.token)

    def test_existing_quote_is_honored_after_policy_changes(self):
        config=PaymentFeePolicy.objects.create(enabled=True)
        quote=quote_for(self.order,'pix');config.pix_bps=250;config.save()
        self.assertEqual(submitted_quote(self.order,'pix',1,quote.token).total,quote.total)
        self.assertNotEqual(quote_for(self.order,'pix').total,quote.total)

    def test_fee_approval_allocates_full_amount_and_does_not_duplicate(self):
        PaymentFeePolicy.objects.create(enabled=True)
        quote=quote_for(self.order,'pix')
        self.order.total_cents=quote.total;self.order.payment_fee_cents=quote.fee;self.order.save()
        self.charge.amount_cents=quote.total;self.charge.passed_fee_cents=quote.fee;self.charge.fee_snapshot=asdict(quote);self.charge.save()
        payment=self.payment(transaction_amount=quote.total/100,fee_details=[{'amount':quote.fee/100,'fee_payer':'collector'}])
        reconcile_payment(payment);reconcile_payment(payment)
        self.order.refresh_from_db()
        self.assertEqual(self.order.base_total_cents,18001)
        self.assertEqual(sum(self.charge.allocations.values_list('amount_cents',flat=True)),quote.total)
        self.assertEqual(sum(FinancialEntry.objects.values_list('amount_cents_signed',flat=True)),18001)

    def test_fee_tab_is_read_only_for_finance_and_private(self):
        self.owner();url=reverse('gestao:mercadopago_fees')
        response=self.client.get(url)
        self.assertContains(response,'Taxas e repasses');self.assertContains(response,'Inativa')
        self.assertIn('no-store',response['Cache-Control'])
        self.assertFalse(PaymentFeePolicy.objects.exists());self.http.assert_not_called()
        finance=User.objects.create_user(email='fee-finance@example.test')
        EventMembership.objects.create(user=finance,event=self.event,role_code=RoleChoices.FINANCE)
        self.client.force_login(finance)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{}).status_code,403)

    def test_fee_policy_edit_checks_revision_and_draft_never_activates_commission(self):
        self.owner();url=reverse('gestao:mercadopago_fees')
        data={'fees-version':0,'fees-enabled':'on','fees-pix_pass_through':'on','fees-card_pass_through':'on',
            'fees-pix_percent':'0.99','fees-pix_fixed':'0','fees-card_percent':'4.98','fees-card_fixed':'0','fees-max_installments':1,
            'fees-commission_requested':'on','fees-commission_percent':'2','fees-commission_fixed':'0','fees-commission_payer':'TEAM',
            'fees-commission_recipient':'Plataforma'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        config=PaymentFeePolicy.objects.get();self.assertEqual(config.commission_bps,200);self.assertEqual(config.revision,1)
        self.assertEqual(self.client.post(url,data).status_code,400)
        config.refresh_from_db();self.assertEqual(config.revision,1)
        self.http.assert_not_called()

    def test_addons_require_paid_registration_and_its_buyer(self):
        buyer=User.objects.create_user(email='not-paid@example.test')
        item=AddOn.objects.create(event=self.event,title='Copo',price_cents=1500,max_quantity=5)
        with self.assertRaises(ValidationError):
            reserve_addons(registration_id=self.registration.pk,user=buyer,items={str(item.pk):1},request_key=str(uuid.uuid4()))

    def addon_order(self):
        buyer=self.paid_buyer()
        item=AddOn.objects.create(event=self.event,title='Camiseta',variant_code='G',price_cents=2500,max_quantity=2)
        key=str(uuid.uuid4())
        order=reserve_addons(registration_id=self.registration.pk,user=buyer,items={str(item.pk):2},request_key=key)
        return buyer,item,key,order

    def test_addon_stock_idempotency_and_no_grant_before_payment(self):
        buyer,item,key,order=self.addon_order()
        duplicate=reserve_addons(registration_id=self.registration.pk,user=buyer,items={str(item.pk):2},request_key=key)
        self.assertEqual(order.pk,duplicate.pk)
        self.assertEqual(order.total_cents,5000)
        self.assertEqual(inventory_items(self.event)[0].available_quantity,0)
        self.assertFalse(Entitlement.objects.filter(source_order_line__order=order).exists())
        with self.assertRaises(ValidationError):
            reserve_addons(registration_id=self.registration.pk,user=buyer,items={str(item.pk):1},request_key=str(uuid.uuid4()))

    def test_addon_approval_and_refund_share_original_registration(self):
        buyer,item,key,order=self.addon_order()
        charge=ChargeAttempt.objects.create(order=order,idempotency_key=str(uuid.uuid4()),payment_method='pix',amount_cents=5000)
        payment=self.payment(id=112233,transaction_amount=50,external_reference=order.merchant_reference,
            metadata={'order_id':str(order.pk),'charge_attempt_id':str(charge.pk)})
        reconcile_payment(payment);reconcile_payment(payment)
        entitlement=Entitlement.objects.get(source_order_line__order=order)
        self.assertEqual(entitlement.registration_id,self.registration.pk)
        self.assertEqual(entitlement.quantity_total,2)
        self.assertEqual(CapacityResource.objects.get(type='ADDON_TOTAL').committed_count,2)
        payment.update(status='refunded',transaction_amount_refunded=50,date_last_updated=(self.now+timedelta(seconds=1)).isoformat())
        reconcile_payment(payment);entitlement.refresh_from_db()
        self.assertEqual(entitlement.state,'REVOKED')
        self.registration.refresh_from_db();self.assertEqual(self.registration.commercial_state,'ACTIVE')

    def test_unpaid_archive_blocks_live_charge_then_releases_expired_capacity(self):
        actor=self.owner()
        with self.assertRaises(ValidationError):archive_unpaid_order(self.order.pk,actor=actor)
        self.order.expires_at=timezone.now()-timedelta(seconds=1);self.order.save()
        self.assertEqual(archive_unpaid_order(self.order.pk,actor=actor),1)
        self.capacity.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.capacity.reserved_count,0);self.assertIsNotNone(self.registration.archived_at)

    def test_paid_orders_cannot_be_archived(self):
        buyer=self.paid_buyer()
        with self.assertRaises(ValidationError):archive_unpaid_order(self.order.pk,actor=buyer)

    def test_paper_batch_is_inclusive_and_idempotent(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event,motorcycle_count=2)
        actor=self.owner()
        self.assertEqual(create_paper_batch(raffle,actor),1001)
        self.assertEqual(create_paper_batch(raffle,actor),0)
        self.assertEqual(raffle.tickets.first().number,2000);self.assertEqual(raffle.tickets.last().number,3000)
        self.assertFalse(raffle.purchase_available)

    def test_digital_number_only_after_payment_and_single_number_on_replay(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event)
        self.assertIsNone(issue_registration_ticket(self.registration))
        self.assertIsNone(self.registration.registration_number)
        self.paid_buyer()
        ticket=RaffleTicket.objects.get(registration=self.registration)
        self.assertEqual(ticket.number,1)
        self.assertEqual(issue_registration_ticket(self.registration).pk,ticket.pk)
        self.registration.refresh_from_db();self.assertEqual(self.registration.registration_number,1)

    def test_paper_payment_integrates_finance_once_and_rejects_changed_buyer(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event)
        ticket=RaffleTicket.objects.create(raffle=raffle,number=2000,source='PAPER')
        point=RaffleSalesPoint.objects.create(raffle=raffle,name='Loja')
        data=dict(ticket_id=ticket.pk,actor=self.owner(),point=point,buyer_name='Comprador',buyer_phone='88999999999',amount_cents=2000,confirmed=True)
        record_paper_ticket(**data);record_paper_ticket(**data)
        self.assertEqual(FinancialEntry.objects.filter(kind='RECEITA_SORTEIO').count(),1)
        with self.assertRaises(ValidationError):record_paper_ticket(**{**data,'buyer_name':'Outra pessoa'})

    def test_automatic_own_link_requires_explicit_choice_and_matching_cpf(self):
        buyer=self.paid_buyer()
        self.order.purchaser_document=self.registration.participant_cpf='52998224725'
        self.order.save();self.registration.save()
        link_buyer_registration(self.order,buyer)
        self.assertFalse(UserPersonLink.objects.exists())
        self.registration.product_snapshot_json={'buyer_is_participant':True};self.registration.save()
        link_buyer_registration(self.order,buyer)
        self.assertTrue(UserPersonLink.objects.filter(user=buyer,person=self.person).exists())

    def test_team_operator_is_scoped_and_cannot_grant_global_owner(self):
        self.owner();url=reverse('gestao:team_management',args=[self.event.pk])
        payload={'email':'operator@example.test','full_name':'Operador','roles':['CHECKIN_AGENT'],'replace_permissions':'on'}
        self.assertEqual(self.client.post(url,payload).status_code,200)
        operator=User.objects.get(email=payload['email'])
        self.assertFalse(operator.is_staff);self.assertFalse(operator.is_superuser)
        self.assertEqual(operator.event_memberships.get().event_id,self.event.pk)
        payload.update(email='bad-role@example.test',roles=['OWNER'])
        self.assertEqual(self.client.post(url,payload).status_code,400)
        self.assertFalse(User.objects.filter(email=payload['email']).exists())

    def test_sponsor_categories_are_only_gold_silver_bronze(self):
        self.assertEqual(SponsorTier.values,['OURO','PRATA','BRONZE'])

    def test_configuration_command_is_scoped_idempotent_and_preserves_owner_rules(self):
        actor=self.owner();self.event.edition_number=3;self.event.save()
        config=PaymentFeePolicy.objects.create(enabled=False,pix_bps=125)
        arguments={'event':self.event.slug,'actor':actor.email,'stdout':StringIO()}
        call_command('apply_meeting_configuration',**arguments)
        self.assertFalse(MotorcycleRaffle.objects.exists())
        call_command('apply_meeting_configuration',apply=True,**arguments)
        call_command('apply_meeting_configuration',apply=True,**arguments)
        self.assertEqual(MotorcycleRaffle.objects.get().motorcycle_count,2)
        config.refresh_from_db();self.assertFalse(config.enabled);self.assertEqual(config.pix_bps,125)

    def test_installment_quote_uses_selected_contract_rate_and_rejects_different_count(self):
        PaymentFeePolicy.objects.create(enabled=True,max_installments=2,installment_rates={'2':751})
        quote=quote_for(self.order,'credit_card',2)
        self.assertEqual(quote.total,gross_up(18001,751))
        with self.assertRaises(ValidationError):submitted_quote(self.order,'credit_card',1,quote.token)

    def test_team_can_absorb_fees_while_allowing_installments(self):
        PaymentFeePolicy.objects.create(enabled=False,max_installments=2)
        from apps.checkout.views import _payment_context
        context=_payment_context(self.order,2)
        self.assertEqual(context['card_quote'].fee,0)
        self.assertEqual(context['mp_config']['quotedInstallments'],2)
        self.assertEqual(len(context['installment_choices']),2)
        self.assertEqual(submitted_quote(self.order,'credit_card',2,context['card_quote'].token).total,18001)
        with self.assertRaises(ValidationError):quote_for(self.order,'credit_card',3)

    def test_addons_cannot_use_a_product_from_another_event(self):
        buyer=self.paid_buyer()
        from apps.events.models import Event
        event=Event.objects.create(organization=self.event.organization,title='Outro evento',slug='other-addons',
            starts_at=self.event.starts_at,ends_at=self.event.ends_at,registration_opens_at=self.event.registration_opens_at,
            registration_closes_at=self.event.registration_closes_at)
        item=AddOn.objects.create(event=event,title='Produto de outro evento')
        before=Order.objects.count()
        with self.assertRaises(ValidationError):reserve_addons(registration_id=self.registration.pk,user=buyer,
            items={str(item.pk):1},request_key=str(uuid.uuid4()))
        self.assertEqual(Order.objects.count(),before)

    def test_revoke_team_access_preserves_global_owner(self):
        actor=self.owner()
        url=reverse('gestao:team_revoke',args=[self.event.pk,actor.pk])
        self.assertEqual(self.client.post(url).status_code,403)
        self.assertTrue(actor.event_memberships.filter(role_code='OWNER',revoked_at__isnull=True).exists())

    def test_sponsor_whatsapp_is_public_only_when_explicitly_registered(self):
        from apps.events.models import Sponsor
        sponsor=Sponsor.objects.create(event=self.event,name='Patrocinador',whatsapp_number='(88) 99999-1234')
        self.assertEqual(sponsor.whatsapp_url,'https://wa.me/5588999991234')
        sponsor.whatsapp_number=''
        self.assertEqual(sponsor.whatsapp_url,'')
