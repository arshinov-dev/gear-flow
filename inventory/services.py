import re
from base64 import b64encode
from io import BytesIO
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils.text import get_valid_filename
from django.utils import timezone
import qrcode

from .models import (
    AssetAuditObservation,
    AssetCurrentState,
    AssetEventItem,
    AssetKit,
    AssetKitQrCode,
    AssetItem,
    AssetQrCode,
    EventEvidence,
    EventLog,
    Holder,
    Person,
    QrPrintBatch,
    QrPrintBatchItem,
    StockBalance,
    StockEventItem,
    StockSku,
)


class DomainError(ValidationError):
    pass


ITEM_ID_PREFIX = "GF"
KIT_ID_PREFIX = "KIT"
SKU_ID_PREFIX = "SKU"


def _generate_prefixed_id(model, field_name, prefix):
    last_number = 0
    existing_ids = model.objects.filter(**{f"{field_name}__regex": rf"^{prefix}-[0-9]+$"})
    for value in existing_ids.values_list(field_name, flat=True):
        match = re.match(rf"^{prefix}-(\d+)$", value)
        if match:
            last_number = max(last_number, int(match.group(1)))
    next_number = last_number + 1
    return f"{prefix}-{next_number:03d}"


def generate_item_id():
    return _generate_prefixed_id(AssetItem, "item_id", ITEM_ID_PREFIX)


def generate_kit_id():
    return _generate_prefixed_id(AssetKit, "kit_id", KIT_ID_PREFIX)


def generate_sku_id():
    return _generate_prefixed_id(StockSku, "sku_id", SKU_ID_PREFIX)


def build_asset_url(item_id):
    return urljoin(settings.GEARFLOW_PUBLIC_BASE_URL.rstrip("/") + "/", f"i/{item_id}")


def build_kit_url(kit_id):
    return urljoin(settings.GEARFLOW_PUBLIC_BASE_URL.rstrip("/") + "/", f"k/{kit_id}")


def create_qr_for_asset(asset):
    payload_url = build_asset_url(asset.item_id)
    return AssetQrCode.objects.create(
        item=asset,
        code_value=payload_url,
        payload_url=payload_url,
        label_text=asset.item_id,
    )


def create_qr_for_kit(kit):
    payload_url = build_kit_url(kit.kit_id)
    return AssetKitQrCode.objects.create(
        kit=kit,
        code_value=payload_url,
        payload_url=payload_url,
        label_text=kit.kit_id,
    )


@transaction.atomic
def ensure_person_holder(person):
    holder, created = Holder.objects.get_or_create(
        linked_person=person,
        holder_type=Holder.Type.PERSON,
        defaults={
            "name": person.display_name,
            "is_active": person.is_active,
        },
    )
    changed_fields = []
    if holder.name != person.display_name:
        holder.name = person.display_name
        changed_fields.append("name")
    if holder.is_active != person.is_active:
        holder.is_active = person.is_active
        changed_fields.append("is_active")
    if changed_fields:
        holder.save(update_fields=changed_fields + ["updated_at"])
    return holder


@transaction.atomic
def ensure_retired_holder():
    holder, _created = Holder.objects.get_or_create(
        holder_type=Holder.Type.RETIRED,
        name="Списано",
        defaults={"is_active": True},
    )
    return holder


