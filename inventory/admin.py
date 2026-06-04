from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AssetAuditObservation,
    AssetCurrentState,
    AssetEventItem,
    AssetKit,
    AssetKitItem,
    AssetKitQrCode,
    AssetItem,
    AssetQrCode,
    EventEvidence,
    EventLog,
    Holder,
    OverdueAssetCurrentState,
    Person,
    QrPrintBatch,
    QrPrintBatchItem,
    StockBalance,
    StockEventItem,
    StockSku,
    validate_pin,
)
from .services import (
    DomainError,
    active_qr_for_asset,
    active_qr_for_kit,
    create_qr_print_batch,
    create_qr_for_kit,
    ensure_person_holder,
    generate_kit_id,
    generate_sku_id,
    issue_stock,
    move_stock,
    qr_data_uri,
    receive_stock,
    register_asset,
    retire_asset,
)


admin.site.site_header = "Учет оборудования"
admin.site.site_title = "Учет оборудования"
admin.site.index_title = "Панель управления"


class PersonAdminForm(forms.ModelForm):
    pin = forms.CharField(
        label="PIN",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="4 цифры. При редактировании оставьте пустым, чтобы не менять.",
    )

    class Meta:
        model = Person
        fields = "__all__"

    def clean_pin(self):
        pin = self.cleaned_data.get("pin")
        if pin:
            validate_pin(pin)
        elif not self.instance.pk:
            raise ValidationError("PIN обязателен для нового участника.")
        return pin

    def save(self, commit=True):
        person = super().save(commit=False)
        pin = self.cleaned_data.get("pin")
        if pin:
            person.set_pin(pin)
        if commit:
            person.save()
            self.save_m2m()
        return person


class RegisterAssetAdminForm(forms.Form):
    item_id = forms.CharField(
        label="ID",
        required=False,
        help_text="Можно не трогать: система сама выдаст короткий ID вроде GF-001.",
    )
    name = forms.CharField(label="Название", help_text="Например: Камера Sony, Петличка Rode, Штатив Manfrotto.")
    asset_type = forms.CharField(label="Тип", help_text="Например: камера, микрофон, штатив, свет.")
    initial_holder = forms.ModelChoiceField(
        label="Где лежит сейчас",
        queryset=Holder.objects.none(),
    )
    condition = forms.ChoiceField(
        label="Состояние",
        choices=AssetCurrentState.Condition.choices,
        initial=AssetCurrentState.Condition.WORKING,
    )
    create_print_batch = forms.BooleanField(
        label="Сразу создать пакет печати QR",
        required=False,
        initial=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["initial_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.LOCATION,
            is_active=True,
        ).order_by("name")


class ReceiveStockAdminForm(forms.Form):
    to_holder = forms.ModelChoiceField(label="Куда принять", queryset=Holder.objects.none())
    quantity = forms.IntegerField(label="Количество", min_value=1)
    reason = forms.CharField(label="Причина", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.LOCATION,
            is_active=True,
        ).order_by("name")


class MoveStockAdminForm(forms.Form):
    from_holder = forms.ModelChoiceField(label="Откуда", queryset=Holder.objects.none())
    to_holder = forms.ModelChoiceField(label="Куда", queryset=Holder.objects.none())
    quantity = forms.IntegerField(label="Количество", min_value=1)
    reason = forms.CharField(label="Причина", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, sku=None, **kwargs):
        super().__init__(*args, **kwargs)
        source_holders = Holder.objects.filter(
            stock_balances__sku=sku,
            stock_balances__quantity__gt=0,
        ).distinct()
        self.fields["from_holder"].queryset = source_holders.order_by("name")
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.LOCATION,
            is_active=True,
        ).order_by("name")


