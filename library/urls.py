from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import auth_views, views

router = DefaultRouter()
router.register("authors", views.AuthorViewSet)
router.register("books", views.BookViewSet)
router.register("reviews", views.ReviewViewSet)
router.register("loans", views.LoanViewSet, basename="loan")
router.register("reservations", views.ReservationViewSet, basename="reservation")

urlpatterns = [
    path("stats/", views.stats, name="stats"),
    path("staff/overview/", views.staff_overview, name="staff-overview"),
    path("auth/me/", auth_views.me, name="auth-me"),
    path("auth/login/", auth_views.login_view, name="auth-login"),
    path("auth/logout/", auth_views.logout_view, name="auth-logout"),
    path("auth/register/", auth_views.register_view, name="auth-register"),
    path("", include(router.urls)),
    path("home/", views.home, name="home"),
]