@transaction.atomic
def register_asset(
    *,
    name,
    asset_type,
    initial_holder,
    item_id=None,
    condition=None,
    created_by=None,
    serial_number="",
    model="",
    manufacturer="",
    inventory_number="",
):
    if initial_holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Первичный учет оборудования должен начинаться с места хранения.")

    item_id = item_id or generate_item_id()
    condition = condition or AssetCurrentState.Condition.WORKING

    if AssetItem.objects.filter(item_id=item_id).exists():
        raise DomainError(f"ID {item_id} уже существует.")

    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_REGISTERED,
        recorded_by_admin=created_by,
        reason="Регистрация оборудования",
    )
    asset = AssetItem.objects.create(
        item_id=item_id,
        name=name,
        asset_type=asset_type,
        serial_number=serial_number,
        model=model,
        manufacturer=manufacturer,
        inventory_number=inventory_number,
        registered_event=event,
        created_by=created_by,
    )
    AssetEventItem.objects.create(
        event=event,
        item=asset,
        to_holder=initial_holder,
        condition_after=condition,
        lifecycle_status_after=AssetCurrentState.LifecycleStatus.ACTIVE,
    )
    AssetCurrentState.objects.create(
        item=asset,
        holder=initial_holder,
        condition=condition,
        lifecycle_status=AssetCurrentState.LifecycleStatus.ACTIVE,
        last_event=event,
    )
    create_qr_for_asset(asset)
    return asset


@transaction.atomic
def create_qr_print_batch(*, qr_codes, title="", created_by=None):
    qr_codes = list(qr_codes)
    if not qr_codes:
        raise DomainError("В пакет печати нужно добавить хотя бы один QR-код.")
    batch = QrPrintBatch.objects.create(title=title, created_by=created_by)
    QrPrintBatchItem.objects.bulk_create(
        [
            QrPrintBatchItem(batch=batch, qr_code=qr_code, position_index=index)
            for index, qr_code in enumerate(qr_codes, start=1)
        ]
    )
    return batch


def active_qr_for_asset(asset):
    return asset.qr_codes.get(status=AssetQrCode.Status.ACTIVE)


def active_qr_for_kit(kit):
    return kit.qr_codes.get(status=AssetKitQrCode.Status.ACTIVE)


def qr_data_uri(payload):
    image = qrcode.make(payload)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def get_active_qr_by_item_id(item_id):
    return AssetQrCode.objects.select_related("item").get(
        item_id=item_id,
        status=AssetQrCode.Status.ACTIVE,
    )


def get_active_qr_by_kit_id(kit_id):
    return AssetKitQrCode.objects.select_related("kit").get(
        kit_id=kit_id,
        status=AssetKitQrCode.Status.ACTIVE,
    )


def _validate_person_pin(person, raw_pin):
    if not person.is_active:
        raise DomainError("Участник неактивен.")
    if not person.check_pin(raw_pin):
        raise DomainError("Неверный PIN-код.")


def _validate_inventory_manager_pin(person, raw_pin):
    _validate_person_pin(person, raw_pin)
    if not person.can_manage_inventory:
        raise DomainError("Аудит может записывать только администратор или ответственный за учет.")


def _validate_inventory_manager(person):
    if not person.is_active:
        raise DomainError("Участник неактивен.")
    if not person.can_manage_inventory:
        raise DomainError("Действие может выполнять только администратор или ответственный за учет.")


def _person_holder(person):
    try:
        return person.holder
    except Holder.DoesNotExist as exc:
        raise DomainError("У выбранного человека нет держателя ответственности.") from exc


def _attach_problem_photo(event, problem_photo):
    if not problem_photo:
        return
    if hasattr(problem_photo, "read"):
        safe_name = get_valid_filename(problem_photo.name)
        stored_path = default_storage.save(f"problem_photos/{event.id}/{safe_name}", problem_photo)
    else:
        stored_path = str(problem_photo)
    EventEvidence.objects.create(
        event=event,
        evidence_type=EventEvidence.Type.PHOTO,
        uri_or_value=stored_path,
    )


def _locked_states_for_kit(kit):
    item_ids = list(kit.items.values_list("item_id", flat=True))
    states = (
        AssetCurrentState.objects.select_for_update()
        .select_related("item", "holder")
        .filter(item_id__in=item_ids)
    )
    state_by_item_id = {state.item_id: state for state in states}
    return [state_by_item_id[item_id] for item_id in item_ids if item_id in state_by_item_id]


