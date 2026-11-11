from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("productos/", views.catalogue, name="catalogue"),
    path("productos/<str:product_id>/", views.product_detail, name="product_detail"),
    # The flare page's own data, read server-side from flare-nrt (D-100).
    path("productos/usach.flare.nrt/datos/<slug:name>.json", views.flare_datos_json, name="flare_datos_json"),
    path("productos/<str:product_id>/serie.json", views.product_series_json, name="product_series_json"),
    path("productos/<str:product_id>/forecast.json", views.product_forecast_json, name="product_forecast_json"),
    path("que-ofrecemos/", views.que_ofrecemos, name="que_ofrecemos"),
    path("estado/", views.status, name="status"),
    path("acerca/", views.about, name="about"),
    path("datos-api/", views.data_api, name="data_api"),
]
