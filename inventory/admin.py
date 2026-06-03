from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

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
    validate_pin,
)
from .services import (
    DomainError,
    active_qr_for_asset,
    create_qr_print_batch,
    ensure_person_holder,
    qr_data_uri,
    register_asset,
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
    list_display = ("name", "holder_type", "linked_person", "is_self_service_source", "is_active")
    list_filter = ("holder_type", "is_self_service_source", "is_active", "is_temporary")
    search_fields = ("name", "linked_person__display_name")


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
    actions = ["create_print_batch_for_assets"]

    def has_add_permission(self, request):
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


@admin.register(AssetCurrentState)
class AssetCurrentStateAdmin(admin.ModelAdmin):
    list_display = ("item", "holder", "condition", "lifecycle_status", "due_on")
    list_filter = ("condition", "lifecycle_status", "holder")
    search_fields = ("item__item_id", "item__name", "holder__name")
    readonly_fields = ("item", "holder", "condition", "lifecycle_status", "due_on", "checkout_reason", "last_event")


@admin.register(AssetQrCode)
class AssetQrCodeAdmin(admin.ModelAdmin):
    list_display = ("label_text", "item", "status", "payload_url", "created_at")
    list_filter = ("status",)
    search_fields = ("label_text", "item__item_id", "item__name")


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


class AssetEventItemInline(admin.TabularInline):
    model = AssetEventItem
    extra = 0
    readonly_fields = [field.name for field in AssetEventItem._meta.fields]
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


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type_label", "recorded_at", "recorded_by_person", "recorded_by_admin", "reason")
    list_filter = ("event_type", "recorded_at")
    search_fields = ("reason", "comment", "payload")
    readonly_fields = [field.name for field in EventLog._meta.fields]
    inlines = [AssetEventItemInline, EventEvidenceInline]

    @admin.display(description="Тип события", ordering="event_type")
    def event_type_label(self, obj):
        return obj.get_event_type_display()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