class IssueStockAdminForm(forms.Form):
    from_holder = forms.ModelChoiceField(label="Откуда", queryset=Holder.objects.none())
    to_holder = forms.ModelChoiceField(
        label="Кому выдано",
        queryset=Holder.objects.none(),
        required=False,
        help_text="Можно оставить пустым, если мерч списывается без конкретного получателя.",
    )
    quantity = forms.IntegerField(label="Количество", min_value=1)
    reason = forms.CharField(label="Причина", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, sku=None, **kwargs):
        super().__init__(*args, **kwargs)
        source_holders = Holder.objects.filter(
            stock_balances__sku=sku,
            stock_balances__quantity__gt=0,
        ).distinct()
        self.fields["from_holder"].queryset = source_holders.order_by("name")
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.PERSON,
            is_active=True,
        ).order_by("name")


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    form = PersonAdminForm
    list_display = ("display_name", "role", "is_active", "pin_reset_required")
    list_filter = ("role", "is_active")
    search_fields = ("display_name", "email")
    readonly_fields = ("pin_hash", "pin_updated_at", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        ensure_person_holder(obj)


@admin.register(Holder)
class HolderAdmin(admin.ModelAdmin):
    list_display = ("name", "holder_type", "linked_person", "can_take_by_qr", "is_active")
    list_filter = ("holder_type", "is_self_service_source", "is_active", "is_temporary")
    search_fields = ("name", "linked_person__display_name")

    @admin.display(boolean=True, description="Можно брать по QR")
    def can_take_by_qr(self, obj):
        return obj.is_self_service_source


class AssetQrCodeInline(admin.TabularInline):
    model = AssetQrCode
    extra = 0
    readonly_fields = ("code_value", "payload_url", "label_text", "created_at", "revoked_at")


@admin.register(AssetItem)
class AssetItemAdmin(admin.ModelAdmin):
    list_display = ("item_id", "name", "asset_type", "current_holder", "current_condition")
    search_fields = ("item_id", "name", "asset_type")
    list_filter = ("asset_type",)
    fields = ("item_id", "name", "asset_type", "registered_event", "created_at", "created_by")
    readonly_fields = ("item_id", "registered_event", "created_at", "created_by")
    inlines = [AssetQrCodeInline]
    change_list_template = "admin/inventory/assetitem/change_list.html"
    actions = ["create_print_batch_for_assets", "retire_assets"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("current_state__holder")

    @admin.display(description="Где сейчас")
    def current_holder(self, obj):
        try:
            return obj.current_state.holder.name
        except AssetCurrentState.DoesNotExist:
            return "-"

    @admin.display(description="Состояние")
    def current_condition(self, obj):
        try:
            return obj.current_state.get_condition_display()
        except AssetCurrentState.DoesNotExist:
            return "-"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "register/",
                self.admin_site.admin_view(self.register_view),
                name="inventory_assetitem_register",
            ),
        ]
        return custom_urls + urls

    def register_view(self, request):
        if request.method == "POST":
            form = RegisterAssetAdminForm(request.POST)
            if form.is_valid():
                try:
                    asset = register_asset(
                        item_id=form.cleaned_data["item_id"] or None,
                        name=form.cleaned_data["name"],
                        asset_type=form.cleaned_data["asset_type"],
                        initial_holder=form.cleaned_data["initial_holder"],
                        condition=form.cleaned_data["condition"],
                        created_by=request.user,
                    )
                    if form.cleaned_data["create_print_batch"]:
                        batch = create_qr_print_batch(
                            qr_codes=[active_qr_for_asset(asset)],
                            title=f"QR для {asset.item_id}",
                            created_by=request.user,
                        )
                        messages.success(request, f"Оборудование {asset.item_id} зарегистрировано.")
                        return redirect("admin:inventory_qrprintbatch_print", batch_id=batch.pk)
                except DomainError as exc:
                    form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
                else:
                    messages.success(request, f"Оборудование {asset.item_id} зарегистрировано.")
                    return redirect("admin:inventory_assetitem_change", object_id=asset.pk)
        else:
            form = RegisterAssetAdminForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Зарегистрировать оборудование",
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/inventory/assetitem/register.html", context)

    @admin.action(description="Создать пакет печати QR для выбранных объектов")
    def create_print_batch_for_assets(self, request, queryset):
        qr_codes = []
        missing_qr = []
        for asset in queryset.prefetch_related("qr_codes"):
            try:
                qr_codes.append(active_qr_for_asset(asset))
            except AssetQrCode.DoesNotExist:
                missing_qr.append(asset.item_id)
        if missing_qr:
            self.message_user(
                request,
                f"У объектов без активного QR пакет не создан: {', '.join(missing_qr)}",
                level=messages.ERROR,
            )
            return None
        try:
            batch = create_qr_print_batch(
                qr_codes=qr_codes,
                title=f"Пакет от {timezone.localtime().strftime('%Y-%m-%d %H:%M')}",
                created_by=request.user,
            )
        except DomainError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return None
        return HttpResponseRedirect(reverse("admin:inventory_qrprintbatch_print", args=[batch.pk]))

    @admin.action(description="Списать выбранное оборудование")
    def retire_assets(self, request, queryset):
        retired_count = 0
        errors = []
        for asset in queryset:
            try:
                retire_asset(asset=asset, reason="Списано через админку", recorded_by_admin=request.user)
            except DomainError as exc:
                errors.append(exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                retired_count += 1
        if retired_count:
            self.message_user(request, f"Списано объектов: {retired_count}", level=messages.SUCCESS)
        if errors:
            self.message_user(request, " ".join(errors), level=messages.ERROR)
        return None


@admin.register(AssetCurrentState)
class AssetCurrentStateAdmin(admin.ModelAdmin):
    list_display = ("item", "holder", "condition", "lifecycle_status", "due_on")
    list_filter = ("condition", "lifecycle_status", "holder")
    search_fields = ("item__item_id", "item__name", "holder__name")
    readonly_fields = ("item", "holder", "condition", "lifecycle_status", "due_on", "checkout_reason", "last_event")


@admin.register(OverdueAssetCurrentState)
class OverdueAssetCurrentStateAdmin(admin.ModelAdmin):
    list_display = ("item", "holder", "due_on", "checkout_reason")
    search_fields = ("item__item_id", "item__name", "holder__name", "checkout_reason")
    readonly_fields = ("item", "holder", "condition", "lifecycle_status", "due_on", "checkout_reason", "last_event")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("item", "holder")
            .filter(
                holder__holder_type=Holder.Type.PERSON,
                lifecycle_status=AssetCurrentState.LifecycleStatus.ACTIVE,
                due_on__lt=timezone.localdate(),
            )
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssetQrCode)
class AssetQrCodeAdmin(admin.ModelAdmin):
    list_display = ("label_text", "item", "status", "payload_url", "created_at")
    list_filter = ("status",)
    search_fields = ("label_text", "item__item_id", "item__name")


