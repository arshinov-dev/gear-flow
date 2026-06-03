from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import CheckoutForm, ReturnForm
from .models import AssetItem, Holder
from .services import DomainError, checkout_asset_by_qr, overdue_assets, return_asset_by_qr


def asset_page_context(asset, checkout_form=None, return_form=None):
    state = asset.current_state
    can_checkout = (
        state.holder.holder_type == Holder.Type.LOCATION
        and state.holder.is_self_service_source
        and state.lifecycle_status == state.LifecycleStatus.ACTIVE
    )
    can_return = state.holder.holder_type == Holder.Type.PERSON
    return {
        "asset": asset,
        "state": state,
        "checkout_form": checkout_form or CheckoutForm(),
        "return_form": return_form or ReturnForm(),
        "can_checkout": can_checkout,
        "can_return": can_return,
    }


def asset_detail(request, item_id):
    asset = get_object_or_404(AssetItem.objects.select_related("current_state__holder"), item_id=item_id)
    return render(request, "inventory/asset_detail.html", asset_page_context(asset))


def checkout_asset(request, item_id):
    asset = get_object_or_404(AssetItem.objects.select_related("current_state__holder"), item_id=item_id)
    if request.method != "POST":
        return redirect("inventory:asset_detail", item_id=item_id)

    form = CheckoutForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            checkout_asset_by_qr(
                item_id=item_id,
                person=form.cleaned_data["person"],
                pin=form.cleaned_data["pin"],
                due_on=form.cleaned_data["due_on"],
                reason=form.cleaned_data["reason"],
                condition_after=form.cleaned_data.get("condition_after") or "",
                problem_photo=form.cleaned_data.get("problem_photo"),
            )
        except DomainError as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Оборудование выдано.")
            return redirect("inventory:asset_detail", item_id=item_id)
    return render(request, "inventory/asset_detail.html", asset_page_context(asset, checkout_form=form))


def return_asset(request, item_id):
    asset = get_object_or_404(AssetItem.objects.select_related("current_state__holder"), item_id=item_id)
    if request.method != "POST":
        return redirect("inventory:asset_detail", item_id=item_id)

    form = ReturnForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            return_asset_by_qr(
                item_id=item_id,
                person=form.cleaned_data["person"],
                pin=form.cleaned_data["pin"],
                to_holder=form.cleaned_data["to_holder"],
                condition_after=form.cleaned_data.get("condition_after") or "",
                problem_photo=form.cleaned_data.get("problem_photo"),
            )
        except DomainError as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Оборудование возвращено.")
            return redirect("inventory:asset_detail", item_id=item_id)
    return render(request, "inventory/asset_detail.html", asset_page_context(asset, return_form=form))


@staff_member_required
def overdue_list(request):
    states = overdue_assets()
    today = timezone.localdate()
    rows = [
        {
            "state": state,
            "days_overdue": (today - state.due_on).days,
        }
        for state in states
    ]
    return render(request, "inventory/overdue_list.html", {"rows": rows})


def index(request):
    return redirect(reverse("admin:index"))
