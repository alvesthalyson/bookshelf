from django.shortcuts import render  # <-- IMPORT MOVIDO PARA O TOPO
from django.db.models import Avg, Count, F
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .filters import NullsLastOrderingFilter
from .models import Author, Book, Review
from .permissions import ReadOrCreateForAnyone
from .serializers import AuthorSerializer, BookSerializer, ReviewSerializer


class AuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.prefetch_related("books")
    serializer_class = AuthorSerializer
    filterset_fields = ["country"]
    search_fields = ["name"]
    ordering_fields = ["name", "birth_year"]


class BookViewSet(viewsets.ModelViewSet):
    queryset = (
        Book.objects.select_related("author")
        .annotate(avg_rating=Avg("reviews__rating"))
        .order_by("-published_year", "title")
    )
    serializer_class = BookSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, NullsLastOrderingFilter]
    filterset_fields = {
        "author": ["exact"],
        "in_stock": ["exact"],
        "genre": ["exact"],
        "author__country": ["exact"],
        "published_year": ["exact", "gte", "lte"],
    }
    search_fields = ["title", "author__name", "genre"]
    ordering_fields = ["published_year", "title", "created_at", "avg_rating"]


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.select_related("book")
    serializer_class = ReviewSerializer
    permission_classes = [ReadOrCreateForAnyone]
    filterset_fields = ["book"]
    ordering_fields = ["rating", "created_at"]


@api_view(["GET"])
@permission_classes([AllowAny])
def stats(request):
    """Numbers for the dashboard on the home page."""
    books = Book.objects.all()
    total = books.count()
    in_stock = books.filter(in_stock=True).count()

    top = (
        Book.objects.select_related("author")
        .annotate(avg_rating=Avg("reviews__rating"), review_count=Count("reviews"))
        .filter(review_count__gt=0)
        .order_by("-avg_rating", "-review_count", "title")
        .first()
    )
    overall = Review.objects.aggregate(avg=Avg("rating"))["avg"]

    by_country = (
        books.exclude(author__country="")
        .values(country=F("author__country"))
        .annotate(count=Count("id"))
        .order_by("-count", "country")
    )
    by_genre = (
        books.exclude(genre="")
        .values("genre")
        .annotate(count=Count("id"))
        .order_by("-count", "genre")
    )

    return Response({
        "books": total,
        "in_stock": in_stock,
        "checked_out": total - in_stock,
        "authors": Author.objects.count(),
        "reviews": Review.objects.count(),
        "average_rating": round(overall, 1) if overall is not None else None,
        "top_rated": None if top is None else {
            "id": top.id,
            "title": top.title,
            "author_name": top.author.name,
            "average_rating": round(top.avg_rating, 1),
            "review_count": top.review_count,
        },
        "by_country": list(by_country),
        "by_genre": list(by_genre),
    })


# --- A SUA VIEW DA PÁGINA INICIAL FICA AQUI, FORA DAS CLASSES ---
@ensure_csrf_cookie  # the page posts reviews with fetch and needs the CSRF cookie
def home(request):
    return render(request, 'home.html')