class AssetKitItemInline(admin.TabularInline):
    model = AssetKitItem
    extra = 1
    autocomplete_fields = ("item",)


class AssetKitQrCodeInline(admin.TabularInline):
    model = AssetKitQrCode
    extra = 0
    readonly_fields = ("code_value", "payload_url", "label_text", "created_at", "revoked_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AssetKit)
class AssetKitAdmin(admin.ModelAdmin):
    list_display = ("kit_id", "name", "status", "items_count", "print_link")
    list_filter = ("status",)
    search_fields = ("kit_id", "name", "items__item_id", "items__name")
    fields = ("kit_id", "name", "description", "status", "created_at", "created_by")
    readonly_fields = ("created_at", "created_by")
    inlines = [AssetKitItemInline, AssetKitQrCodeInline]

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not obj.kit_id:
            obj.kit_id = generate_kit_id()
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if not obj.qr_codes.filter(status=AssetKitQrCode.Status.ACTIVE).exists():
            create_qr_for_kit(obj)

    @admin.display(description="Предметов")
    def items_count(self, obj):
        return obj.items.count()

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:kit_id>/print/",
                self.admin_site.admin_view(self.print_view),
                name="inventory_assetkit_print",
            ),
        ]
        return custom_urls + urls

    def print_link(self, obj):
        try:
            active_qr_for_kit(obj)
        except AssetKitQrCode.DoesNotExist:
            return "-"
        url = reverse("admin:inventory_assetkit_print", args=[obj.pk])
        return format_html('<a href="{}">Печать QR</a>', url)

    print_link.short_description = "QR"

    def print_view(self, request, kit_id):
        kit = get_object_or_404(AssetKit, pk=kit_id)
        qr_code = active_qr_for_kit(kit)
        context = {
            **self.admin_site.each_context(request),
            "title": f"Печать QR: {kit}",
            "kit": kit,
            "qr_code": qr_code,
            "data_uri": qr_data_uri(qr_code.payload_url),
        }
        return render(request, "admin/inventory/assetkit/print.html", context)