def _validate_kit_ready_for_checkout(kit, states):
    if kit.status != AssetKit.Status.ACTIVE:
        raise DomainError("Комплект в архиве и недоступен для выдачи.")
    if not states:
        raise DomainError("В комплекте нет оборудования.")
    if len(states) != kit.items.count():
        raise DomainError("В составе комплекта есть оборудование без текущего состояния.")
    for state in states:
        if state.lifecycle_status != AssetCurrentState.LifecycleStatus.ACTIVE:
            raise DomainError(f"{state.item.item_id} недоступен для выдачи.")
        if state.holder.holder_type != Holder.Type.LOCATION:
            raise DomainError(f"{state.item.item_id} уже числится за: {state.holder.name}.")
        if not state.holder.is_self_service_source:
            raise DomainError(f"{state.item.item_id} нельзя взять по QR из места: {state.holder.name}.")


@transaction.atomic
def checkout_asset_by_qr(
    *,
    item_id,
    person,
    pin,
    due_on,
    reason,
    condition_after="",
    problem_photo=None,
):
    _validate_person_pin(person, pin)
    if not due_on:
        raise DomainError("Дата возврата обязательна.")
    if not reason or not reason.strip():
        raise DomainError("Причина взятия обязательна.")

    qr_code = get_active_qr_by_item_id(item_id)
    state = AssetCurrentState.objects.select_for_update().select_related("holder").get(item=qr_code.item)

    if state.lifecycle_status in {
        AssetCurrentState.LifecycleStatus.RETIRED,
        AssetCurrentState.LifecycleStatus.DISPOSED,
        AssetCurrentState.LifecycleStatus.LOST,
    }:
        raise DomainError("Объект недоступен для выдачи.")
    if not state.holder.is_self_service_source:
        if state.holder.holder_type == Holder.Type.PERSON:
            raise DomainError(f"Объект уже числится за: {state.holder.name}.")
        raise DomainError("Текущий держатель не разрешает самообслуживаемую выдачу.")
    if state.holder.holder_type == Holder.Type.EXTERNAL:
        raise DomainError("Объект находится у внешнего держателя.")

    target_holder = _person_holder(person)
    new_condition = condition_after or state.condition
    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_TRANSFERRED,
        recorded_by_person=person,
        reason=reason.strip(),
        payload={"qr_code": qr_code.code_value, "flow": "checkout"},
    )
    AssetEventItem.objects.create(
        event=event,
        item=qr_code.item,
        from_holder=state.holder,
        to_holder=target_holder,
        condition_before=state.condition,
        condition_after=new_condition,
        lifecycle_status_before=state.lifecycle_status,
        lifecycle_status_after=state.lifecycle_status,
        due_on=due_on,
    )
    _attach_problem_photo(event, problem_photo)

    state.holder = target_holder
    state.condition = new_condition
    state.due_on = due_on
    state.checkout_reason = reason.strip()
    state.last_event = event
    state.save(update_fields=["holder", "condition", "due_on", "checkout_reason", "last_event", "updated_at"])
    return event


@transaction.atomic
def checkout_kit_by_qr(
    *,
    kit_id,
    person,
    pin,
    due_on,
    reason,
    condition_after="",
    problem_photo=None,
):
    _validate_person_pin(person, pin)
    if not due_on:
        raise DomainError("Дата возврата обязательна.")
    if not reason or not reason.strip():
        raise DomainError("Причина взятия обязательна.")

    qr_code = get_active_qr_by_kit_id(kit_id)
    kit = qr_code.kit
    states = _locked_states_for_kit(kit)
    _validate_kit_ready_for_checkout(kit, states)

    target_holder = _person_holder(person)
    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_TRANSFERRED,
        recorded_by_person=person,
        reason=reason.strip(),
        payload={"qr_code": qr_code.code_value, "flow": "kit_checkout", "kit_id": kit.kit_id},
    )
    for state in states:
        new_condition = condition_after or state.condition
        AssetEventItem.objects.create(
            event=event,
            item=state.item,
            from_holder=state.holder,
            to_holder=target_holder,
            condition_before=state.condition,
            condition_after=new_condition,
            lifecycle_status_before=state.lifecycle_status,
            lifecycle_status_after=state.lifecycle_status,
            due_on=due_on,
        )
        state.holder = target_holder
        state.condition = new_condition
        state.due_on = due_on
        state.checkout_reason = reason.strip()
        state.last_event = event
        state.save(update_fields=["holder", "condition", "due_on", "checkout_reason", "last_event", "updated_at"])
    _attach_problem_photo(event, problem_photo)
    return event


