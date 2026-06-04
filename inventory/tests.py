from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AssetAuditObservation,
    AssetCurrentState,
    AssetKit,
    AssetKitItem,
    AssetKitQrCode,
    AssetQrCode,
    EventLog,
    Holder,
    Person,
    StockBalance,
    StockSku,
)
from .services import (
    DomainError,
    active_qr_for_asset,
    active_qr_for_kit,
    checkout_asset_by_qr,
    checkout_kit_by_qr,
    create_qr_print_batch,
    create_qr_for_kit,
    ensure_person_holder,
    generate_item_id,
    generate_kit_id,
    generate_sku_id,
    adjust_stock_balance,
    issue_stock,
    mark_asset_lost,
    move_stock,
    qr_data_uri,
    record_location_audit,
    receive_stock,
    register_asset,
    retire_asset,
    return_asset_by_qr,
    return_asset_from_repair,
    return_kit_by_qr,
    send_asset_to_repair,
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

    def test_generated_kit_and_sku_ids_use_numeric_order(self):
        AssetKit.objects.create(kit_id="KIT-099", name="Комплект 99")
        AssetKit.objects.create(kit_id="KIT-100", name="Комплект 100")
        StockSku.objects.create(sku_id="SKU-099", name="Наклейка")
        StockSku.objects.create(sku_id="SKU-100", name="Значок")

        self.assertEqual(generate_kit_id(), "KIT-101")
        self.assertEqual(generate_sku_id(), "SKU-101")

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

    def test_kit_checkout_and_return_move_all_items_with_one_qr(self):
        light = register_asset(name="Свет Aputure", asset_type="light", initial_holder=self.studio)
        cable = register_asset(name="Провод питания", asset_type="cable", initial_holder=self.storage)
        kit = AssetKit.objects.create(kit_id=generate_kit_id(), name="Световой набор")
        AssetKitItem.objects.create(kit=kit, item=self.asset, position_index=1)
        AssetKitItem.objects.create(kit=kit, item=light, position_index=2)
        AssetKitItem.objects.create(kit=kit, item=cable, position_index=3)
        create_qr_for_kit(kit)
        qr_code = active_qr_for_kit(kit)
        due_on = timezone.localdate() + timedelta(days=2)

        event = checkout_kit_by_qr(
            kit_id=kit.kit_id,
            person=self.alex,
            pin="1234",
            due_on=due_on,
            reason="Съемка с комплектом",
        )

        self.assertEqual(qr_code.status, AssetKitQrCode.Status.ACTIVE)
        self.assertIn(kit.kit_id, qr_code.payload_url)
        self.assertEqual(event.asset_items.count(), 3)
        for asset in [self.asset, light, cable]:
            state = AssetCurrentState.objects.get(item=asset)
            self.assertEqual(state.holder, self.alex_holder)
            self.assertEqual(state.due_on, due_on)

        return_event = return_kit_by_qr(
            kit_id=kit.kit_id,
            person=self.alex,
            pin="1234",
            to_holder=self.storage,
        )

        self.assertEqual(return_event.asset_items.count(), 3)
        for asset in [self.asset, light, cable]:
            state = AssetCurrentState.objects.get(item=asset)
            self.assertEqual(state.holder, self.storage)
            self.assertIsNone(state.due_on)

    def test_kit_checkout_fails_when_one_item_is_unavailable(self):
        light = register_asset(name="Свет Aputure", asset_type="light", initial_holder=self.studio)
        checkout_asset_by_qr(
            item_id=self.asset.item_id,
            person=self.alex,
            pin="1234",
            due_on=timezone.localdate() + timedelta(days=1),
            reason="Съемка",
        )
        kit = AssetKit.objects.create(kit_id=generate_kit_id(), name="Неполный набор")
        AssetKitItem.objects.create(kit=kit, item=self.asset, position_index=1)
        AssetKitItem.objects.create(kit=kit, item=light, position_index=2)
        create_qr_for_kit(kit)

        with self.assertRaises(DomainError):
            checkout_kit_by_qr(
                kit_id=kit.kit_id,
                person=self.maria,
                pin="4321",
                due_on=timezone.localdate() + timedelta(days=1),
                reason="Другая съемка",
            )

    def test_kit_qr_page_full_checkout_flow(self):
        light = register_asset(name="Свет Aputure", asset_type="light", initial_holder=self.studio)
        kit = AssetKit.objects.create(kit_id=generate_kit_id(), name="Световой набор")
        AssetKitItem.objects.create(kit=kit, item=self.asset, position_index=1)
        AssetKitItem.objects.create(kit=kit, item=light, position_index=2)
        create_qr_for_kit(kit)
        detail_url = reverse("inventory:kit_detail", args=[kit.kit_id])
        checkout_url = reverse("inventory:checkout_kit", args=[kit.kit_id])
        due_on = timezone.localdate() + timedelta(days=2)

        response = self.client.get(detail_url)
        self.assertContains(response, "Можно взять комплект")
        self.assertContains(response, "Световой набор")

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

        self.assertContains(response, "Комплект выдан.")
        self.assertContains(response, "Комплект на руках")
        self.assertContains(response, "Вернуть комплект")

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

    def test_location_audit_records_missing_and_unexpected_items(self):
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()
        unexpected_asset = register_asset(name="Петличка Rode", asset_type="microphone", initial_holder=self.storage)

        event = record_location_audit(
            holder=self.studio,
            auditor=manager,
            pin="5555",
            observed_item_ids=[],
            unexpected_item_ids=[unexpected_asset.item_id],
            comment="Проверка полки",
        )

        self.assertEqual(event.event_type, EventLog.Type.ASSET_AUDIT_RECORDED)
        self.assertEqual(event.recorded_by_person, manager)
        self.assertEqual(event.asset_audit_observations.count(), 2)
        self.assertTrue(
            event.asset_audit_observations.filter(
                item=self.asset,
                result=AssetAuditObservation.Result.MISSING,
            ).exists()
        )
        self.assertTrue(
            event.asset_audit_observations.filter(
                item=unexpected_asset,
                expected_holder=self.storage,
                observed_holder=self.studio,
                result=AssetAuditObservation.Result.UNEXPECTED_HOLDER,
            ).exists()
        )

    def test_location_audit_requires_inventory_role(self):
        with self.assertRaises(DomainError):
            record_location_audit(
                holder=self.studio,
                auditor=self.alex,
                pin="1234",
                observed_item_ids=[self.asset.item_id],
            )

    def test_work_login_requires_inventory_role(self):
        response = self.client.post(
            reverse("inventory:work_login"),
            {
                "person": self.alex.pk,
                "pin": "1234",
            },
        )

        self.assertContains(response, "Нужна роль администратора или ответственного за учет.")

    def test_work_audit_flow_records_event_without_admin(self):
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()

        response = self.client.post(
            reverse("inventory:work_login"),
            {
                "person": manager.pk,
                "pin": "5555",
            },
            follow=True,
        )
        self.assertContains(response, "Рабочий кабинет")

        response = self.client.post(
            reverse("inventory:work_audit_location", args=[self.studio.pk]),
            {
                "observed_items": [],
                "unexpected_item_ids": "",
                "comment": "Полка проверена",
            },
            follow=True,
        )

        self.assertContains(response, "Аудит записан.")
        event = EventLog.objects.filter(event_type=EventLog.Type.ASSET_AUDIT_RECORDED).latest("id")
        self.assertEqual(event.recorded_by_person, manager)
        self.assertTrue(
            event.asset_audit_observations.filter(
                item=self.asset,
                result=AssetAuditObservation.Result.MISSING,
            ).exists()
        )

    def test_work_stock_receive_updates_balance(self):
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()
        sku = StockSku.objects.create(sku_id=generate_sku_id(), name="Футболка")
        self.client.post(reverse("inventory:work_login"), {"person": manager.pk, "pin": "5555"})

        response = self.client.post(
            reverse("inventory:work_stock_receive", args=[sku.pk]),
            {
                "to_holder": self.storage.pk,
                "quantity": 7,
                "reason": "Партия",
            },
            follow=True,
        )

        self.assertContains(response, "Поступление записано.")
        self.assertEqual(StockBalance.objects.get(sku=sku, holder=self.storage).quantity, 7)

    def test_work_asset_action_can_mark_lost(self):
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()
        self.client.post(reverse("inventory:work_login"), {"person": manager.pk, "pin": "5555"})

        response = self.client.post(
            reverse("inventory:work_asset_action", args=[self.asset.item_id]),
            {
                "action": "lost",
                "holder": "",
                "condition": "",
                "lifecycle_status": "",
                "reason": "Не нашли на складе",
            },
            follow=True,
        )

        self.assertContains(response, "Действие записано.")
        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(state.lifecycle_status, AssetCurrentState.LifecycleStatus.LOST)

    def test_work_kits_marks_split_kit(self):
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()
        light = register_asset(name="Свет Aputure", asset_type="light", initial_holder=self.storage)
        kit = AssetKit.objects.create(kit_id=generate_kit_id(), name="Световой набор")
        AssetKitItem.objects.create(kit=kit, item=self.asset, position_index=1)
        AssetKitItem.objects.create(kit=kit, item=light, position_index=2)
        self.client.post(reverse("inventory:work_login"), {"person": manager.pk, "pin": "5555"})

        response = self.client.get(reverse("inventory:work_kits"))

        self.assertContains(response, "Разъехался")

    def test_event_log_is_append_only(self):
        event = EventLog.objects.create(event_type=EventLog.Type.ASSET_AUDIT_RECORDED, reason="Проверка")
        event.reason = "Тихое исправление"

        with self.assertRaises(ValidationError):
            event.save()

        with self.assertRaises(ValidationError):
            event.delete()

    def test_retire_asset_replaces_delete_with_domain_event(self):
        event = retire_asset(asset=self.asset, reason="Сломано без ремонта")

        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(event.event_type, EventLog.Type.ASSET_RETIRED)
        self.assertEqual(state.lifecycle_status, AssetCurrentState.LifecycleStatus.RETIRED)
        self.assertEqual(state.holder.holder_type, Holder.Type.RETIRED)

        with self.assertRaises(ValidationError):
            self.asset.delete()

    def test_asset_lost_and_repair_flows_are_correction_events(self):
        repair = Holder.objects.create(holder_type=Holder.Type.EXTERNAL, name="Сервис")
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()

        lost_event = mark_asset_lost(asset=self.asset, reason="Не найдено после съемки", recorded_by_person=manager)
        state = AssetCurrentState.objects.get(item=self.asset)
        self.assertEqual(lost_event.event_type, EventLog.Type.ASSET_CORRECTION_RECORDED)
        self.assertEqual(state.lifecycle_status, AssetCurrentState.LifecycleStatus.LOST)

        send_asset_to_repair(asset=self.asset, repair_holder=repair, reason="Нашли, нужен ремонт", recorded_by_person=manager)
        state.refresh_from_db()
        self.assertEqual(state.holder, repair)
        self.assertEqual(state.lifecycle_status, AssetCurrentState.LifecycleStatus.REPAIR)

        return_asset_from_repair(
            asset=self.asset,
            to_holder=self.storage,
            condition_after=AssetCurrentState.Condition.WORKING,
            reason="Починили",
            recorded_by_person=manager,
        )
        state.refresh_from_db()
        self.assertEqual(state.holder, self.storage)
        self.assertEqual(state.lifecycle_status, AssetCurrentState.LifecycleStatus.ACTIVE)

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

    def test_stock_receive_move_and_issue_updates_balances_and_events(self):
        sku = StockSku.objects.create(sku_id=generate_sku_id(), name="Футболка")

        receive_stock(sku=sku, to_holder=self.storage, quantity=10, reason="Партия")
        move_stock(sku=sku, from_holder=self.storage, to_holder=self.studio, quantity=4, reason="На полку")
        issue_stock(sku=sku, from_holder=self.studio, to_holder=self.alex_holder, quantity=3, reason="Подарок")

        self.assertEqual(StockBalance.objects.get(sku=sku, holder=self.storage).quantity, 6)
        self.assertEqual(StockBalance.objects.get(sku=sku, holder=self.studio).quantity, 1)
        self.assertEqual(EventLog.objects.filter(event_type=EventLog.Type.STOCK_RECEIVED).count(), 1)
        self.assertEqual(EventLog.objects.filter(event_type=EventLog.Type.STOCK_MOVED).count(), 1)
        self.assertEqual(EventLog.objects.filter(event_type=EventLog.Type.STOCK_ISSUED).count(), 1)

    def test_stock_cannot_go_negative(self):
        sku = StockSku.objects.create(sku_id=generate_sku_id(), name="Значок")
        receive_stock(sku=sku, to_holder=self.storage, quantity=2)

        with self.assertRaises(DomainError):
            issue_stock(sku=sku, from_holder=self.storage, quantity=3)

        self.assertEqual(StockBalance.objects.get(sku=sku, holder=self.storage).quantity, 2)

    def test_stock_adjustment_sets_actual_quantity(self):
        manager = Person.objects.create(display_name="Ответственный", role=Person.Role.INVENTORY_MANAGER)
        manager.set_pin("5555")
        manager.save()
        sku = StockSku.objects.create(sku_id=generate_sku_id(), name="Значок")
        receive_stock(sku=sku, to_holder=self.storage, quantity=5)

        event = adjust_stock_balance(
            sku=sku,
            holder=self.storage,
            actual_quantity=3,
            reason="Пересчет",
            recorded_by_person=manager,
        )

        self.assertEqual(event.event_type, EventLog.Type.STOCK_AUDIT_ADJUSTED)
        self.assertEqual(StockBalance.objects.get(sku=sku, holder=self.storage).quantity, 3)

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
