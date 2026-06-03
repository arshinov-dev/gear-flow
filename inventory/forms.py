from django import forms
from django.utils import timezone

from .models import AssetCurrentState, Holder, Person


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