@transaction.atomic
def return_asset_by_qr(
    *,
    item_id,
    person,
    pin,
    to_holder,
    condition_after="",
    problem_photo=None,
):
    _validate_person_pin(person, pin)
    if to_holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Возврат возможен только в место хранения.")

    qr_code = get_active_qr_by_item_id(item_id)
    state = AssetCurrentState.objects.select_for_update().select_related("holder").get(item=qr_code.item)
    source_holder = _person_holder(person)

    if state.holder_id != source_holder.id:
        raise DomainError("Объект числится за другим держателем. Нужен аудит/коррекция.")

    new_condition = condition_after or state.condition
    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_TRANSFERRED,
        recorded_by_person=person,
        reason="Возврат оборудования",
        payload={"qr_code": qr_code.code_value, "flow": "return"},
    )
    AssetEventItem.objects.create(
        event=event,
        item=qr_code.item,
        from_holder=state.holder,
        to_holder=to_holder,
        condition_before=state.condition,
        condition_after=new_condition,
        lifecycle_status_before=state.lifecycle_status,
        lifecycle_status_after=state.lifecycle_status,
    )
    _attach_problem_photo(event, problem_photo)

    state.holder = to_holder
    state.condition = new_condition
    state.due_on = None
    state.checkout_reason = ""
    state.last_event = event
    state.save(update_fields=["holder", "condition", "due_on", "checkout_reason", "last_event", "updated_at"])
    return event


@transaction.atomic
def return_kit_by_qr(
    *,
    kit_id,
    person,
    pin,
    to_holder,
    condition_after="",
    problem_photo=None,
):
    _validate_person_pin(person, pin)
    if to_holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Возврат возможен только в место хранения.")

    qr_code = get_active_qr_by_kit_id(kit_id)
    kit = qr_code.kit
    states = _locked_states_for_kit(kit)
    if not states:
        raise DomainError("В комплекте нет оборудования.")

    source_holder = _person_holder(person)
    for state in states:
        if state.holder_id != source_holder.id:
            raise DomainError(f"{state.item.item_id} числится за другим держателем. Нужен аудит/коррекция.")

    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_TRANSFERRED,
        recorded_by_person=person,
        reason="Возврат комплекта",
        payload={"qr_code": qr_code.code_value, "flow": "kit_return", "kit_id": kit.kit_id},
    )
    for state in states:
        new_condition = condition_after or state.condition
        AssetEventItem.objects.create(
            event=event,
            item=state.item,
            from_holder=state.holder,
            to_holder=to_holder,
            condition_before=state.condition,
            condition_after=new_condition,
            lifecycle_status_before=state.lifecycle_status,
            lifecycle_status_after=state.lifecycle_status,
        )
        state.holder = to_holder
        state.condition = new_condition
        state.due_on = None
        state.checkout_reason = ""
        state.last_event = event
        state.save(update_fields=["holder", "condition", "due_on", "checkout_reason", "last_event", "updated_at"])
    _attach_problem_photo(event, problem_photo)
    return event


