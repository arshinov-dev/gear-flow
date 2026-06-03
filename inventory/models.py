from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Person(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Администратор"
        MEMBER = "member", "Участник"
        INVENTORY_MANAGER = "inventory_manager", "Ответственный за учет"

    display_name = models.CharField("имя", max_length=160)
    email = models.EmailField("почта", blank=True)
    role = models.CharField("роль", max_length=32, choices=Role.choices, default=Role.MEMBER)
    pin_hash = models.CharField("PIN-хеш", max_length=256)
    pin_updated_at = models.DateTimeField("PIN обновлен", null=True, blank=True)
    pin_reset_required = models.BooleanField("нужно сбросить PIN", default=False)
    is_active = models.BooleanField("активен", default=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    updated_at = models.DateTimeField("обновлен", auto_now=True)

    class Meta:
        ordering = ["display_name"]
        verbose_name = "человек"
        verbose_name_plural = "люди"

    def __str__(self):
        return self.display_name

    def set_pin(self, raw_pin):
        validate_pin(raw_pin)
        self.pin_hash = make_password(raw_pin)
        self.pin_updated_at = timezone.now()
        self.pin_reset_required = False

    def check_pin(self, raw_pin):
        return bool(raw_pin) and check_password(raw_pin, self.pin_hash)


class Holder(models.Model):
    class Type(models.TextChoices):
        PERSON = "person", "человек"
        LOCATION = "location", "место"
        EXTERNAL = "external", "внешний держатель"
        RETIRED = "retired", "выведено из оборота"

    holder_type = models.CharField("тип держателя", max_length=32, choices=Type.choices)
    name = models.CharField("название", max_length=160)
    linked_person = models.OneToOneField(
        Person,
        verbose_name="связанный человек",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="holder",
    )
    is_active = models.BooleanField("активен", default=True)
    is_self_service_source = models.BooleanField("самовыдача по QR", default=False)
    is_temporary = models.BooleanField("временный", default=False)
    starts_at = models.DateTimeField("начало", null=True, blank=True)
    ends_at = models.DateTimeField("окончание", null=True, blank=True)
    managed_by = models.ForeignKey(
        Person,
        verbose_name="ответственный",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_holders",
    )
    metadata = models.JSONField("метаданные", default=dict, blank=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    updated_at = models.DateTimeField("обновлен", auto_now=True)

    class Meta:
        ordering = ["holder_type", "name"]
        verbose_name = "держатель"
        verbose_name_plural = "держатели"
        constraints = [
            models.UniqueConstraint(
                fields=["linked_person"],
                condition=Q(holder_type="person", is_active=True),
                name="one_active_holder_per_person",
            )
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.holder_type == self.Type.PERSON and not self.linked_person:
            raise ValidationError({"linked_person": "Для держателя-человека нужен связанный участник."})
        if self.holder_type != self.Type.PERSON and self.linked_person:
            raise ValidationError({"linked_person": "Связанный участник допустим только для держателя-человека."})


class EventLog(models.Model):
    class Type(models.TextChoices):
        ASSET_REGISTERED = "AssetRegistered", "Оборудование зарегистрировано"
        ASSET_TRANSFERRED = "AssetTransferred", "Оборудование передано"
        ASSET_AUDIT_RECORDED = "AssetAuditRecorded", "Проведен аудит оборудования"
        ASSET_CORRECTION_RECORDED = "AssetCorrectionRecorded", "Записана коррекция оборудования"
        ASSET_RETIRED = "AssetRetired", "Оборудование выведено"
        ASSET_REACTIVATED = "AssetReactivated", "Оборудование возвращено в оборот"
        STOCK_RECEIVED = "StockReceived", "Мерч поступил"
        STOCK_MOVED = "StockMoved", "Мерч перемещен"
        STOCK_ISSUED = "StockIssued", "Мерч выдан"
        STOCK_AUDIT_RECORDED = "StockAuditRecorded", "Проведен аудит мерча"
        STOCK_AUDIT_ADJUSTED = "StockAuditAdjusted", "Скорректирован остаток мерча"

    event_type = models.CharField("тип события", max_length=64, choices=Type.choices)
    happened_at = models.DateTimeField("произошло", default=timezone.now)
    recorded_at = models.DateTimeField("записано", auto_now_add=True)
    recorded_by_person = models.ForeignKey(
        Person,
        verbose_name="записал участник",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="recorded_events",
    )
    recorded_by_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="записал администратор",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="gearflow_recorded_events",
    )
    reason = models.TextField("причина", blank=True)
    comment = models.TextField("комментарий", blank=True)
    correction_of_event = models.ForeignKey(
        "self",
        verbose_name="исправляет событие",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    payload = models.JSONField("данные", default=dict, blank=True)
    idempotency_key = models.CharField("ключ идемпотентности", max_length=128, unique=True, null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = "событие"
        verbose_name_plural = "журнал событий"

    def __str__(self):
        return f"{self.get_event_type_display()} #{self.pk}"

    def save(self, *args, **kwargs):
        if self.pk and EventLog.objects.filter(pk=self.pk).exists():
            raise ValidationError("События неизменяемы. Создайте корректирующее событие.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("События нельзя удалять.")


class AssetItem(models.Model):
    item_id = models.CharField("ID", max_length=32, primary_key=True)
    asset_type = models.CharField("тип", max_length=80)
    name = models.CharField("название", max_length=160)
    serial_number = models.CharField("серийный номер", max_length=160, blank=True)
    model = models.CharField("модель", max_length=160, blank=True)
    manufacturer = models.CharField("производитель", max_length=160, blank=True)
    inventory_number = models.CharField("инвентарный номер", max_length=160, blank=True)
    metadata = models.JSONField("метаданные", default=dict, blank=True)
    registered_event = models.ForeignKey(
        EventLog,
        verbose_name="событие регистрации",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registered_assets",
    )
    created_at = models.DateTimeField("создано", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="создал",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_assets",
    )

    class Meta:
        ordering = ["item_id"]
        verbose_name = "оборудование"
        verbose_name_plural = "оборудование"

    def __str__(self):
        return f"{self.item_id} · {self.name}"


class AssetQrCode(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "активен"
        REVOKED = "revoked", "отозван"

    code_value = models.CharField("значение QR", max_length=255, unique=True)
    payload_url = models.CharField("ссылка", max_length=255)
    label_text = models.CharField("надпись", max_length=80)
    item = models.ForeignKey(
        AssetItem,
        verbose_name="оборудование",
        on_delete=models.CASCADE,
        related_name="qr_codes",
    )
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    revoked_at = models.DateTimeField("отозван", null=True, blank=True)

    class Meta:
        ordering = ["item_id", "-created_at"]
        verbose_name = "QR-код"
        verbose_name_plural = "QR-коды"
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(status="active"),
                name="one_active_qr_per_asset",
            )
        ]

    def __str__(self):
        return self.label_text


class QrPrintBatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "черновик"
        PRINTED = "printed", "напечатан"

    created_at = models.DateTimeField("создан", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="создал",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qr_print_batches",
    )
    title = models.CharField("название", max_length=160, blank=True)
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.DRAFT)
    printed_at = models.DateTimeField("напечатан", null=True, blank=True)
    qr_codes = models.ManyToManyField(
        AssetQrCode,
        verbose_name="QR-коды",
        through="QrPrintBatchItem",
        related_name="print_batches",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "пакет печати QR"
        verbose_name_plural = "пакеты печати QR"

    def __str__(self):
        return self.title or f"Пакет печати #{self.pk}"


class QrPrintBatchItem(models.Model):
    batch = models.ForeignKey(QrPrintBatch, verbose_name="пакет печати", on_delete=models.CASCADE)
    qr_code = models.ForeignKey(AssetQrCode, verbose_name="QR-код", on_delete=models.CASCADE)
    position_index = models.PositiveIntegerField("позиция", default=0)

    class Meta:
        ordering = ["position_index", "id"]
        verbose_name = "QR в пакете печати"
        verbose_name_plural = "QR в пакете печати"
        constraints = [
            models.UniqueConstraint(fields=["batch", "qr_code"], name="unique_qr_in_print_batch")
        ]


class AssetCurrentState(models.Model):
    class Condition(models.TextChoices):
        WORKING = "working", "Работает"
        NEEDS_ATTENTION = "needs_attention", "Требует внимания"
        BROKEN = "broken", "Сломано"
        UNKNOWN = "unknown", "Неизвестно"

    class LifecycleStatus(models.TextChoices):
        ACTIVE = "active", "Активно"
        LOST = "lost", "Потеряно"
        REPAIR = "repair", "Ремонт"
        RETIRED = "retired", "Выведено"
        DISPOSED = "disposed", "Утилизировано"

    item = models.OneToOneField(
        AssetItem,
        verbose_name="оборудование",
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="current_state",
    )
    holder = models.ForeignKey(
        Holder,
        verbose_name="текущий держатель",
        on_delete=models.PROTECT,
        related_name="current_assets",
    )
    condition = models.CharField(
        "состояние",
        max_length=32,
        choices=Condition.choices,
        default=Condition.WORKING,
    )
    lifecycle_status = models.CharField(
        "статус",
        max_length=32,
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.ACTIVE,
    )
    due_on = models.DateField("вернуть до", null=True, blank=True)
    checkout_reason = models.TextField("причина взятия", blank=True)
    last_event = models.ForeignKey(
        EventLog,
        verbose_name="последнее событие",
        on_delete=models.PROTECT,
        related_name="asset_state_updates",
    )
    updated_at = models.DateTimeField("обновлено", auto_now=True)

    class Meta:
        verbose_name = "текущее состояние"
        verbose_name_plural = "текущее состояние"
        indexes = [
            models.Index(fields=["holder"]),
            models.Index(fields=["due_on"]),
        ]

    def __str__(self):
        return f"{self.item_id}: {self.holder}"


class AssetEventItem(models.Model):
    event = models.ForeignKey(
        EventLog,
        verbose_name="событие",
        on_delete=models.PROTECT,
        related_name="asset_items",
    )
    item = models.ForeignKey(
        AssetItem,
        verbose_name="оборудование",
        on_delete=models.PROTECT,
        related_name="event_items",
    )
    from_holder = models.ForeignKey(
        Holder,
        verbose_name="откуда",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="asset_events_from",
    )
    to_holder = models.ForeignKey(
        Holder,
        verbose_name="куда",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="asset_events_to",
    )
    condition_before = models.CharField("состояние до", max_length=32, blank=True)
    condition_after = models.CharField("состояние после", max_length=32, blank=True)
    lifecycle_status_before = models.CharField("статус до", max_length=32, blank=True)
    lifecycle_status_after = models.CharField("статус после", max_length=32, blank=True)
    due_on = models.DateField("вернуть до", null=True, blank=True)
    note = models.TextField("заметка", blank=True)

    class Meta:
        verbose_name = "строка события оборудования"
        verbose_name_plural = "строки событий оборудования"
        constraints = [
            models.UniqueConstraint(fields=["event", "item"], name="unique_asset_item_per_event")
        ]
        indexes = [models.Index(fields=["item"])]


class AssetAuditObservation(models.Model):
    class Result(models.TextChoices):
        MATCHED = "matched", "совпадает"
        MISSING = "missing", "не найдено"
        UNEXPECTED_HOLDER = "unexpected_holder", "неожиданный держатель"
        CONDITION_MISMATCH = "condition_mismatch", "расхождение состояния"

    event = models.ForeignKey(
        EventLog,
        verbose_name="событие",
        on_delete=models.PROTECT,
        related_name="asset_audit_observations",
    )
    item = models.ForeignKey(
        AssetItem,
        verbose_name="оборудование",
        on_delete=models.PROTECT,
        related_name="audit_observations",
    )
    expected_holder = models.ForeignKey(
        Holder,
        verbose_name="ожидаемый держатель",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="expected_audit_observations",
    )
    observed_holder = models.ForeignKey(
        Holder,
        verbose_name="фактический держатель",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="observed_audit_observations",
    )
    expected_condition = models.CharField("ожидаемое состояние", max_length=32, blank=True)
    observed_condition = models.CharField("фактическое состояние", max_length=32, blank=True)
    result = models.CharField("результат", max_length=32, choices=Result.choices)
    note = models.TextField("заметка", blank=True)

    class Meta:
        verbose_name = "наблюдение аудита"
        verbose_name_plural = "наблюдения аудита"
        constraints = [
            models.UniqueConstraint(fields=["event", "item"], name="unique_asset_audit_observation")
        ]


class EventEvidence(models.Model):
    class Type(models.TextChoices):
        PHOTO = "photo", "фото"
        FILE = "file", "файл"
        LINK = "link", "ссылка"
        QR_SCAN = "qr_scan", "QR-скан"

    event = models.ForeignKey(
        EventLog,
        verbose_name="событие",
        on_delete=models.PROTECT,
        related_name="evidence",
    )
    evidence_type = models.CharField("тип доказательства", max_length=32, choices=Type.choices)
    uri_or_value = models.CharField("файл или значение", max_length=512)
    metadata = models.JSONField("метаданные", default=dict, blank=True)
    created_at = models.DateTimeField("создано", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="создал",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gearflow_evidence",
    )

    class Meta:
        verbose_name = "доказательство события"
        verbose_name_plural = "доказательства событий"


def validate_pin(raw_pin):
    if not raw_pin or len(raw_pin) != 4 or not raw_pin.isdigit():
        raise ValidationError("PIN должен состоять из 4 цифр.")