class QrPrintBatchItemInline(admin.TabularInline):
    model = QrPrintBatchItem
    extra = 0


@admin.register(QrPrintBatch)
class QrPrintBatchAdmin(admin.ModelAdmin):
    list_display = ("batch_title", "status", "created_at", "printed_at", "print_link")
    list_filter = ("status",)
    inlines = [QrPrintBatchItemInline]

    @admin.display(description="Пакет")
    def batch_title(self, obj):
        return str(obj)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:batch_id>/print/",
                self.admin_site.admin_view(self.print_view),
                name="inventory_qrprintbatch_print",
            ),
        ]
        return custom_urls + urls

    def print_link(self, obj):
        url = reverse("admin:inventory_qrprintbatch_print", args=[obj.pk])
        return format_html('<a href="{}">Печать</a>', url)

    print_link.short_description = "Печатный лист"

    def print_view(self, request, batch_id):
        batch = get_object_or_404(QrPrintBatch, pk=batch_id)
        batch.status = QrPrintBatch.Status.PRINTED
        batch.printed_at = batch.printed_at or timezone.now()
        batch.save(update_fields=["status", "printed_at"])
        items = [
            {
                "qr_code": batch_item.qr_code,
                "data_uri": qr_data_uri(batch_item.qr_code.payload_url),
            }
            for batch_item in batch.qrprintbatchitem_set.select_related("qr_code", "qr_code__item")
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": f"Печать QR: {batch}",
            "batch": batch,
            "items": items,
        }
        return render(request, "admin/inventory/qrprintbatch/print.html", context)


