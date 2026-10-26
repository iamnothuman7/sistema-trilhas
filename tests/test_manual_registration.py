from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.catalog.models import CapacityHold, ProductVariant
from apps.checkout.models import Order, OrderStatus
from apps.core.models import AuditLog
from apps.events.models import LegalDocumentVersion
from apps.finance.models import FinancialEntry
from apps.identity.models import User, EventMembership, CheckoutAccount, UserPersonLink
from apps.payments.models import ManualPaymentEvidence, ChargeAttempt
from apps.registrations.models import ManualRegistration, Registration, RegistrationCredential, TermsAcceptance
from apps.reporting.selectors import get_financial_summary
from tests import test_system as fixtures


class ManualRegistrationTests(TestCase):
    def setUp(self):
        fixtures.SistemaTrilhasTests.setUp(self)
        self.client.force_login(self.user)
        self.url = reverse('gestao:manual_registration_create', args=[self.event.pk])

    def data(self, paid=False):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return {'request_token': response.context['form'].initial['request_token'],
            'name': 'Piloto Manual Teste', 'cpf': '529.982.247-25', 'email': 'manual@example.test',
            'phone': '(88) 99999-9999', 'city': 'Tianguá', 'state': 'CE', 'team': 'Equipe Teste',
            'lot': str(self.lot.pk), 'shirt': 'M', 'notes': 'Atendimento presencial autorizado.',
            'payment_state': 'received' if paid else 'pending', 'method': 'CASH' if paid else '',
            'amount': '180,00' if paid else '', 'payment_notes': 'Recebido no balcão pela equipe.' if paid else '',
            'received_confirmed': 'on' if paid else ''}

    def create(self, paid=False):
        data = self.data(paid)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors)
        return ManualRegistration.objects.select_related('registration__source_order_line__order').get(), data

    def detail(self, entry):
        return reverse('gestao:registration_detail', args=[self.event.pk, entry.registration_id])

    def receipt_url(self, entry):
        return reverse('gestao:manual_registration_payment', args=[self.event.pk, entry.registration_id])

    def receipt(self):
        return {'method':'PIX_MANUAL','amount':'180,00','payment_notes':'Pix conferido no aplicativo.','received_confirmed':'on'}

    def link(self, entry):
        response = self.client.get(self.detail(entry))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        return urlsplit(response.context['manual_access_url']).path

    def test_paid_creates_real_manual_ledger_capacity_account_but_never_terms_or_qr(self):
        entry, _ = self.create(True)
        reg = entry.registration; order = reg.source_order_line.order
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertNotEqual(order.purchaser_user_id, self.user.pk)
        self.assertEqual(order.purchaser_user.email, 'manual@example.test')
        self.assertFalse(order.purchaser_user.has_usable_password())
        self.assertEqual(CheckoutAccount.objects.get().order, order)
        self.assertEqual(reg.participant_cpf, '52998224725')
        self.assertEqual(reg.commercial_state, 'ACTIVE')
        self.assertEqual(reg.operational_state, 'NOT_READY')
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertFalse(RegistrationCredential.objects.exists())
        self.assertFalse(ChargeAttempt.objects.exists())
        self.assertTrue(reg.entitlements.filter(benefit_type='KIT').exists())
        self.assertEqual(order.receivables.get().allocated_paid_cents, 18000)
        self.assertEqual(ManualPaymentEvidence.objects.get().verified_by, self.user)
        self.assertEqual(FinancialEntry.objects.get().amount_cents_signed, 18000)
        self.assertEqual(get_financial_summary(self.event)['revenue_cents'], 18000)
        self.cap_resource.refresh_from_db(); self.lot.refresh_from_db()
        self.assertEqual((self.cap_resource.reserved_count, self.cap_resource.committed_count, self.lot.current_sold),(0,1,1))
        self.assertTrue(AuditLog.objects.filter(action='MANUAL_REGISTRATION_CREATED').exists())
        self.assertTrue(AuditLog.objects.filter(action='MANUAL_PAYMENT_CONFIRMED').exists())

    def test_pending_is_unpaid_and_can_be_settled_once_later(self):
        entry, _ = self.create()
        order = entry.registration.source_order_line.order
        self.assertIsNone(order.purchaser_user_id)
        self.assertEqual(order.status, 'RESERVED')
        self.assertFalse(FinancialEntry.objects.exists())
        self.assertFalse(User.objects.filter(email='manual@example.test').exists())
        for _ in range(2):
            self.assertEqual(self.client.post(self.receipt_url(entry), self.receipt()).status_code, 302)
        self.assertEqual(FinancialEntry.objects.count(), 1)
        self.assertEqual(ManualPaymentEvidence.objects.count(), 1)
        self.assertEqual(CheckoutAccount.objects.count(), 1)

    def test_duplicate_submission_is_idempotent_but_modified_replay_is_rejected(self):
        entry, data = self.create(True)
        self.assertEqual(self.client.post(self.url, data).status_code,302)
        data['notes'] = 'Outra solicitação.'
        self.assertEqual(self.client.post(self.url, data).status_code,400)
        self.assertEqual(Order.objects.count(),1)
        self.assertEqual(FinancialEntry.objects.count(),1)

    def test_same_cpf_in_second_manual_form_is_rejected(self):
        self.create()
        self.assertEqual(self.client.post(self.url, self.data()).status_code,400)
        self.assertEqual(Order.objects.count(),1)

    def test_shared_cpf_constraint_blocks_public_checkout_after_manual(self):
        self.create()
        data={'purchaser_name':'Comprador','purchaser_email':'public@example.test','purchaser_phone':'88999999999',
            'participant_name':'Piloto','participant_document':'52998224725','product_id':str(self.product.pk),'lot_id':str(self.lot.pk),'shirt_size':'M'}
        response=Client().post(reverse('checkout:start_registration',args=[self.event.slug]),data)
        self.assertContains(response,'Já existe uma inscrição ou reserva')
        self.assertEqual(Order.objects.count(),1)

    def test_wrong_amount_or_unconfirmed_receipt_rolls_back_everything(self):
        data=self.data(True);data['amount']='1,00'
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(Order.objects.exists());self.assertFalse(Registration.objects.exists())
        self.assertFalse(FinancialEntry.objects.exists());self.assertFalse(CapacityHold.objects.exists())
        self.cap_resource.refresh_from_db();self.assertEqual(self.cap_resource.reserved_count,0)
        data['amount']='180,00';data['received_confirmed']=''
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(Order.objects.exists())

    def test_variant_extra_price_is_enforced_server_side(self):
        ProductVariant.objects.create(product=self.product,kind='SHIRT_SIZE',code='M',label='M especial',extra_price_cents=1000)
        data=self.data(True)
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        data['amount']='190,00'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(Order.objects.get().total_cents,19000)

    def test_event_capacity_and_lot_limits_reject_without_partial_records(self):
        data=self.data()
        self.event.max_attendees=0;self.event.save(update_fields=['max_attendees'])
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(Order.objects.exists())
        self.event.max_attendees=100;self.event.save(update_fields=['max_attendees'])
        self.lot.current_sold=self.lot.max_quantity;self.lot.save(update_fields=['current_sold'])
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(Order.objects.exists())

    def test_unavailable_event_and_invalid_cpf_cannot_be_registered(self):
        data=self.data();data['cpf']='111.111.111-11'
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        data['cpf']='529.982.247-25'
        self.event.status='CANCELED';self.event.save(update_fields=['status'])
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(Order.objects.exists())

    def test_registration_operator_cannot_assert_received_payment_or_settle(self):
        entry,_=self.create()
        operator=User.objects.create_user(email='operator@example.test',full_name='Operador')
        EventMembership.objects.create(user=operator,event=self.event,role_code='REGISTRATION_AGENT')
        self.client.force_login(operator)
        data=self.data(True);data['cpf']='11144477735'
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertEqual(self.client.post(self.receipt_url(entry),self.receipt()).status_code,403)
        self.assertFalse(FinancialEntry.objects.exists())

    def test_anonymous_and_unscoped_users_cannot_view_or_post(self):
        data=self.data()
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
        outsider=User.objects.create_user(email='outsider@example.test',full_name='Outra conta')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url,data).status_code,403)
        self.assertFalse(Order.objects.exists())

    def test_form_token_cannot_be_forged_or_reused_by_other_operator(self):
        data=self.data();original=data['request_token'];data['request_token']='forged'
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        manager=User.objects.create_user(email='manager@example.test',full_name='Gestor',is_staff=True)
        self.client.force_login(manager);data['request_token']=original
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(Order.objects.exists())

    def test_expired_reservation_and_provider_charge_cannot_receive_manual_payment(self):
        entry,_=self.create();order=entry.registration.source_order_line.order
        order.expires_at=timezone.now()-timedelta(seconds=1);order.save(update_fields=['expires_at'])
        self.assertEqual(self.client.post(self.receipt_url(entry),self.receipt()).status_code,400)
        order.expires_at=timezone.now()+timedelta(minutes=20);order.save(update_fields=['expires_at'])
        ChargeAttempt.objects.create(order=order,payment_method='PIX',amount_cents=18000,idempotency_key='test-manual-charge')
        self.assertEqual(self.client.post(self.receipt_url(entry),self.receipt()).status_code,400)
        self.assertFalse(FinancialEntry.objects.exists())

    def test_link_get_does_not_grant_access_and_post_is_single_use_csrf_protected(self):
        entry,_=self.create(True);link=self.link(entry)
        guest=Client(enforce_csrf_checks=True)
        page=guest.get(link)
        self.assertEqual(page.status_code,200)
        self.assertNotIn('checkout_order_ids',guest.session)
        self.assertEqual(guest.post(link).status_code,403)
        from django.conf import settings
        response=guest.post(link,{'csrfmiddlewaretoken':guest.cookies[settings.CSRF_COOKIE_NAME].value})
        self.assertEqual(response.status_code,302)
        self.assertEqual(guest.session['checkout_order_ids'],[str(entry.registration.source_order_line.order_id)])
        self.assertEqual(Client().get(link).status_code,410)
        self.assertEqual(guest.get(response.url).status_code,200)

    def test_renewing_link_invalidates_old_link_without_resetting_account_password(self):
        entry,_=self.create(True);old=self.link(entry)
        url=reverse('gestao:manual_registration_renew_link',args=[self.event.pk,entry.registration_id])
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(self.client.post(url).status_code,302)
        self.assertEqual(Client().get(old).status_code,410)
        self.assertEqual(Client().get(self.link(entry)).status_code,200)

    def test_existing_account_is_not_claimed_by_typing_its_email(self):
        user=User.objects.create_user(email='manual@example.test',full_name='Existing',password='KeepExistingPass82')
        before=user.password
        entry,_=self.create(True)
        self.assertIsNone(entry.registration.source_order_line.order.purchaser_user_id)
        self.assertFalse(CheckoutAccount.objects.exists())
        user.refresh_from_db();self.assertEqual(user.password,before)
        self.assertFalse(UserPersonLink.objects.exists())

    def test_full_participant_access_password_terms_pdf_and_kit_entitlement(self):
        entry,_=self.create(True);reg=entry.registration;order=reg.source_order_line.order
        doc=LegalDocumentVersion.objects.create(event=self.event,kind='REGULAMENTO',version_number=1,content='Regulamento para teste',published_at=timezone.now()-timedelta(minutes=1),effective_at=timezone.now()-timedelta(minutes=1))
        guest=Client()
        response=guest.post(self.link(entry))
        self.assertEqual(response.status_code,302)
        access=reverse('identity:purchase_access',args=[order.pk])
        response=guest.post(access,{'new_password1':'PilotoTeste942','new_password2':'PilotoTeste942'})
        self.assertEqual(response.status_code,302)
        user=User.objects.get(email='manual@example.test')
        self.assertEqual(guest.session['_auth_user_id'],str(user.pk))
        claim=guest.post(reverse('minha_conta:create_claim',args=[reg.pk]))
        claim_path=urlsplit(claim.context['claim_url']).path
        self.assertEqual(guest.post(claim_path,{'confirm_holder':'yes'}).status_code,302)
        self.assertEqual(guest.post(reverse('minha_conta:accept_terms',args=[reg.pk]),{'document_version_id':[str(doc.pk)]}).status_code,302)
        reg.refresh_from_db();self.assertEqual(reg.operational_state,'READY')
        self.assertTrue(reg.credential.is_valid)
        self.assertEqual(reg.entitlements.get(benefit_type='KIT').quantity_total,1)
        pdf=guest.get(reverse('minha_conta:registration_pdf',args=[reg.pk]))
        self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertEqual(TermsAcceptance.objects.get().accepting_user,user)

    def test_cpf_search_accepts_formatted_cpf(self):
        entry,_=self.create()
        page=self.client.get(reverse('gestao:registration_list',args=[self.event.pk]),{'q':'529.982.247-25'})
        self.assertEqual(list(page.context['registrations']),[entry.registration])
