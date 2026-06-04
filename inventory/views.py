from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    CheckoutForm,
    AssetActionForm,
    LocationAuditForm,
    ManagerLoginForm,
    ReturnForm,
    StockIssueForm,
    StockMoveForm,
    StockReceiveForm,
    StockAdjustForm,
)
from .models import AssetAuditObservation, AssetCurrentState, AssetItem, AssetKit, Holder, Person, StockSku
from .services import (
    DomainError,
    checkout_asset_by_qr,
    checkout_kit_by_qr,
    adjust_stock_balance,
    correct_asset_state,
    issue_stock,
    mark_asset_lost,
    move_stock,
    overdue_assets,
    receive_stock,
    record_location_audit,
    retire_asset,
    return_asset_by_qr,
    return_asset_from_repair,
    return_kit_by_qr,
    send_asset_to_repair,
)


MANAGER_SESSION_KEY = "inventory_manager_person_id"


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


def kit_page_context(kit, checkout_form=None, return_form=None):
    kit_items = list(kit.kit_items.select_related("item").order_by("position_index", "id"))
    item_ids = [kit_item.item_id for kit_item in kit_items]
    states = (
        AssetCurrentState.objects.select_related("item", "holder")
        .filter(item_id__in=item_ids)
        .order_by("item_id")
    )
    state_by_item_id = {state.item_id: state for state in states}
    rows = [
        {
            "kit_item": kit_item,
            "state": state_by_item_id.get(kit_item.item_id),
        }
        for kit_item in kit_items
    ]
    can_checkout = (
        kit.status == AssetKit.Status.ACTIVE
        and bool(rows)
        and all(
            row["state"]
            and row["state"].lifecycle_status == AssetCurrentState.LifecycleStatus.ACTIVE
            and row["state"].holder.holder_type == Holder.Type.LOCATION
            and row["state"].holder.is_self_service_source
            for row in rows
        )
    )
    person_holder_ids = {
        row["state"].holder_id
        for row in rows
        if row["state"] and row["state"].holder.holder_type == Holder.Type.PERSON
    }
    can_return = bool(rows) and len(person_holder_ids) == 1 and all(
        row["state"] and row["state"].holder_id in person_holder_ids for row in rows
    )
    return {
        "kit": kit,
        "rows": rows,
        "checkout_form": checkout_form or CheckoutForm(),
        "return_form": return_form or ReturnForm(),
        "can_checkout": can_checkout,
        "can_return": can_return,
    }


def kit_detail(request, kit_id):
    kit = get_object_or_404(AssetKit.objects.prefetch_related("kit_items__item"), kit_id=kit_id)
    return render(request, "inventory/kit_detail.html", kit_page_context(kit))


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


