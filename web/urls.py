from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("productos/", views.catalogue, name="catalogue"),
    path("productos/<str:product_id>/", views.product_detail, name="product_detail"),
    path("productos/<str:product_id>/serie.json", views.product_series_json, name="product_series_json"),
    path("que-ofrecemos/", views.que_ofrecemos, name="que_ofrecemos"),
    path("estado/", views.status, name="status"),
    path("acerca/", views.about, name="about"),
    path("datos-api/", views.data_api, name="data_api"),
    path("datos-api/gracias/", views.data_api_thanks, name="data_api_thanks"),
]
