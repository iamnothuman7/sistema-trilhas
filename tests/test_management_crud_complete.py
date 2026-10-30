import uuid
from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.db.models import Sum
from apps.core.record_actions import record_version
from apps.catalog.models import Product,Lot,AddOn,Coupon,ProductVariant
from apps.events.models import SponsorProfile,Sponsor,SponsorHistory,MotorcycleRaffle,RaffleTicket,RaffleSalesPoint,RaffleResult
from apps.events.forms_sponsorships import SponsorReceiptForm
from apps.finance.models import FinancialEntry,SponsorPayment
from apps.finance.sponsorships import record_sponsor_payment
from apps.identity.models import EventMembership,RoleChoices,User
from tests import test_reporting_finance as fixtures


class ManagementCrudCompleteTests(TestCase):
    setUpTestData=classmethod(fixtures.ReportingFinanceTests.setUpTestData.__func__)
    def setUp(self):
        EventMembership.objects.create(user=self.user,event=self.event,role_code=RoleChoices.EVENT_MANAGER)
        self.client.force_login(self.user)
    def confirm(self,url,**extras):
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        data={'version':response.context['form'].initial['version'],'reason':'Correção de cadastro duplicado','confirmed':'on',**extras}
        return self.client.post(url,data)
    def net(self):return FinancialEntry.objects.filter(event=self.event).aggregate(n=Sum('amount_cents_signed'))['n'] or 0
    def sponsor(self):
        self.profile=SponsorProfile.objects.create(organization=self.org,name='Marca modelo')
        return Sponsor.objects.create(event=self.event,profile=self.profile,name=self.profile.name,agreed_amount_cents=50000,is_public=True)
    def receipt(self,sponsor,amount='100,00'):
        form=SponsorReceiptForm(dict(amount=amount,received_on=timezone.localdate().isoformat(),method='PIX',reference='Ref original',notes='',confirm_received=True,idempotency_key=str(uuid.uuid4())))
        self.assertTrue(form.is_valid(),form.errors)
        return record_sponsor_payment(event=self.event,sponsor_id=sponsor.pk,data=form.cleaned_data,actor=self.user)[0]
    def paper(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event)
        point=RaffleSalesPoint.objects.create(raffle=raffle,name='Loja 1')
        ticket=RaffleTicket.objects.create(raffle=raffle,number=40,source='PAPER',state='SOLD',point=point,buyer_name='Piloto Original',amount_cents=1000,paid_at=timezone.now()-timedelta(days=2))
        FinancialEntry.objects.create(event=self.event,raffle=raffle,kind='RECEITA_SORTEIO',amount_cents_signed=1000,source_reference=f'RAFFLE-{ticket.pk}-SALE',recognized_at=ticket.paid_at)
        return raffle,point,ticket
    def test_every_catalog_kind_can_be_removed_and_restored_without_loss(self):
        now=timezone.now()
        records=[('produto',self.product),('lote',Lot.objects.create(event=self.event,product=self.product,title='Lote piloto',opens_at=now,closes_at=now+timedelta(days=2))),
            ('adicional',AddOn.objects.create(event=self.event,title='Almoço')),('cupom',Coupon.objects.create(event=self.event,code_upper='PILOTO',valid_from=now,valid_until=now+timedelta(days=2))),
            ('tamanho',ProductVariant.objects.create(product=self.product,kind='SHIRT_SIZE',code='GG',label='GG'))]
        for kind,row in records:
            with self.subTest(kind=kind):
                url=reverse('gestao:catalog_remove',args=[self.event.pk,kind,row.pk])
                self.assertEqual(self.confirm(url).status_code,302);row.refresh_from_db();self.assertFalse(row.active)
                self.assertEqual(self.confirm(reverse('gestao:catalog_restore',args=[self.event.pk,kind,row.pk])).status_code,302);row.refresh_from_db();self.assertTrue(row.active)
        self.assertEqual(self.client.get(reverse('gestao:catalog_trash',args=[self.event.pk])).status_code,200)
    def test_catalog_refuses_stale_and_other_event_and_get_does_not_delete(self):
        url=reverse('gestao:catalog_remove',args=[self.event.pk,'produto',self.product.pk])
        version=record_version(self.product);self.client.get(url);self.product.refresh_from_db();self.assertTrue(self.product.active)
        self.product.name='Alterado';self.product.save()
        self.assertEqual(self.client.post(url,dict(version=version,reason='Cadastro de teste',confirmed='on')).status_code,400)
        self.product.refresh_from_db();self.assertTrue(self.product.active)
        foreign=Product.objects.create(event=self.other_event,name='Outro')
        self.assertEqual(self.client.get(reverse('gestao:catalog_remove',args=[self.event.pk,'produto',foreign.pk])).status_code,404)
    def test_event_archive_and_restore_preserves_children(self):
        self.assertEqual(self.confirm(reverse('gestao:event_remove',args=[self.event.pk])).status_code,302)
        self.event.refresh_from_db();self.assertEqual(self.event.status,'ARCHIVED');self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertEqual(self.confirm(reverse('gestao:event_restore',args=[self.event.pk])).status_code,302)
        self.event.refresh_from_db();self.assertEqual(self.event.status,'DRAFT')
    def test_sponsor_archive_preserves_cash_and_restore_stays_hidden(self):
        sponsor=self.sponsor();payment=self.receipt(sponsor)
        self.assertEqual(self.confirm(reverse('gestao:sponsor_remove',args=[self.event.pk,sponsor.pk])).status_code,302)
        sponsor.refresh_from_db();self.assertIsNotNone(sponsor.archived_at);self.assertFalse(sponsor.is_public);self.assertEqual(self.net(),10000)
        self.assertEqual(self.confirm(reverse('gestao:sponsor_restore',args=[self.event.pk,sponsor.pk])).status_code,302)
        sponsor.refresh_from_db();self.assertIsNone(sponsor.archived_at);self.assertFalse(sponsor.is_public)
    def test_brand_archive_all_participations_and_restore_does_not_republish(self):
        sponsor=self.sponsor();self.receipt(sponsor)
        self.assertEqual(self.confirm(reverse('gestao:sponsor_profile_remove',args=[self.profile.pk])).status_code,302)
        sponsor.refresh_from_db();self.assertIsNotNone(sponsor.archived_at);self.assertEqual(self.net(),10000)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_profile_edit',args=[self.profile.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_history',args=[self.event.pk,self.profile.pk])).status_code,200)
        self.assertEqual(self.confirm(reverse('gestao:sponsor_profile_restore',args=[self.profile.pk])).status_code,302)
        sponsor.refresh_from_db();self.assertIsNotNone(sponsor.archived_at)
    def test_sponsor_history_edit_and_delete_does_not_touch_current_cash(self):
        self.sponsor();row=SponsorHistory.objects.create(profile=self.profile,edition='2025',amount_cents=10000,created_by=self.user,request_key=uuid.uuid4())
        url=reverse('gestao:sponsor_history_edit',args=[self.event.pk,row.pk]);r=self.client.get(url)
        self.assertEqual(r.status_code,200)
        data=dict(version=r.context['form'].initial['version'],edition='Edição 2026',amount='150,00',notes='Corrigido',reason='Corrigir edição anterior')
        self.assertEqual(self.client.post(url,data).status_code,302);row.refresh_from_db();self.assertEqual(row.amount_cents,15000)
        self.assertEqual(self.confirm(reverse('gestao:sponsor_history_remove',args=[self.event.pk,row.pk])).status_code,302)
        self.assertFalse(SponsorHistory.objects.filter(pk=row.pk).exists());self.assertEqual(self.net(),0)
    def test_receipt_correction_adjusts_original_period_and_is_idempotent(self):
        sponsor=self.sponsor();old=self.receipt(sponsor);url=reverse('gestao:sponsor_payment_edit',args=[self.event.pk,sponsor.pk,old.pk]);r=self.client.get(url)
        self.assertEqual(r.status_code,200)
        data={**r.context['form'].initial,'amount':'90,00','confirm_received':'on','reason':'Corrigir valor digitado','received_on':(timezone.localdate()-timedelta(days=1)).isoformat()}
        for _ in range(2):self.assertEqual(self.client.post(url,data).status_code,302)
        old.refresh_from_db();self.assertTrue(old.reversed_at);self.assertEqual(self.net(),9000);self.assertEqual(SponsorPayment.objects.count(),2)
        self.assertEqual(old.reversal_entry.recognized_at,old.ledger_entry.recognized_at)
    def test_invalid_receipt_correction_rolls_back_reversal(self):
        sponsor=self.sponsor();old=self.receipt(sponsor);url=reverse('gestao:sponsor_payment_edit',args=[self.event.pk,sponsor.pk,old.pk]);r=self.client.get(url)
        data={**r.context['form'].initial,'amount':'900,00','confirm_received':'on','reason':'Valor maior que acordo'}
        self.assertEqual(self.client.post(url,data).status_code,400);old.refresh_from_db();self.assertIsNone(old.reversed_at);self.assertEqual(self.net(),10000)
    def test_point_remove_restore_and_ticket_correction(self):
        raffle,point,ticket=self.paper()
        self.assertEqual(self.confirm(reverse('gestao:raffle_point_remove',args=[self.event.pk,point.pk])).status_code,302);point.refresh_from_db();self.assertFalse(point.active)
        url=reverse('gestao:raffle_point_edit',args=[self.event.pk,point.pk]);r=self.client.get(url)
        self.assertEqual(self.client.post(url,dict(version=r.context['form'].initial['version'],name='Loja corrigida',contact='João',active='on')).status_code,302)
        url=reverse('gestao:raffle_ticket_edit',args=[self.event.pk,ticket.pk]);r=self.client.get(url)
        data=dict(version=r.context['form'].initial['version'],buyer_name='Piloto Correto',buyer_phone='88999990000',point=str(point.pk),amount='12,00',reason='Corrigir recibo do papel')
        self.assertEqual(self.client.post(url,data).status_code,302);ticket.refresh_from_db();self.assertEqual(ticket.amount_cents,1200);self.assertEqual(self.net(),1200)
        self.assertEqual(self.client.post(url,data).status_code,400);self.assertEqual(self.net(),1200)
        remove=reverse('gestao:raffle_ticket_remove',args=[self.event.pk,ticket.pk]);self.assertEqual(self.confirm(remove).status_code,400)
        self.assertEqual(self.confirm(remove,returned='on').status_code,302);ticket.refresh_from_db();self.assertEqual(ticket.state,'VOID');self.assertEqual(self.net(),0)
    def test_paper_cannot_be_changed_after_result_or_cross_event(self):
        raffle,point,ticket=self.paper()
        RaffleResult.objects.create(raffle=raffle,recorded_by=self.user,method_notes='Conferido',snapshot_sha256='a'*64)
        self.assertEqual(self.confirm(reverse('gestao:raffle_ticket_remove',args=[self.event.pk,ticket.pk]),returned='on').status_code,400)
        self.assertEqual(self.client.post(reverse('gestao:raffle_operations',args=[self.event.pk]),dict(action='void',number=ticket.number,reason='Pedido de cancelamento',returned='on')).status_code,400)
        ticket.refresh_from_db();self.assertEqual(ticket.state,'SOLD');self.assertEqual(self.net(),1000)
        self.assertEqual(self.client.get(reverse('gestao:raffle_ticket_edit',args=[self.other_event.pk,ticket.pk])).status_code,403)
    def test_team_edit_preserves_password_and_other_event_access(self):
        worker=User.objects.create_user(email='worker@example.test',full_name='Atendimento',password='Example-test-2026')
        EventMembership.objects.create(event=self.event,user=worker,role_code=RoleChoices.CHECKIN_AGENT)
        EventMembership.objects.create(event=self.event,user=worker,role_code=RoleChoices.FINANCE)
        other=EventMembership.objects.create(event=self.other_event,user=worker,role_code=RoleChoices.CHECKIN_AGENT)
        original=worker.password;url=reverse('gestao:team_edit',args=[self.event.pk,worker.pk]);r=self.client.get(url)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.post(url,dict(version=r.context['form'].initial['version'],full_name='Atendimento',roles=[RoleChoices.REGISTRATION_AGENT])).status_code,302)
        worker.refresh_from_db();other.refresh_from_db();self.assertEqual(worker.password,original);self.assertIsNone(other.revoked_at)
        self.assertEqual(list(EventMembership.objects.filter(event=self.event,user=worker,revoked_at=None).values_list('role_code',flat=True)),[RoleChoices.REGISTRATION_AGENT])
    def test_archive_permissions_require_finance_for_paid_sponsors(self):
        sponsor=self.sponsor();self.receipt(sponsor)
        content=User.objects.create_user(email='content@example.test',full_name='Conteúdo')
        EventMembership.objects.create(user=content,event=self.event,role_code=RoleChoices.CONTENT);self.client.force_login(content)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_remove',args=[self.event.pk,sponsor.pk])).status_code,403)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_profile_remove',args=[self.profile.pk])).status_code,403)
    def test_actions_are_visible_on_list_pages(self):
        sponsor=self.sponsor();self.receipt(sponsor);raffle,point,ticket=self.paper()
        for name in ['catalog','finance','sponsor_list','raffle_operations','raffle_finance','event_detail','team_management','shirt_stock']:
            with self.subTest(name=name):self.assertEqual(self.client.get(reverse('gestao:'+name,args=[self.event.pk])).status_code,200)
        html=self.client.get(reverse('gestao:sponsor_list',args=[self.event.pk])).content.decode()
        self.assertIn('Excluir do evento',html);self.assertIn('Excluir marca',html)
        self.assertContains(self.client.get(reverse('gestao:raffle_finance',args=[self.event.pk])),reverse('gestao:raffle_ticket_edit',args=[self.event.pk,ticket.pk]))