def checkout_kit(request, kit_id):
    kit = get_object_or_404(AssetKit.objects.prefetch_related("kit_items__item"), kit_id=kit_id)
    if request.method != "POST":
        return redirect("inventory:kit_detail", kit_id=kit_id)

    form = CheckoutForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            checkout_kit_by_qr(
                kit_id=kit_id,
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
            messages.success(request, "Комплект выдан.")
            return redirect("inventory:kit_detail", kit_id=kit_id)
    return render(request, "inventory/kit_detail.html", kit_page_context(kit, checkout_form=form))


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


def return_kit(request, kit_id):
    kit = get_object_or_404(AssetKit.objects.prefetch_related("kit_items__item"), kit_id=kit_id)
    if request.method != "POST":
        return redirect("inventory:kit_detail", kit_id=kit_id)

    form = ReturnForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            return_kit_by_qr(
                kit_id=kit_id,
                person=form.cleaned_data["person"],
                pin=form.cleaned_data["pin"],
                to_holder=form.cleaned_data["to_holder"],
                condition_after=form.cleaned_data.get("condition_after") or "",
                problem_photo=form.cleaned_data.get("problem_photo"),
            )
        except DomainError as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        else:
            messages.success(request, "Комплект возвращен.")
            return redirect("inventory:kit_detail", kit_id=kit_id)
    return render(request, "inventory/kit_detail.html", kit_page_context(kit, return_form=form))


def current_manager(request):
    person_id = request.session.get(MANAGER_SESSION_KEY)
    if not person_id:
        return None
    try:
        person = Person.objects.get(pk=person_id, is_active=True)
    except Person.DoesNotExist:
        request.session.pop(MANAGER_SESSION_KEY, None)
        return None
    if not person.can_manage_inventory:
        request.session.pop(MANAGER_SESSION_KEY, None)
        return None
    return person


def require_manager(request):
    manager = current_manager(request)
    if manager:
        return manager
    return None


def work_login(request):
    if current_manager(request):
        return redirect("inventory:work_dashboard")
    if request.method == "POST":
        form = ManagerLoginForm(request.POST)
        if form.is_valid():
            request.session[MANAGER_SESSION_KEY] = form.cleaned_data["person"].pk
            messages.success(request, "Вход выполнен.")
            return redirect("inventory:work_dashboard")
    else:
        form = ManagerLoginForm()
    return render(request, "inventory/work/login.html", {"form": form})


def work_logout(request):
    request.session.pop(MANAGER_SESSION_KEY, None)
    messages.success(request, "Вы вышли из рабочего кабинета.")
    return redirect("inventory:work_login")


def work_dashboard(request):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    overdue_count = overdue_assets().count()
    locations_count = Holder.objects.filter(holder_type=Holder.Type.LOCATION, is_active=True).count()
    stock_count = StockSku.objects.filter(is_active=True).count()
    assets_count = AssetItem.objects.count()
    kits_count = AssetKit.objects.filter(status=AssetKit.Status.ACTIVE).count()
    return render(
        request,
        "inventory/work/dashboard.html",
        {
            "manager": manager,
            "overdue_count": overdue_count,
            "locations_count": locations_count,
            "stock_count": stock_count,
            "assets_count": assets_count,
            "kits_count": kits_count,
        },
    )


def work_assets(request):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    status = request.GET.get("status", "")
    states = AssetCurrentState.objects.select_related("item", "holder").order_by("item_id")
    if status:
        states = states.filter(lifecycle_status=status)
    return render(
        request,
        "inventory/work/assets.html",
        {
            "manager": manager,
            "states": states,
            "status": status,
            "status_choices": AssetCurrentState.LifecycleStatus.choices,
        },
    )


def work_asset_action(request, item_id):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    asset = get_object_or_404(AssetItem.objects.select_related("current_state__holder"), item_id=item_id)
    if request.method == "POST":
        form = AssetActionForm(request.POST)
        if form.is_valid():
            try:
                action = form.cleaned_data["action"]
                if action == AssetActionForm.ACTION_LOST:
                    mark_asset_lost(asset=asset, reason=form.cleaned_data["reason"], recorded_by_person=manager)
                elif action == AssetActionForm.ACTION_REPAIR:
                    send_asset_to_repair(
                        asset=asset,
                        repair_holder=form.cleaned_data["holder"],
                        reason=form.cleaned_data["reason"],
                        recorded_by_person=manager,
                    )
                elif action == AssetActionForm.ACTION_RETURN_FROM_REPAIR:
                    return_asset_from_repair(
                        asset=asset,
                        to_holder=form.cleaned_data["holder"],
                        condition_after=form.cleaned_data["condition"],
                        reason=form.cleaned_data["reason"],
                        recorded_by_person=manager,
                    )
                elif action == AssetActionForm.ACTION_CORRECTION:
                    correct_asset_state(
                        asset=asset,
                        reason=form.cleaned_data["reason"],
                        recorded_by_person=manager,
                        to_holder=form.cleaned_data["holder"],
                        condition_after=form.cleaned_data["condition"],
                        lifecycle_status_after=form.cleaned_data["lifecycle_status"],
                    )
                elif action == AssetActionForm.ACTION_RETIRE:
                    retire_asset(asset=asset, reason=form.cleaned_data["reason"], recorded_by_person=manager)
            except DomainError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, "Действие записано.")
                return redirect("inventory:work_assets")
    else:
        form = AssetActionForm(
            initial={
                "holder": asset.current_state.holder_id,
                "condition": asset.current_state.condition,
                "lifecycle_status": asset.current_state.lifecycle_status,
            }
        )
    return render(
        request,
        "inventory/work/asset_action.html",
        {"manager": manager, "asset": asset, "state": asset.current_state, "form": form},
    )


def kit_status(kit):
    rows = []
    holder_ids = set()
    holder_names = set()
    has_unavailable = False
    for kit_item in kit.kit_items.all():
        state = getattr(kit_item.item, "current_state", None)
        rows.append({"kit_item": kit_item, "state": state})
        if not state or state.lifecycle_status != AssetCurrentState.LifecycleStatus.ACTIVE:
            has_unavailable = True
        if state:
            holder_ids.add(state.holder_id)
            holder_names.add(state.holder.name)
    if kit.status == AssetKit.Status.ARCHIVED:
        label = "В архиве"
        tone = "neutral"
    elif not rows:
        label = "Пустой"
        tone = "warning"
    elif has_unavailable:
        label = "Есть недоступные"
        tone = "danger"
    elif len(holder_ids) > 1:
        label = "Разъехался"
        tone = "danger"
    else:
        holder_name = next(iter(holder_names))
        label = f"Вместе: {holder_name}"
        tone = "ok"
    return {"kit": kit, "rows": rows, "label": label, "tone": tone, "is_split": len(holder_ids) > 1}


def work_kits(request):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    kits = AssetKit.objects.prefetch_related("kit_items__item__current_state__holder").order_by("kit_id")
    kit_rows = [kit_status(kit) for kit in kits]
    return render(request, "inventory/work/kits.html", {"manager": manager, "kit_rows": kit_rows})


def work_audit_locations(request):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    locations = Holder.objects.filter(holder_type=Holder.Type.LOCATION, is_active=True).order_by("name")
    return render(request, "inventory/work/audit_locations.html", {"manager": manager, "locations": locations})


