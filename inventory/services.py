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
    AssetCurrentState,
    AssetEventItem,
    AssetItem,
    AssetQrCode,
    EventEvidence,
    EventLog,
    Holder,
    Person,
    QrPrintBatch,
    QrPrintBatchItem,
)


class DomainError(ValidationError):
    pass


ITEM_ID_PREFIX = "GF"


def generate_item_id():
    last_number = 0
    existing_ids = AssetItem.objects.filter(item_id__regex=rf"^{ITEM_ID_PREFIX}-[0-9]+$")
    for item_id in existing_ids.values_list("item_id", flat=True):
        match = re.match(rf"^{ITEM_ID_PREFIX}-(\d+)$", item_id)
        if match:
            last_number = max(last_number, int(match.group(1)))
    next_number = last_number + 1
    return f"{ITEM_ID_PREFIX}-{next_number:03d}"


def build_asset_url(item_id):
    return urljoin(settings.GEARFLOW_PUBLIC_BASE_URL.rstrip("/") + "/", f"i/{item_id}")


def create_qr_for_asset(asset):
    payload_url = build_asset_url(asset.item_id)
    return AssetQrCode.objects.create(
        item=asset,
        code_value=payload_url,
        payload_url=payload_url,
        label_text=asset.item_id,
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


def _validate_person_pin(person, raw_pin):
    if not person.is_active:
        raise DomainError("Участник неактивен.")
    if not person.check_pin(raw_pin):
        raise DomainError("Неверный PIN-код.")


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


def overdue_assets(today=None):
    today = today or timezone.localdate()
    return (
        AssetCurrentState.objects.select_related("item", "holder", "holder__linked_person")
        .filter(holder__holder_type=Holder.Type.PERSON, due_on__lt=today)
        .order_by("due_on", "item_id")
    )