@transaction.atomic
def retire_asset(*, asset, reason, recorded_by_person=None, recorded_by_admin=None):
    if not reason or not reason.strip():
        raise DomainError("Причина списания обязательна.")

    state = AssetCurrentState.objects.select_for_update().select_related("holder").get(item=asset)
    if state.lifecycle_status in {
        AssetCurrentState.LifecycleStatus.RETIRED,
        AssetCurrentState.LifecycleStatus.DISPOSED,
    }:
        raise DomainError(f"{asset.item_id} уже списано.")

    retired_holder = ensure_retired_holder()
    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_RETIRED,
        recorded_by_person=recorded_by_person,
        recorded_by_admin=recorded_by_admin,
        reason=reason.strip(),
    )
    AssetEventItem.objects.create(
        event=event,
        item=asset,
        from_holder=state.holder,
        to_holder=retired_holder,
        condition_before=state.condition,
        condition_after=state.condition,
        lifecycle_status_before=state.lifecycle_status,
        lifecycle_status_after=AssetCurrentState.LifecycleStatus.RETIRED,
    )
    state.holder = retired_holder
    state.lifecycle_status = AssetCurrentState.LifecycleStatus.RETIRED
    state.due_on = None
    state.checkout_reason = ""
    state.last_event = event
    state.save(update_fields=["holder", "lifecycle_status", "due_on", "checkout_reason", "last_event", "updated_at"])
    return event


@transaction.atomic
def correct_asset_state(
    *,
    asset,
    reason,
    recorded_by_person=None,
    recorded_by_admin=None,
    to_holder=None,
    condition_after=None,
    lifecycle_status_after=None,
):
    if not reason or not reason.strip():
        raise DomainError("Причина коррекции обязательна.")
    state = AssetCurrentState.objects.select_for_update().select_related("holder").get(item=asset)
    new_holder = to_holder or state.holder
    new_condition = condition_after or state.condition
    new_lifecycle_status = lifecycle_status_after or state.lifecycle_status

    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_CORRECTION_RECORDED,
        recorded_by_person=recorded_by_person,
        recorded_by_admin=recorded_by_admin,
        reason=reason.strip(),
    )
    AssetEventItem.objects.create(
        event=event,
        item=asset,
        from_holder=state.holder,
        to_holder=new_holder,
        condition_before=state.condition,
        condition_after=new_condition,
        lifecycle_status_before=state.lifecycle_status,
        lifecycle_status_after=new_lifecycle_status,
    )
    state.holder = new_holder
    state.condition = new_condition
    state.lifecycle_status = new_lifecycle_status
    if new_holder.holder_type != Holder.Type.PERSON:
        state.due_on = None
        state.checkout_reason = ""
    state.last_event = event
    state.save(
        update_fields=[
            "holder",
            "condition",
            "lifecycle_status",
            "due_on",
            "checkout_reason",
            "last_event",
            "updated_at",
        ]
    )
    return event


def mark_asset_lost(*, asset, reason, recorded_by_person=None, recorded_by_admin=None):
    return correct_asset_state(
        asset=asset,
        reason=reason,
        recorded_by_person=recorded_by_person,
        recorded_by_admin=recorded_by_admin,
        lifecycle_status_after=AssetCurrentState.LifecycleStatus.LOST,
    )


def send_asset_to_repair(*, asset, repair_holder, reason, recorded_by_person=None, recorded_by_admin=None):
    if repair_holder.holder_type != Holder.Type.EXTERNAL:
        raise DomainError("Ремонт должен быть внешним держателем.")
    return correct_asset_state(
        asset=asset,
        reason=reason,
        recorded_by_person=recorded_by_person,
        recorded_by_admin=recorded_by_admin,
        to_holder=repair_holder,
        condition_after=AssetCurrentState.Condition.NEEDS_ATTENTION,
        lifecycle_status_after=AssetCurrentState.LifecycleStatus.REPAIR,
    )


def return_asset_from_repair(
    *,
    asset,
    to_holder,
    condition_after,
    reason,
    recorded_by_person=None,
    recorded_by_admin=None,
):
    if to_holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Вернуть из ремонта можно только в место хранения.")
    return correct_asset_state(
        asset=asset,
        reason=reason,
        recorded_by_person=recorded_by_person,
        recorded_by_admin=recorded_by_admin,
        to_holder=to_holder,
        condition_after=condition_after,
        lifecycle_status_after=AssetCurrentState.LifecycleStatus.ACTIVE,
    )


