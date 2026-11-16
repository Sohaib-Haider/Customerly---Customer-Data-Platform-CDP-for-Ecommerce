from django.urls import path

from .views import Customer360View, CustomerHistoryView, CustomerListView, CustomerOrderView

app_name = "customer360"
urlpatterns = [
    path("customers/", CustomerListView.as_view(), name="customer-list"),
    path("customers/<uuid:customer_id>/360/", Customer360View.as_view(), name="profile"),
    path("customers/<uuid:customer_id>/orders/<uuid:order_id>/", CustomerOrderView.as_view(), name="order-detail"),
    *[path(f"customers/<uuid:customer_id>/{kind}/", CustomerHistoryView.as_view(),
           {"kind": kind}, name=kind) for kind in ("orders", "events", "messages", "coupons")],
]
