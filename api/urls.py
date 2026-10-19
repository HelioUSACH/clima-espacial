"""Both URL sets live outside i18n_patterns: data and HAPI are
language-neutral (climaespacial/urls.py)."""

from django.urls import path, re_path

from . import hapi, views

hapi_patterns = ([
    path("hapi", hapi.landing, name="landing"),
    path("hapi/", hapi.landing),
    path("hapi/about", hapi.about, name="about"),
    path("hapi/capabilities", hapi.capabilities, name="capabilities"),
    path("hapi/catalog", hapi.catalog, name="catalog"),
    path("hapi/info", hapi.info, name="info"),
    path("hapi/data", hapi.data, name="data"),
    re_path(r"^hapi/(?P<rest>.*)$", hapi.unknown_endpoint),
], "hapi")

api_patterns = ([
    path("datos/<str:product_id>.csv", views.download_csv, name="download_csv"),
    path("pronosticos/<str:product_id>.json", views.forecast_export, {"fmt": "json"}, name="forecast_json"),
    path("pronosticos/<str:product_id>.csv", views.forecast_export, {"fmt": "csv"}, name="forecast_csv"),
], "api")