@transaction.atomic
def record_location_audit(
    *,
    holder,
    auditor,
    pin=None,
    observed_item_ids,
    unexpected_item_ids=None,
    comment="",
    recorded_by_admin=None,
):
    if pin is None:
        _validate_inventory_manager(auditor)
    else:
        _validate_inventory_manager_pin(auditor, pin)
    if holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Аудит можно записать только для места хранения.")

    observed_item_ids = {item_id.strip() for item_id in observed_item_ids if item_id and item_id.strip()}
    unexpected_item_ids = {
        item_id.strip()
        for item_id in (unexpected_item_ids or [])
        if item_id and item_id.strip()
    }
    all_observed_ids = observed_item_ids | unexpected_item_ids
    expected_states = (
        AssetCurrentState.objects.select_for_update()
        .select_related("item", "holder")
        .filter(holder=holder)
        .exclude(
            lifecycle_status__in=[
                AssetCurrentState.LifecycleStatus.RETIRED,
                AssetCurrentState.LifecycleStatus.DISPOSED,
            ]
        )
    )
    expected_by_id = {state.item_id: state for state in expected_states}
    known_observed_states = {
        state.item_id: state
        for state in AssetCurrentState.objects.select_related("item", "holder").filter(item_id__in=all_observed_ids)
    }
    unknown_item_ids = sorted(all_observed_ids - set(known_observed_states))

    event = EventLog.objects.create(
        event_type=EventLog.Type.ASSET_AUDIT_RECORDED,
        recorded_by_person=auditor,
        recorded_by_admin=recorded_by_admin,
        reason=f"Аудит места: {holder.name}",
        comment=comment.strip(),
        payload={
            "holder_id": holder.id,
            "holder_name": holder.name,
            "unknown_item_ids": unknown_item_ids,
        },
    )

    for item_id, state in expected_by_id.items():
        result = (
            AssetAuditObservation.Result.MATCHED
            if item_id in all_observed_ids
            else AssetAuditObservation.Result.MISSING
        )
        AssetAuditObservation.objects.create(
            event=event,
            item=state.item,
            expected_holder=holder,
            observed_holder=holder if result == AssetAuditObservation.Result.MATCHED else None,
            expected_condition=state.condition,
            observed_condition=state.condition if result == AssetAuditObservation.Result.MATCHED else "",
            result=result,
        )

    for item_id in sorted(all_observed_ids - set(expected_by_id) - set(unknown_item_ids)):
        state = known_observed_states[item_id]
        AssetAuditObservation.objects.create(
            event=event,
            item=state.item,
            expected_holder=state.holder,
            observed_holder=holder,
            expected_condition=state.condition,
            observed_condition=state.condition,
            result=AssetAuditObservation.Result.UNEXPECTED_HOLDER,
        )

    return event


def overdue_assets(today=None):
    today = today or timezone.localdate()
    return (
        AssetCurrentState.objects.select_related("item", "holder", "holder__linked_person")
        .filter(
            holder__holder_type=Holder.Type.PERSON,
            lifecycle_status=AssetCurrentState.LifecycleStatus.ACTIVE,
            due_on__lt=today,
        )
        .order_by("due_on", "item_id")
    )


def _validate_positive_quantity(quantity):
    if quantity <= 0:
        raise DomainError("Количество должно быть больше нуля.")


def _locked_stock_balance(sku, holder):
    try:
        return StockBalance.objects.select_for_update().get(sku=sku, holder=holder)
    except StockBalance.DoesNotExist as exc:
        raise DomainError(f"Нет остатка {sku.name} в: {holder.name}.") from exc


def _get_or_create_locked_stock_balance(sku, holder):
    balance, _created = StockBalance.objects.select_for_update().get_or_create(
        sku=sku,
        holder=holder,
        defaults={"quantity": 0},
    )
    return balance


