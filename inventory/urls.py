from django.urls import path

from . import views


app_name = "inventory"

urlpatterns = [
    path("", views.index, name="index"),
    path("i/<str:item_id>", views.asset_detail, name="asset_detail"),
    path("i/<str:item_id>/checkout", views.checkout_asset, name="checkout_asset"),
    path("i/<str:item_id>/return", views.return_asset, name="return_asset"),
    path("k/<str:kit_id>", views.kit_detail, name="kit_detail"),
    path("k/<str:kit_id>/checkout", views.checkout_kit, name="checkout_kit"),
    path("k/<str:kit_id>/return", views.return_kit, name="return_kit"),
    path("overdue", views.overdue_list, name="overdue_list"),
    path("work/", views.work_dashboard, name="work_dashboard"),
    path("work/login", views.work_login, name="work_login"),
    path("work/logout", views.work_logout, name="work_logout"),
    path("work/assets", views.work_assets, name="work_assets"),
    path("work/assets/<str:item_id>/action", views.work_asset_action, name="work_asset_action"),
    path("work/kits", views.work_kits, name="work_kits"),
    path("work/audit", views.work_audit_locations, name="work_audit_locations"),
    path("work/audit/<int:holder_id>", views.work_audit_location, name="work_audit_location"),
    path("work/overdue", views.work_overdue, name="work_overdue"),
    path("work/stock", views.work_stock, name="work_stock"),
    path("work/stock/<str:sku_id>/receive", views.work_stock_receive, name="work_stock_receive"),
    path("work/stock/<str:sku_id>/move", views.work_stock_move, name="work_stock_move"),
    path("work/stock/<str:sku_id>/issue", views.work_stock_issue, name="work_stock_issue"),
    path("work/stock/<str:sku_id>/adjust", views.work_stock_adjust, name="work_stock_adjust"),
]