def work_audit_location(request, holder_id):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    holder = get_object_or_404(Holder, pk=holder_id, holder_type=Holder.Type.LOCATION)
    if request.method == "POST":
        form = LocationAuditForm(request.POST, holder=holder)
        if form.is_valid():
            try:
                event = record_location_audit(
                    holder=holder,
                    auditor=manager,
                    observed_item_ids=form.cleaned_data["observed_items"].values_list("pk", flat=True),
                    unexpected_item_ids=form.cleaned_data["unexpected_item_ids"],
                    comment=form.cleaned_data["comment"],
                )
            except DomainError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                observations = event.asset_audit_observations.all()
                missing_count = observations.filter(result=AssetAuditObservation.Result.MISSING).count()
                unexpected_count = observations.filter(result=AssetAuditObservation.Result.UNEXPECTED_HOLDER).count()
                messages.success(
                    request,
                    f"Аудит записан. Не найдено: {missing_count}. Неожиданно найдено: {unexpected_count}.",
                )
                return redirect("inventory:work_audit_locations")
    else:
        form = LocationAuditForm(holder=holder)
    return render(
        request,
        "inventory/work/audit_location.html",
        {"manager": manager, "holder": holder, "form": form, "expected_count": form.fields["observed_items"].queryset.count()},
    )


def work_overdue(request):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    states = overdue_assets()
    holder_id = request.GET.get("holder", "")
    if holder_id:
        states = states.filter(holder_id=holder_id)
    holders = Holder.objects.filter(
        holder_type=Holder.Type.PERSON,
        current_assets__due_on__lt=timezone.localdate(),
        current_assets__lifecycle_status=AssetCurrentState.LifecycleStatus.ACTIVE,
    ).distinct().order_by("name")
    today = timezone.localdate()
    rows = [
        {
            "state": state,
            "days_overdue": (today - state.due_on).days,
        }
        for state in states
    ]
    return render(
        request,
        "inventory/work/overdue.html",
        {"manager": manager, "rows": rows, "holders": holders, "holder_id": holder_id},
    )


def work_stock(request):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    skus = (
        StockSku.objects.prefetch_related("balances__holder")
        .annotate(total_quantity=Sum("balances__quantity"))
        .order_by("sku_id")
    )
    return render(request, "inventory/work/stock.html", {"manager": manager, "skus": skus})


def work_stock_receive(request, sku_id):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    sku = get_object_or_404(StockSku, pk=sku_id)
    if request.method == "POST":
        form = StockReceiveForm(request.POST)
        if form.is_valid():
            try:
                receive_stock(
                    sku=sku,
                    to_holder=form.cleaned_data["to_holder"],
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data["reason"],
                )
            except DomainError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, "Поступление записано.")
                return redirect("inventory:work_stock")
    else:
        form = StockReceiveForm()
    return render(request, "inventory/work/stock_form.html", {"manager": manager, "sku": sku, "form": form, "title": "Поступление"})


def work_stock_move(request, sku_id):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    sku = get_object_or_404(StockSku, pk=sku_id)
    if request.method == "POST":
        form = StockMoveForm(request.POST, sku=sku)
        if form.is_valid():
            try:
                move_stock(
                    sku=sku,
                    from_holder=form.cleaned_data["from_holder"],
                    to_holder=form.cleaned_data["to_holder"],
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data["reason"],
                )
            except DomainError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, "Перемещение записано.")
                return redirect("inventory:work_stock")
    else:
        form = StockMoveForm(sku=sku)
    return render(request, "inventory/work/stock_form.html", {"manager": manager, "sku": sku, "form": form, "title": "Перемещение"})


def work_stock_issue(request, sku_id):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    sku = get_object_or_404(StockSku, pk=sku_id)
    if request.method == "POST":
        form = StockIssueForm(request.POST, sku=sku)
        if form.is_valid():
            try:
                issue_stock(
                    sku=sku,
                    from_holder=form.cleaned_data["from_holder"],
                    to_holder=form.cleaned_data["to_holder"],
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data["reason"],
                )
            except DomainError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, "Выдача/списание записано.")
                return redirect("inventory:work_stock")
    else:
        form = StockIssueForm(sku=sku)
    return render(request, "inventory/work/stock_form.html", {"manager": manager, "sku": sku, "form": form, "title": "Выдача или списание"})


def work_stock_adjust(request, sku_id):
    manager = require_manager(request)
    if not manager:
        return redirect("inventory:work_login")
    sku = get_object_or_404(StockSku, pk=sku_id)
    if request.method == "POST":
        form = StockAdjustForm(request.POST, sku=sku)
        if form.is_valid():
            try:
                adjust_stock_balance(
                    sku=sku,
                    holder=form.cleaned_data["holder"],
                    actual_quantity=form.cleaned_data["actual_quantity"],
                    reason=form.cleaned_data["reason"],
                    recorded_by_person=manager,
                )
            except DomainError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, "Остаток скорректирован.")
                return redirect("inventory:work_stock")
    else:
        form = StockAdjustForm(sku=sku)
    return render(request, "inventory/work/stock_form.html", {"manager": manager, "sku": sku, "form": form, "title": "Коррекция остатка"})


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
    return redirect(reverse("inventory:work_dashboard"))