@transaction.atomic
def receive_stock(*, sku, to_holder, quantity, reason="", recorded_by_admin=None):
    _validate_positive_quantity(quantity)
    if to_holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Поступление мерча возможно только в место хранения.")
    if not sku.is_active:
        raise DomainError("SKU неактивен.")

    event = EventLog.objects.create(
        event_type=EventLog.Type.STOCK_RECEIVED,
        recorded_by_admin=recorded_by_admin,
        reason=reason.strip() or "Поступление мерча",
    )
    balance = _get_or_create_locked_stock_balance(sku, to_holder)
    balance.quantity += quantity
    balance.save(update_fields=["quantity", "updated_at"])
    StockEventItem.objects.create(event=event, sku=sku, to_holder=to_holder, quantity=quantity)
    return event


@transaction.atomic
def move_stock(*, sku, from_holder, to_holder, quantity, reason="", recorded_by_admin=None):
    _validate_positive_quantity(quantity)
    if from_holder == to_holder:
        raise DomainError("Места отправления и назначения должны отличаться.")
    if to_holder.holder_type != Holder.Type.LOCATION:
        raise DomainError("Перемещение мерча возможно только в место хранения.")

    source_balance = _locked_stock_balance(sku, from_holder)
    if source_balance.quantity < quantity:
        raise DomainError("Недостаточно остатка для перемещения.")

    target_balance = _get_or_create_locked_stock_balance(sku, to_holder)
    event = EventLog.objects.create(
        event_type=EventLog.Type.STOCK_MOVED,
        recorded_by_admin=recorded_by_admin,
        reason=reason.strip() or "Перемещение мерча",
    )
    source_balance.quantity -= quantity
    target_balance.quantity += quantity
    source_balance.save(update_fields=["quantity", "updated_at"])
    target_balance.save(update_fields=["quantity", "updated_at"])
    StockEventItem.objects.create(
        event=event,
        sku=sku,
        from_holder=from_holder,
        to_holder=to_holder,
        quantity=quantity,
    )
    return event


@transaction.atomic
def issue_stock(*, sku, from_holder, quantity, reason="", to_holder=None, recorded_by_admin=None):
    _validate_positive_quantity(quantity)
    source_balance = _locked_stock_balance(sku, from_holder)
    if source_balance.quantity < quantity:
        raise DomainError("Недостаточно остатка для выдачи.")

    event = EventLog.objects.create(
        event_type=EventLog.Type.STOCK_ISSUED,
        recorded_by_admin=recorded_by_admin,
        reason=reason.strip() or "Выдача мерча",
    )
    source_balance.quantity -= quantity
    source_balance.save(update_fields=["quantity", "updated_at"])
    StockEventItem.objects.create(
        event=event,
        sku=sku,
        from_holder=from_holder,
        to_holder=to_holder,
        quantity=quantity,
    )
    return event


@transaction.atomic
def adjust_stock_balance(*, sku, holder, actual_quantity, reason, recorded_by_person=None, recorded_by_admin=None):
    if actual_quantity < 0:
        raise DomainError("Фактический остаток не может быть отрицательным.")
    if not reason or not reason.strip():
        raise DomainError("Причина корректировки обязательна.")

    balance = _get_or_create_locked_stock_balance(sku, holder)
    previous_quantity = balance.quantity
    if previous_quantity == actual_quantity:
        event_type = EventLog.Type.STOCK_AUDIT_RECORDED
        delta = 0
    else:
        event_type = EventLog.Type.STOCK_AUDIT_ADJUSTED
        delta = abs(actual_quantity - previous_quantity)

    event = EventLog.objects.create(
        event_type=event_type,
        recorded_by_person=recorded_by_person,
        recorded_by_admin=recorded_by_admin,
        reason=reason.strip(),
        payload={
            "sku_id": sku.sku_id,
            "holder_id": holder.id,
            "previous_quantity": previous_quantity,
            "actual_quantity": actual_quantity,
        },
    )
    if delta:
        StockEventItem.objects.create(
            event=event,
            sku=sku,
            from_holder=holder if actual_quantity < previous_quantity else None,
            to_holder=holder if actual_quantity > previous_quantity else None,
            quantity=delta,
        )
    balance.quantity = actual_quantity
    balance.save(update_fields=["quantity", "updated_at"])
    return event
