from django.urls import path

from . import views


app_name = "inventory"

urlpatterns = [
    path("", views.index, name="index"),
    path("i/<str:item_id>", views.asset_detail, name="asset_detail"),
    path("i/<str:item_id>/checkout", views.checkout_asset, name="checkout_asset"),
    path("i/<str:item_id>/return", views.return_asset, name="return_asset"),
    path("overdue", views.overdue_list, name="overdue_list"),
]
