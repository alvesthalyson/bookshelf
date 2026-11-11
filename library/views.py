from django.shortcuts import render  # <-- IMPORT MOVIDO PARA O TOPO
from django.db.models import Avg, BooleanField, Count, Exists, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .filters import NullsLastOrderingFilter
from . import services
from .models import Author, Book, Favorite, Loan, Reservation, Review
from .permissions import IsStaffOrReadOnly, ReadOrCreateForAnyone
from .serializers import (
    AuthorSerializer, BookSerializer, LoanSerializer, RelatedBookSerializer, ReservationSerializer,
    ReviewSerializer,
)


class AuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.prefetch_related("books")
    serializer_class = AuthorSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_fields = ["country"]
    search_fields = ["name"]
    ordering_fields = ["name", "birth_year"]


class BookViewSet(viewsets.ModelViewSet):
    queryset = (
        Book.objects.select_related("author")
        .annotate(
            avg_rating=Avg("reviews__rating"),
            # when the current loan ends, so the page can say "back on Nov 24"
            active_due=Subquery(
                Loan.objects.filter(book=OuterRef("pk"), returned_at__isnull=True).values("due_at")[:1]
            ),
            # readers in line for the book, and when the hold on it ends if it is being held
            queue=Coalesce(
                Subquery(
                    Reservation.objects.filter(book=OuterRef("pk"), status__in=Reservation.ACTIVE)
                    .order_by().values("book").annotate(n=Count("pk")).values("n")[:1]
                ),
                0,
                output_field=IntegerField(),
            ),
            held=Subquery(
                Reservation.objects.filter(book=OuterRef("pk"), status=Reservation.READY).values("expires_at")[:1]
            ),
        )
        .order_by("-published_year", "title")
    )
    serializer_class = BookSerializer
    permission_classes = [IsStaffOrReadOnly]
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

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated:
            qs = qs.annotate(fav=Exists(Favorite.objects.filter(book=OuterRef("pk"), user=user)))
        else:
            qs = qs.annotate(fav=Value(False, output_field=BooleanField()))
        # ?favorites=true shows only the books the reader saved (nothing for visitors)
        if self.request.query_params.get("favorites") == "true":
            qs = qs.filter(fav=True)
        return qs

    @action(detail=True, methods=["post", "delete"], permission_classes=[IsAuthenticated])
    def favorite(self, request, pk=None):
        """POST saves the book to the wish list, DELETE removes it. Both can be repeated safely."""
        book = self.get_object()
        saved = services.set_favorite(request.user, book, request.method == "POST")
        return Response({"favorited": saved})

    @action(detail=True, methods=["get"])
    def related(self, request, pk=None):
        """Books to read next: what other readers borrowed too, then the same author and genre."""
        book = self.get_object()
        items = []
        for other, reason in services.recommendations(book):
            other.reason = reason
            items.append(other)
        return Response(RelatedBookSerializer(items, many=True).data)

    # Holds that ran out are released when someone looks at the books (there is no background job).
    def list(self, request, *args, **kwargs):
        services.expire_due_holds()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        services.expire_due_holds()
        return super().retrieve(request, *args, **kwargs)


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.select_related("book")
    serializer_class = ReviewSerializer
    permission_classes = [ReadOrCreateForAnyone]
    filterset_fields = ["book"]
    ordering_fields = ["rating", "created_at"]


class LoanViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Borrow, list, return and renew. Regular users only see their own loans."""

    serializer_class = LoanSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["book"]
    ordering_fields = ["borrowed_at", "due_at"]

    def get_queryset(self):
        qs = Loan.objects.select_related("book__author", "user")
        user = self.request.user
        params = self.request.query_params
        # Staff see every loan, unless they ask for their own with ?mine=true.
        if not user.is_staff or params.get("mine") == "true":
            qs = qs.filter(user=user)
        active = params.get("active")
        if active == "true":
            qs = qs.filter(returned_at__isnull=True)
        elif active == "false":
            qs = qs.filter(returned_at__isnull=False)
        return qs

    @staticmethod
    def _error(exc):
        return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            loan = services.create_loan(request.user, serializer.validated_data["book"])
        except services.LoanError as exc:
            return self._error(exc)
        return Response(self.get_serializer(loan).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="return")
    def return_book(self, request, pk=None):
        loan = self.get_object()  # 404 for loans that are not yours
        try:
            loan = services.return_loan(loan.pk, request.user)
        except services.LoanError as exc:
            return self._error(exc)
        return Response(self.get_serializer(loan).data)

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        loan = self.get_object()
        try:
            loan = services.renew_loan(loan.pk, request.user)
        except services.LoanError as exc:
            return self._error(exc)
        return Response(self.get_serializer(loan).data)


class ReservationViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Join a line, list your places in line and leave a line. Regular users only see their own."""

    serializer_class = ReservationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["book"]

    def get_queryset(self):
        qs = Reservation.objects.select_related("book__author", "user")
        user = self.request.user
        params = self.request.query_params
        if not user.is_staff or params.get("mine") == "true":
            qs = qs.filter(user=user)
        active = params.get("active")
        if active == "true":
            qs = qs.filter(status__in=Reservation.ACTIVE)
        elif active == "false":
            qs = qs.exclude(status__in=Reservation.ACTIVE)
        return qs

    @staticmethod
    def _error(exc):
        return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)

    def list(self, request, *args, **kwargs):
        services.expire_due_holds()
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reservation = services.reserve(request.user, serializer.validated_data["book"])
        except services.LoanError as exc:
            return self._error(exc)
        return Response(self.get_serializer(reservation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        reservation = self.get_object()  # 404 for reservations that are not yours
        try:
            reservation = services.cancel_reservation(reservation.pk, request.user)
        except services.LoanError as exc:
            return self._error(exc)
        return Response(self.get_serializer(reservation).data)


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
