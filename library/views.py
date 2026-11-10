from django.shortcuts import render  # <-- IMPORT MOVIDO PARA O TOPO
from django.db.models import Avg
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import SearchFilter

from .filters import NullsLastOrderingFilter
from .models import Author, Book, Review
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
        "published_year": ["exact", "gte", "lte"],
    }
    search_fields = ["title", "author__name"]
    ordering_fields = ["published_year", "title", "created_at", "avg_rating"]


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.select_related("book")
    serializer_class = ReviewSerializer
    filterset_fields = ["book"]
    ordering_fields = ["rating", "created_at"]


# --- A SUA VIEW DA PÁGINA INICIAL FICA AQUI, FORA DAS CLASSES ---
def home(request):
    return render(request, 'home.html')
