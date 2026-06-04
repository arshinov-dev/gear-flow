import re

from django import forms
from django.utils import timezone

from .models import AssetCurrentState, AssetItem, Holder, Person


PROBLEM_CONDITION_CHOICES = [
    ("", "Выберите состояние"),
    (AssetCurrentState.Condition.NEEDS_ATTENTION, "Требует внимания"),
    (AssetCurrentState.Condition.BROKEN, "Сломано"),
    (AssetCurrentState.Condition.UNKNOWN, "Неизвестно"),
]


class CheckoutForm(forms.Form):
    person = forms.ModelChoiceField(
        label="Кто берет",
        queryset=Person.objects.none(),
    )
    pin = forms.CharField(label="PIN", max_length=4, widget=forms.PasswordInput)
    due_on = forms.DateField(
        label="Дата возврата",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    reason = forms.CharField(label="Причина", widget=forms.Textarea(attrs={"rows": 3}))
    has_problem = forms.BooleanField(label="Есть проблема с состоянием", required=False)
    condition_after = forms.ChoiceField(
        label="Новое состояние",
        required=False,
        choices=PROBLEM_CONDITION_CHOICES,
    )
    problem_photo = forms.ImageField(label="Фото проблемы", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.filter(is_active=True).order_by("display_name")

    def clean_due_on(self):
        due_on = self.cleaned_data["due_on"]
        if due_on < timezone.localdate():
            raise forms.ValidationError("Дата возврата не может быть в прошлом.")
        return due_on

    def clean(self):
        cleaned_data = super().clean()
        has_problem = cleaned_data.get("has_problem")
        condition_after = cleaned_data.get("condition_after")
        if has_problem and not condition_after:
            self.add_error("condition_after", "Выберите состояние проблемы.")
        if not has_problem:
            cleaned_data["condition_after"] = ""
            cleaned_data["problem_photo"] = None
        return cleaned_data


class ReturnForm(forms.Form):
    person = forms.ModelChoiceField(
        label="Кто возвращает",
        queryset=Person.objects.none(),
    )
    pin = forms.CharField(label="PIN", max_length=4, widget=forms.PasswordInput)
    to_holder = forms.ModelChoiceField(
        label="Куда вернуть",
        queryset=Holder.objects.none(),
    )
    has_problem = forms.BooleanField(label="Есть проблема с состоянием", required=False)
    condition_after = forms.ChoiceField(
        label="Новое состояние",
        required=False,
        choices=PROBLEM_CONDITION_CHOICES,
    )
    problem_photo = forms.ImageField(label="Фото проблемы", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.filter(is_active=True).order_by("display_name")
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.LOCATION,
            is_active=True,
        ).order_by("name")

    def clean(self):
        cleaned_data = super().clean()
        has_problem = cleaned_data.get("has_problem")
        condition_after = cleaned_data.get("condition_after")
        if has_problem and not condition_after:
            self.add_error("condition_after", "Выберите состояние проблемы.")
        if not has_problem:
            cleaned_data["condition_after"] = ""
            cleaned_data["problem_photo"] = None
        return cleaned_data


class ManagerLoginForm(forms.Form):
    person = forms.ModelChoiceField(
        label="Ответственный",
        queryset=Person.objects.none(),
    )
    pin = forms.CharField(label="PIN", max_length=4, widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.filter(is_active=True).order_by("display_name")

    def clean(self):
        cleaned_data = super().clean()
        person = cleaned_data.get("person")
        pin = cleaned_data.get("pin")
        if person and pin:
            if not person.check_pin(pin):
                raise forms.ValidationError("Неверный PIN-код.")
            if not person.can_manage_inventory:
                raise forms.ValidationError("Нужна роль администратора или ответственного за учет.")
        return cleaned_data


class LocationAuditForm(forms.Form):
    observed_items = forms.ModelMultipleChoiceField(
        label="Что найдено на месте",
        queryset=AssetItem.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    unexpected_item_ids = forms.CharField(
        label="Найденные ID не из списка",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="По одному ID в строке или через запятую. Например: GF-014, GF-020.",
    )
    comment = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, holder=None, **kwargs):
        super().__init__(*args, **kwargs)
        expected_assets = AssetItem.objects.filter(current_state__holder=holder).order_by("item_id")
        self.fields["observed_items"].queryset = expected_assets
        if not self.is_bound:
            self.initial["observed_items"] = list(expected_assets.values_list("pk", flat=True))

    def clean_unexpected_item_ids(self):
        raw_value = self.cleaned_data["unexpected_item_ids"]
        return [item_id for item_id in re.split(r"[\s,]+", raw_value.strip()) if item_id]


class AssetActionForm(forms.Form):
    ACTION_LOST = "lost"
    ACTION_REPAIR = "repair"
    ACTION_RETURN_FROM_REPAIR = "return_from_repair"
    ACTION_CORRECTION = "correction"
    ACTION_RETIRE = "retire"

    ACTION_CHOICES = [
        (ACTION_LOST, "Потеряно"),
        (ACTION_REPAIR, "Отправить в ремонт"),
        (ACTION_RETURN_FROM_REPAIR, "Вернуть из ремонта"),
        (ACTION_CORRECTION, "Коррекция места/состояния"),
        (ACTION_RETIRE, "Списать"),
    ]

    action = forms.ChoiceField(label="Действие", choices=ACTION_CHOICES)
    holder = forms.ModelChoiceField(label="Место или внешний держатель", queryset=Holder.objects.none(), required=False)
    condition = forms.ChoiceField(
        label="Состояние",
        choices=[("", "Не менять")] + list(AssetCurrentState.Condition.choices),
        required=False,
    )
    lifecycle_status = forms.ChoiceField(
        label="Статус",
        choices=[("", "Не менять")] + list(AssetCurrentState.LifecycleStatus.choices),
        required=False,
    )
    reason = forms.CharField(label="Причина", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["holder"].queryset = Holder.objects.filter(is_active=True).order_by("holder_type", "name")

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get("action")
        holder = cleaned_data.get("holder")
        condition = cleaned_data.get("condition")
        lifecycle_status = cleaned_data.get("lifecycle_status")
        if action == self.ACTION_REPAIR and (not holder or holder.holder_type != Holder.Type.EXTERNAL):
            self.add_error("holder", "Выберите внешнего держателя для ремонта.")
        if action == self.ACTION_RETURN_FROM_REPAIR and (not holder or holder.holder_type != Holder.Type.LOCATION):
            self.add_error("holder", "Выберите место хранения.")
        if action == self.ACTION_RETURN_FROM_REPAIR and not condition:
            self.add_error("condition", "Выберите состояние после ремонта.")
        if action == self.ACTION_CORRECTION and not any([holder, condition, lifecycle_status]):
            self.add_error("action", "Для коррекции выберите новое место, состояние или статус.")
        return cleaned_data


class StockReceiveForm(forms.Form):
    to_holder = forms.ModelChoiceField(label="Куда принять", queryset=Holder.objects.none())
    quantity = forms.IntegerField(label="Количество", min_value=1)
    reason = forms.CharField(label="Причина", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.LOCATION,
            is_active=True,
        ).order_by("name")


class StockMoveForm(forms.Form):
    from_holder = forms.ModelChoiceField(label="Откуда", queryset=Holder.objects.none())
    to_holder = forms.ModelChoiceField(label="Куда", queryset=Holder.objects.none())
    quantity = forms.IntegerField(label="Количество", min_value=1)
    reason = forms.CharField(label="Причина", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, sku=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["from_holder"].queryset = Holder.objects.filter(
            stock_balances__sku=sku,
            stock_balances__quantity__gt=0,
        ).distinct().order_by("name")
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.LOCATION,
            is_active=True,
        ).order_by("name")


class StockIssueForm(forms.Form):
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
        self.fields["from_holder"].queryset = Holder.objects.filter(
            stock_balances__sku=sku,
            stock_balances__quantity__gt=0,
        ).distinct().order_by("name")
        self.fields["to_holder"].queryset = Holder.objects.filter(
            holder_type=Holder.Type.PERSON,
            is_active=True,
        ).order_by("name")


class StockAdjustForm(forms.Form):
    holder = forms.ModelChoiceField(label="Где проверено", queryset=Holder.objects.none())
    actual_quantity = forms.IntegerField(label="Фактическое количество", min_value=0)
    reason = forms.CharField(label="Причина", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, sku=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["holder"].queryset = Holder.objects.filter(is_active=True).order_by("holder_type", "name")
