from urllib.parse import parse_qs

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Sponsor, SponsorProfile
from apps.events.sponsorships import brand_values
from apps.identity.models import EventMembership, RoleChoices
from tests import test_sponsorship_finance as fixtures


class SponsorCategoryTests(TestCase):
    setUp = fixtures.SponsorshipFinanceTests.setUp
    role = fixtures.SponsorshipFinanceTests.role

    def get(self, **query):
        return self.client.get(reverse('gestao:sponsor_bank'), query)

    def counts(self, response):
        return {item['key']: item['count'] for item in response.context['categories']}

    def add(self, name, tier, event=None, **fields):
        profile = SponsorProfile.objects.create(organization=self.org, name=name)
        return Sponsor.objects.create(event=event or self.event, profile=profile, tier=tier,
                                      **brand_values(profile), **fields)

    def test_initial_view_contains_prerendered_but_collapsed_category_pages(self):
        self.add('Patrocinador prata', 'PRATA')
        response = self.get(event_id=self.event.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(response), {'OURO': 1, 'PRATA': 1, 'BRONZE': 0})
        self.assertFalse(response.context['show_records'])
        self.assertContains(response, self.profile.name)
        self.assertContains(response, 'Patrocinador prata')
        self.assertContains(response, 'class="management-category-card"', count=3)
        self.assertContains(response, 'data-group-toggle="PRATA"')
        for category in response.context['categories']:
            self.assertFalse(category['expanded'])
            self.assertContains(response, f'id="sponsor-panel-{category["key"]}" data-group-panel="{category["key"]}" class="management-category-panel" hidden')
        self.assertFalse(response.context['all_category']['expanded'])

    def test_shared_brand_counts_once_per_category_and_selected_tier_limits_participations(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, tier='PRATA', **brand_values(self.profile))
        response = self.get()
        self.assertEqual(self.counts(response), {'OURO': 1, 'PRATA': 1, 'BRONZE': 0})
        self.assertEqual(response.context['all_count'], 1)
        selected = self.get(tier='PRATA')
        self.assertTrue(selected.context['show_records'])
        self.assertEqual([s.event_id for s in selected.context['cards'][0]['participations']], [self.other.pk])
        self.assertContains(selected, self.profile.name)
        self.assertNotContains(selected, self.sponsor.agreement_notes)

    def test_same_tier_in_two_events_counts_one_brand(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, tier='OURO', **brand_values(self.profile))
        self.assertEqual(self.counts(self.get())['OURO'], 1)

    def test_each_panel_has_its_own_first_page_and_only_requested_panel_advances(self):
        for number in range(25):
            self.add(f'Prata para paginação {number:02}', 'PRATA')
        initial = self.get(event_id=self.event.pk)
        panels = {item['key']: item for item in initial.context['categories']}
        self.assertEqual(len(panels['PRATA']['page_obj']), 24)
        self.assertEqual(panels['PRATA']['count'], 25)
        selected = self.get(event_id=self.event.pk, tier='PRATA', page=2)
        panels = {item['key']: item for item in selected.context['categories']}
        self.assertEqual(len(panels['PRATA']['page_obj']), 1)
        self.assertEqual(panels['PRATA']['page_obj'].number, 2)
        self.assertTrue(panels['PRATA']['expanded'])
        self.assertFalse(panels['OURO']['expanded'])
        self.assertEqual(panels['OURO']['page_obj'].number, 1)
        self.assertEqual(selected.context['all_category']['page_obj'].number, 1)

    def test_summary_is_stable_when_opening_other_groups_without_a_request(self):
        self.add('Prata contratada', 'PRATA', agreed_amount_cents=25000)
        initial = self.get(event_id=self.event.pk)
        selected = self.get(event_id=self.event.pk, tier='PRATA')
        self.assertEqual(initial.context['totals'], selected.context['totals'])
        self.assertEqual(selected.context['totals']['agreed_cents'], 125000)

    def test_search_status_and_back_to_categories_keep_filters(self):
        self.add('Parceiro bronze', 'BRONZE', is_public=True)
        selected = self.get(event_id=self.event.pk, q='bronze', status='public', tier='BRONZE')
        self.assertEqual(self.counts(selected), {'OURO': 0, 'PRATA': 0, 'BRONZE': 1})
        back = selected.context['categories_url']
        returned = self.client.get(back)
        self.assertFalse(returned.context['show_records'])
        self.assertEqual(returned.context['q'], 'bronze')
        self.assertEqual(returned.context['status'], 'public')
        self.assertEqual(self.counts(returned)['BRONZE'], 1)
        self.assertTrue(self.get(q='bronze').context['show_records'])

    def test_archived_records_are_separate_and_pagination_keeps_category_scope(self):
        for number in range(25):
            self.add(f'Parceiro antigo {number:02}', 'PRATA', archived_at=timezone.now())
        active = self.get(event_id=self.event.pk)
        self.assertEqual(self.counts(active)['PRATA'], 0)
        removed = self.get(event_id=self.event.pk, archived='1', tier='PRATA', q='Parceiro antigo')
        self.assertEqual(removed.context['total_count'], 25)
        self.assertEqual(len(removed.context['cards']), 24)
        params = parse_qs(removed.context['pagination_query'])
        self.assertEqual(params['archived'], ['1'])
        self.assertEqual(params['tier'], ['PRATA'])
        self.assertEqual(params['event_id'], [str(self.event.pk)])
        page_two = self.client.get(reverse('gestao:sponsor_bank') + '?' + removed.context['pagination_query'] + '&page=2')
        self.assertEqual(len(page_two.context['cards']), 1)
        self.assertContains(page_two, 'Restaurar participação')

    def test_unlinked_brands_and_legacy_categories_remain_accessible(self):
        profile = SponsorProfile.objects.create(organization=self.org, name='Marca aguardando edição')
        self.add('Apoiador legado', 'APOIO')
        initial = self.get()
        self.assertEqual(self.counts(initial)['UNLINKED'], 1)
        self.assertEqual(self.counts(initial)['APOIO'], 1)
        self.assertContains(initial, profile.name)
        unlinked = self.get(tier='UNLINKED')
        self.assertContains(unlinked, profile.name)
        self.assertEqual([row['brand'].pk for row in unlinked.context['cards']], [profile.pk])
        self.assertContains(unlinked, 'Vincular a um evento')
        self.assertContains(self.get(tier='APOIO'), 'Apoiador legado')
        filtered = self.get(tier='APOIO', q='nenhum resultado')
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.context['total_count'], 0)
        self.assertEqual(self.get(tier='APOIO', archived='1').status_code, 200)

    def test_counts_and_money_obey_event_permissions(self):
        self.add('Prata de outro evento', 'PRATA', event=self.other, agreed_amount_cents=987654)
        user = self.role(RoleChoices.CONTENT)
        initial = self.get(event_id=self.event.pk)
        self.assertEqual(self.counts(initial)['PRATA'], 0)
        selected = self.get(event_id=self.event.pk, tier='OURO')
        self.assertNotContains(selected, 'Total combinado')
        self.assertNotContains(selected, 'R$ 1.000,00')
        self.assertEqual(self.get(event_id=self.other.pk, tier='PRATA').status_code, 403)
        EventMembership.objects.create(user=user, event=self.other, role_code=RoleChoices.FINANCE)
        selected = self.get(tier='PRATA')
        self.assertEqual(selected.context['totals']['agreed_cents'], 987654)
        self.assertContains(selected, 'R$ 9.876,54')

    def test_unknown_category_does_not_silently_show_unrelated_records(self):
        self.assertEqual(self.get(tier='INVALID').status_code, 404)
