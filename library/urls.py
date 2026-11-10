from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("authors", views.AuthorViewSet)
router.register("books", views.BookViewSet)
router.register("reviews", views.ReviewViewSet)

urlpatterns = [
    path("stats/", views.stats, name="stats"),
    path("", include(router.urls)),
    path("home/", views.home, name="home"),
]

