from datetime import date

from rest_framework import serializers

from .models import Author, Book, Loan, Reservation, Review


class BookInAuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "published_year"]


class AuthorSerializer(serializers.ModelSerializer):
    books = BookInAuthorSerializer(many=True, read_only=True)
    book_count = serializers.IntegerField(source="books.count", read_only=True)

    class Meta:
        model = Author
        fields = ["id", "name", "country", "birth_year", "book_count", "books", "created_at"]
        read_only_fields = ["id", "created_at"]


class BookSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.name", read_only=True)
    average_rating = serializers.SerializerMethodField()
    due_at = serializers.SerializerMethodField()
    queue_length = serializers.SerializerMethodField()
    held_until = serializers.SerializerMethodField()
    favorited = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = [
            "id", "title", "author", "author_name", "published_year",
            "isbn", "in_stock", "genre", "pages", "description", "cover_url",
            "average_rating", "due_at", "queue_length", "held_until", "favorited", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_average_rating(self, obj):
        value = getattr(obj, "avg_rating", None)
        return round(value, 1) if value is not None else None

    def get_due_at(self, obj):
        """When the current loan ends, if the book is checked out. Never says who has it."""
        value = getattr(obj, "active_due", None)
        return value.isoformat() if value is not None else None

    def get_queue_length(self, obj):
        """How many readers are in line for the book, including the one it is held for."""
        return int(getattr(obj, "queue", 0) or 0)

    def get_held_until(self, obj):
        """When the hold on a returned book ends. It never says who the book is held for."""
        value = getattr(obj, "held", None)
        return value.isoformat() if value is not None else None

    def get_favorited(self, obj):
        """True when the reader who is asking saved this book. Always false for visitors."""
        return bool(getattr(obj, "fav", False))

    def validate_published_year(self, value):
        if value > date.today().year:
            raise serializers.ValidationError("published_year cannot be in the future.")
        return value

    def validate_pages(self, value):
        if value is not None and value < 1:
            raise serializers.ValidationError("pages must be at least 1.")
        return value

    def validate_isbn(self, value):
        if not value:
            return None
        if not value.isdigit() or len(value) not in (10, 13):
            raise serializers.ValidationError("ISBN must be 10 or 13 digits.")
        return value


class RelatedBookSerializer(serializers.Serializer):
    """A short card for the recommendations under a book."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    author_name = serializers.CharField(source="author.name")
    published_year = serializers.IntegerField()
    isbn = serializers.CharField(allow_null=True)
    cover_url = serializers.CharField(allow_blank=True)
    in_stock = serializers.BooleanField()
    average_rating = serializers.SerializerMethodField()
    reason = serializers.CharField()

    def get_average_rating(self, obj):
        value = getattr(obj, "avg_rating", None)
        return round(value, 1) if value is not None else None


class ReviewSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)

    class Meta:
        model = Review
        fields = ["id", "book", "book_title", "reviewer_name", "rating", "comment", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("rating must be between 1 and 5.")
        return value


class LoanSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)
    author_name = serializers.CharField(source="book.author.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_late = serializers.IntegerField(read_only=True)
    fine = serializers.IntegerField(read_only=True)
    fine_is_unpaid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Loan
        fields = [
            "id", "book", "book_title", "author_name", "username", "borrowed_at",
            "due_at", "returned_at", "renewals", "is_overdue",
            "days_late", "fine", "fine_is_unpaid", "fine_paid_at",
        ]
        read_only_fields = ["id", "borrowed_at", "due_at", "returned_at", "renewals", "fine_paid_at"]
        # The one-active-loan-per-book rule is checked in services.create_loan (and by the database),
        # so DRF does not need to build its own unique validator from the constraint.
        validators = []


class ReservationSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)
    author_name = serializers.CharField(source="book.author.name", read_only=True)
    position = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            "id", "book", "book_title", "author_name", "status", "position",
            "created_at", "ready_at", "expires_at",
        ]
        read_only_fields = ["id", "status", "created_at", "ready_at", "expires_at"]
        # The one-active-reservation rule is checked in services.reserve (and by the database).
        validators = []

    def get_position(self, obj):
        """Place in line, starting at 1. Empty once the reservation is over."""
        if not obj.is_active:
            return None
        ahead = Reservation.objects.filter(
            book=obj.book_id, status__in=Reservation.ACTIVE, id__lt=obj.id
        ).count()
        return ahead + 1
