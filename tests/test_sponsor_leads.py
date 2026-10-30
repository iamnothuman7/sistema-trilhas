import uuid
from django.test import TestCase
from django.urls import reverse
from apps.events.models import SponsorLead,Sponsor,SponsorProfile,SponsorHistory
from apps.events.sponsor_leads import convert_lead
from apps.events.sponsor_leads import archive_lead
from django.core.exceptions import ValidationError
from apps.finance.models import FinancialEntry
from apps.reporting.selectors import get_financial_summary
from tests import test_system as system_fixtures


class SponsorLeadTests(TestCase):
    def setUp(self):
        system_fixtures.SistemaTrilhasTests.setUp(self);self.client.force_login(self.user)

    def test_minimal_lead_then_contact_history_convert_no_money_or_duplicate(self):
        url=reverse('gestao:sponsor_lead_create',args=[self.event.pk])
        result=self.client.post(url,{'name':'Parceiro Árvore','status':'NEW','revision':0})
        self.assertEqual(result.status_code,302)
        lead=SponsorLead.objects.get();self.assertIsNone(lead.sponsor_id)
        edit=reverse('gestao:sponsor_lead_edit',args=[self.event.pk,lead.pk])
        result=self.client.post(edit,{'name':lead.name,'status':'NEGOTIATING','revision':lead.revision,
            'phone':'88999999999','address':'Rua da visita','previous_edition':'2026','previous_amount':'1.500,00'})
        self.assertEqual(result.status_code,302)
        lead.refresh_from_db();self.assertEqual(lead.previous_amount_cents,150000)
        profile=SponsorProfile.objects.create(organization=self.org,name='parceiro arvore')
        args=dict(event=self.event,lead_id=lead.pk,version=lead.revision,actor=self.user)
        sponsor,created=convert_lead(**args);self.assertTrue(created)
        self.assertEqual(sponsor.profile,profile);self.assertIsNone(sponsor.agreed_amount_cents)
        self.assertFalse(sponsor.is_public);self.assertFalse(FinancialEntry.objects.exists())
        self.assertFalse(convert_lead(**args)[1]);self.assertEqual(Sponsor.objects.count(),1)
        self.assertEqual(SponsorHistory.objects.get().amount_cents,150000)
        self.assertEqual(get_financial_summary(self.event)['revenue_cents'],0)

    def test_normalized_duplicate_and_stale_edit_rejected(self):
        lead=SponsorLead.objects.create(event=self.event,name='Árvore Parceira')
        url=reverse('gestao:sponsor_lead_create',args=[self.event.pk])
        self.assertEqual(self.client.post(url,{'name':'arvore   parceira','status':'NEW','revision':0}).status_code,400)
        edit=reverse('gestao:sponsor_lead_edit',args=[self.event.pk,lead.pk])
        data={'name':lead.name,'status':'SCHEDULED','revision':0}
        self.assertEqual(self.client.post(edit,data).status_code,302)
        self.assertEqual(self.client.post(edit,data).status_code,400)

    def test_historical_addition_is_idempotent_and_not_current_income(self):
        profile=SponsorProfile.objects.create(organization=self.org,name='Marca anterior')
        url=reverse('gestao:sponsor_history',args=[self.event.pk,profile.pk])
        data={'edition':'2º Trilhão / 2026','amount':'2.000,00','notes':'Apoiou anteriormente','request_key':uuid.uuid4()}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(SponsorHistory.objects.count(),1);self.assertFalse(FinancialEntry.objects.exists())
        self.assertContains(self.client.get(url),'2.000,00')

    def test_lead_permissions_and_current_event(self):
        from apps.identity.models import User,EventMembership
        user=User.objects.create_user(email='reader@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code='FINANCE')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_leads',args=[self.event.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_lead_create',args=[self.event.pk])).status_code,403)

    def test_content_role_cannot_read_or_change_historical_amounts(self):
        from apps.identity.models import User,EventMembership
        lead=SponsorLead.objects.create(event=self.event,name='Marca privada',previous_edition='2026',previous_amount_cents=234567)
        user=User.objects.create_user(email='content-lead@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code='CONTENT')
        self.client.force_login(user)
        listing=self.client.get(reverse('gestao:sponsor_leads',args=[self.event.pk]))
        self.assertNotContains(listing,'2.345,67')
        url=reverse('gestao:sponsor_lead_edit',args=[self.event.pk,lead.pk])
        self.assertNotContains(self.client.get(url),'name="previous_amount"')
        response=self.client.post(url,dict(name=lead.name,status='NEW',revision=0,previous_edition='2026',previous_amount='1,00'))
        self.assertEqual(response.status_code,302)
        lead.refresh_from_db();self.assertEqual(lead.previous_amount_cents,234567)

    def test_remove_restore_after_visit_and_confirm_preserves_participation(self):
        lead=SponsorLead.objects.create(event=self.event,name='Empresa para visitar',status='SCHEDULED')
        remove=reverse('gestao:sponsor_lead_remove',args=[self.event.pk,lead.pk])
        listing=reverse('gestao:sponsor_leads',args=[self.event.pk])
        self.assertEqual(self.client.get(remove).status_code,200)
        self.assertEqual(self.client.post(remove,dict(revision=0,reason='Não confirmou na visita')).status_code,400)
        data=dict(revision=0,reason='Não confirmou na visita',confirmed='on')
        self.assertEqual(self.client.post(remove,data).status_code,302)
        self.assertEqual(self.client.post(remove,data).status_code,302)
        self.assertNotContains(self.client.get(listing),lead.name)
        self.assertContains(self.client.get(listing,{'archived':1}),lead.name)
        lead.refresh_from_db()
        with self.assertRaises(ValidationError):convert_lead(event=self.event,lead_id=lead.pk,version=lead.revision,actor=self.user)
        self.assertEqual(self.client.get(reverse('gestao:sponsor_lead_edit',args=[self.event.pk,lead.pk])).status_code,404)
        restore=reverse('gestao:sponsor_lead_restore',args=[self.event.pk,lead.pk])
        self.assertEqual(self.client.post(restore,dict(revision=lead.revision,reason='Retomar conversa após visita',confirmed='on')).status_code,302)
        lead.refresh_from_db();sponsor,_=convert_lead(event=self.event,lead_id=lead.pk,version=lead.revision,actor=self.user)
        lead.refresh_from_db();archive_lead(event=self.event,lead_id=lead.pk,revision=lead.revision,actor=self.user,reason='Contato já convertido em patrocínio')
        self.assertTrue(Sponsor.objects.filter(pk=sponsor.pk).exists());self.assertFalse(FinancialEntry.objects.exists())

    def test_archived_name_cannot_be_duplicated_stale_remove_and_event_scope(self):
        from apps.identity.models import User,EventMembership
        lead=SponsorLead.objects.create(event=self.event,name='Marca Árvore',revision=2)
        with self.assertRaises(ValidationError):archive_lead(event=self.event,lead_id=lead.pk,revision=1,actor=self.user,reason='Página desatualizada')
        archive_lead(event=self.event,lead_id=lead.pk,revision=2,actor=self.user,reason='Visita sem interesse')
        response=self.client.post(reverse('gestao:sponsor_lead_create',args=[self.event.pk]),dict(name='marca arvore',revision=0,status='NEW'))
        self.assertContains(response,'Removidos',status_code=400)
        user=User.objects.create_user(email='finance-lead-remove@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code='FINANCE');self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('gestao:sponsor_lead_restore',args=[self.event.pk,lead.pk]),dict(revision=3,reason='Sem permissão',confirmed='on')).status_code,403)
