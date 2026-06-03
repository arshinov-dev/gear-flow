from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AssetCurrentState, AssetQrCode, EventLog, Holder, Person
from .services import (
    DomainError,
    active_qr_for_asset,
    checkout_asset_by_qr,
    create_qr_print_batch,
    ensure_person_holder,
    generate_item_id,
    qr_data_uri,
    register_asset,
    return_asset_by_qr,
)


class AssetInvariantTests(TestCase):
    def setUp(self):
        self.studio = Holder.objects.create(
            holder_type=Holder.Type.LOCATION,
            name="Студия",
            is_self_service_source=True,
        )
        self.storage = Holder.objects.create(
            holder_type=Holder.Type.LOCATION,
            name="Склад",
            is_self_service_source=True,
        )
        self.alex = Person.objects.create(display_name="Алексей")
        self.alex.set_pin("1234")
        self.alex.save()
        self.alex_holder = ensure_person_holder(self.alex)
        self.maria = Person.objects.create(display_name="Мария")
        self.maria.set_pin("4321")
        self.maria.save()
        self.maria_holder = ensure_person_holder(self.maria)
        self.asset = register_asset(name="Камера Sony", asset_type="camera", initial_holder=self.studio)

    def test_register_asset_creates_event_current_state_and_qr(self):
        state = AssetCurrentState.objects.get(item=self.asset)
        qr_code = active_qr_for_asset(self.asset)

        self.assertEqual(self.asset.item_id, "GF-001")
        self.assertEqual(self.asset.registered_event.event_type, EventLog.Type.ASSET_REGISTERED)
        self.assertEqual(state.holder, self.studio)
        self.assertEqual(qr_code.label_text, self.asset.item_id)
        self.assertEqual(qr_code.status, AssetQrCode.Status.ACTIVE)
        self.assertTrue(qr_code.payload_url.startswith("http://127.0.0.1:8000/"))
        self.assertIn(self.asset.item_id, qr_code.payload_url)

    def test_ensure_person_holder_creates_and_syncs_holder(self):
        person = Person.objects.create(display_name="Иван")
        person.set_pin("1111")
        person.save()

        holder = ensure_person_holder(person)
        self.assertEqual(holder.holder_type, Holder.Type.PERSON)
        self.assertEqual(holder.name, "Иван")

        person.display_name = "Иван Петров"
        person.save()
        holder = ensure_person_holder(person)
        self.assertEqual(holder.name, "Иван Петров")

    def test_generated_item_id_uses_numeric_order(self):
        register_asset(item_id="GF-099", name="Штатив", asset_type="tripod", initial_holder=self.storage)
        register_asset(item_id="GF-100", name="Свет", asset_type="light", initial_holder=self.storage)

        self.assertEqual(generate_item_id(), "GF-101")

    def test_checkout_transfers_single_active_holder(self):
        due_on = timezone.localdate() + timedelta(days=1)

        event = checkout_asset_by_qr(
            item_id=self.asset.item_id,
            person=self.alex,
            pin="1234",
            due_on=due_on,
            reason="Съемка интервью",
        )

        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(state.holder, self.alex_holder)
        self.assertEqual(state.due_on, due_on)
        self.assertEqual(state.checkout_reason, "Съемка интервью")
        self.assertEqual(event.event_type, EventLog.Type.ASSET_TRANSFERRED)
        self.assertEqual(event.asset_items.get().from_holder, self.studio)
        self.assertEqual(event.asset_items.get().to_holder, self.alex_holder)

    def test_checkout_requires_correct_pin(self):
        with self.assertRaises(DomainError):
            checkout_asset_by_qr(
                item_id=self.asset.item_id,
                person=self.alex,
                pin="9999",
                due_on=timezone.localdate() + timedelta(days=1),
                reason="Тест",
            )

    def test_checkout_cannot_silently_take_from_another_person(self):
        checkout_asset_by_qr(
            item_id=self.asset.item_id,
            person=self.alex,
            pin="1234",
            due_on=timezone.localdate() + timedelta(days=1),
            reason="Съемка интервью",
        )

        with self.assertRaises(DomainError):
            checkout_asset_by_qr(
                item_id=self.asset.item_id,
                person=self.maria,
                pin="4321",
                due_on=timezone.localdate() + timedelta(days=1),
                reason="Другая съемка",
            )

    def test_return_is_transfer_and_clears_due_date(self):
        checkout_asset_by_qr(
            item_id=self.asset.item_id,
            person=self.alex,
            pin="1234",
            due_on=timezone.localdate() + timedelta(days=1),
            reason="Съемка интервью",
        )

        event = return_asset_by_qr(
            item_id=self.asset.item_id,
            person=self.alex,
            pin="1234",
            to_holder=self.storage,
        )

        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(state.holder, self.storage)
        self.assertIsNone(state.due_on)
        self.assertEqual(state.checkout_reason, "")
        self.assertEqual(event.event_type, EventLog.Type.ASSET_TRANSFERRED)

    def test_condition_change_is_attached_to_transfer_event(self):
        event = checkout_asset_by_qr(
            item_id=self.asset.item_id,
            person=self.alex,
            pin="1234",
            due_on=timezone.localdate() + timedelta(days=1),
            reason="Съемка интервью",
            condition_after=AssetCurrentState.Condition.NEEDS_ATTENTION,
        )

        state = AssetCurrentState.objects.get(item=self.asset)
        event_item = event.asset_items.get()
        self.assertEqual(state.condition, AssetCurrentState.Condition.NEEDS_ATTENTION)
        self.assertEqual(event_item.condition_after, AssetCurrentState.Condition.NEEDS_ATTENTION)

    def test_event_log_is_append_only(self):
        event = EventLog.objects.create(event_type=EventLog.Type.ASSET_AUDIT_RECORDED, reason="Проверка")
        event.reason = "Тихое исправление"

        with self.assertRaises(ValidationError):
            event.save()

        with self.assertRaises(ValidationError):
            event.delete()

    def test_qr_print_batch_is_not_domain_event(self):
        qr_code = active_qr_for_asset(self.asset)
        initial_event_count = EventLog.objects.count()

        batch = create_qr_print_batch(qr_codes=[qr_code], title="Печать тест")

        self.assertEqual(batch.qr_codes.count(), 1)
        self.assertEqual(EventLog.objects.count(), initial_event_count)

    def test_qr_data_uri_is_png(self):
        qr_code = active_qr_for_asset(self.asset)

        data_uri = qr_data_uri(qr_code.payload_url)

        self.assertTrue(data_uri.startswith("data:image/png;base64,"))

    def test_qr_page_full_checkout_and_return_flow(self):
        detail_url = reverse("inventory:asset_detail", args=[self.asset.item_id])
        checkout_url = reverse("inventory:checkout_asset", args=[self.asset.item_id])
        return_url = reverse("inventory:return_asset", args=[self.asset.item_id])
        due_on = timezone.localdate() + timedelta(days=2)

        response = self.client.get(detail_url)
        self.assertContains(response, "Можно взять")
        self.assertContains(response, "Взять оборудование")
        self.assertNotContains(response, "Вернуть оборудование")

        response = self.client.post(
            checkout_url,
            {
                "person": self.alex.pk,
                "pin": "1234",
                "due_on": due_on.isoformat(),
                "reason": "Съемка выпуска",
            },
            follow=True,
        )
        self.assertContains(response, "Оборудование выдано.")
        self.assertContains(response, "На руках")
        self.assertContains(response, "Вернуть оборудование")
        self.assertNotContains(response, "Взять оборудование")
        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(state.holder, self.alex_holder)

        response = self.client.post(
            return_url,
            {
                "person": self.alex.pk,
                "pin": "1234",
                "to_holder": self.storage.pk,
            },
            follow=True,
        )
        self.assertContains(response, "Оборудование возвращено.")
        self.assertContains(response, "Можно взять")
        state.refresh_from_db()
        self.assertEqual(state.holder, self.storage)
        self.assertIsNone(state.due_on)

    def test_problem_condition_requires_explicit_problem_checkbox(self):
        checkout_url = reverse("inventory:checkout_asset", args=[self.asset.item_id])
        due_on = timezone.localdate() + timedelta(days=1)

        response = self.client.post(
            checkout_url,
            {
                "person": self.alex.pk,
                "pin": "1234",
                "due_on": due_on.isoformat(),
                "reason": "Съемка",
                "has_problem": "on",
                "condition_after": "",
            },
        )

        self.assertContains(response, "Выберите состояние проблемы.")
        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(state.holder, self.studio)