class StockBalanceInline(admin.TabularInline):
    model = StockBalance
    extra = 0
    readonly_fields = ("holder", "quantity", "updated_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockSku)
class StockSkuAdmin(admin.ModelAdmin):
    list_display = ("sku_id", "name", "category", "total_quantity", "receive_link", "move_link", "issue_link")
    list_filter = ("category", "is_active")
    search_fields = ("sku_id", "name", "category")
    fields = ("sku_id", "name", "category", "unit", "is_active", "created_at", "created_by")
    readonly_fields = ("created_at", "created_by")
    inlines = [StockBalanceInline]

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not obj.sku_id:
            obj.sku_id = generate_sku_id()
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Остаток")
    def total_quantity(self, obj):
        return obj.balances.aggregate(total=Sum("quantity"))["total"] or 0

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:sku_id>/receive/",
                self.admin_site.admin_view(self.receive_view),
                name="inventory_stocksku_receive",
            ),
            path(
                "<path:sku_id>/move/",
                self.admin_site.admin_view(self.move_view),
                name="inventory_stocksku_move",
            ),
            path(
                "<path:sku_id>/issue/",
                self.admin_site.admin_view(self.issue_view),
                name="inventory_stocksku_issue",
            ),
        ]
        return custom_urls + urls

    def receive_link(self, obj):
        url = reverse("admin:inventory_stocksku_receive", args=[obj.pk])
        return format_html('<a href="{}">Поступление</a>', url)

    receive_link.short_description = "Принять"

    def move_link(self, obj):
        url = reverse("admin:inventory_stocksku_move", args=[obj.pk])
        return format_html('<a href="{}">Переместить</a>', url)

    move_link.short_description = "Переместить"

    def issue_link(self, obj):
        url = reverse("admin:inventory_stocksku_issue", args=[obj.pk])
        return format_html('<a href="{}">Выдать/списать</a>', url)

    issue_link.short_description = "Выдать"

    def receive_view(self, request, sku_id):
        sku = get_object_or_404(StockSku, pk=sku_id)
        if request.method == "POST":
            form = ReceiveStockAdminForm(request.POST)
            if form.is_valid():
                try:
                    receive_stock(
                        sku=sku,
                        to_holder=form.cleaned_data["to_holder"],
                        quantity=form.cleaned_data["quantity"],
                        reason=form.cleaned_data["reason"],
                        recorded_by_admin=request.user,
                    )
                except DomainError as exc:
                    form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
                else:
                    messages.success(request, "Поступление мерча записано.")
                    return redirect("admin:inventory_stocksku_change", object_id=sku.pk)
        else:
            form = ReceiveStockAdminForm()
        return self._render_stock_form(request, sku, form, "Поступление мерча")

    def move_view(self, request, sku_id):
        sku = get_object_or_404(StockSku, pk=sku_id)
        if request.method == "POST":
            form = MoveStockAdminForm(request.POST, sku=sku)
            if form.is_valid():
                try:
                    move_stock(
                        sku=sku,
                        from_holder=form.cleaned_data["from_holder"],
                        to_holder=form.cleaned_data["to_holder"],
                        quantity=form.cleaned_data["quantity"],
                        reason=form.cleaned_data["reason"],
                        recorded_by_admin=request.user,
                    )
                except DomainError as exc:
                    form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
                else:
                    messages.success(request, "Перемещение мерча записано.")
                    return redirect("admin:inventory_stocksku_change", object_id=sku.pk)
        else:
            form = MoveStockAdminForm(sku=sku)
        return self._render_stock_form(request, sku, form, "Перемещение мерча")

    def issue_view(self, request, sku_id):
        sku = get_object_or_404(StockSku, pk=sku_id)
        if request.method == "POST":
            form = IssueStockAdminForm(request.POST, sku=sku)
            if form.is_valid():
                try:
                    issue_stock(
                        sku=sku,
                        from_holder=form.cleaned_data["from_holder"],
                        to_holder=form.cleaned_data["to_holder"],
                        quantity=form.cleaned_data["quantity"],
                        reason=form.cleaned_data["reason"],
                        recorded_by_admin=request.user,
                    )
                except DomainError as exc:
                    form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
                else:
                    messages.success(request, "Выдача/списание мерча записано.")
                    return redirect("admin:inventory_stocksku_change", object_id=sku.pk)
        else:
            form = IssueStockAdminForm(sku=sku)
        return self._render_stock_form(request, sku, form, "Выдача или списание мерча")

    def _render_stock_form(self, request, sku, form, title):
        context = {
            **self.admin_site.each_context(request),
            "title": f"{title}: {sku}",
            "form": form,
            "sku": sku,
            "opts": self.model._meta,
        }
        return render(request, "admin/inventory/stocksku/stock_form.html", context)


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("sku", "holder", "quantity", "updated_at")
    list_filter = ("holder", "sku")
    search_fields = ("sku__sku_id", "sku__name", "holder__name")
    readonly_fields = ("sku", "holder", "quantity", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AssetEventItemInline(admin.TabularInline):
    model = AssetEventItem
    extra = 0
    readonly_fields = [field.name for field in AssetEventItem._meta.fields]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class AssetAuditObservationInline(admin.TabularInline):
    model = AssetAuditObservation
    extra = 0
    readonly_fields = [field.name for field in AssetAuditObservation._meta.fields]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class EventEvidenceInline(admin.TabularInline):
    model = EventEvidence
    extra = 0
    readonly_fields = [field.name for field in EventEvidence._meta.fields]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class StockEventItemInline(admin.TabularInline):
    model = StockEventItem
    extra = 0
    readonly_fields = [field.name for field in StockEventItem._meta.fields]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type_label", "recorded_at", "recorded_by_person", "recorded_by_admin", "reason")
    list_filter = ("event_type", "recorded_at")
    search_fields = ("reason", "comment", "payload")
    readonly_fields = [field.name for field in EventLog._meta.fields]
    inlines = [AssetEventItemInline, AssetAuditObservationInline, StockEventItemInline, EventEvidenceInline]

    @admin.display(description="Тип события", ordering="event_type")
    def event_type_label(self, obj):
        return obj.get_event_type_display()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
