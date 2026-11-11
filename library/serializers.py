from datetime import date

from rest_framework import serializers

from .models import Author, Book, Loan, Review


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

    class Meta:
        model = Book
        fields = [
            "id", "title", "author", "author_name", "published_year",
            "isbn", "in_stock", "genre", "pages", "description", "cover_url",
            "average_rating", "due_at", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_average_rating(self, obj):
        value = getattr(obj, "avg_rating", None)
        return round(value, 1) if value is not None else None

    def get_due_at(self, obj):
        """When the current loan ends, if the book is checked out. Never says who has it."""
        value = getattr(obj, "active_due", None)
        return value.isoformat() if value is not None else None

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
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Loan
        fields = [
            "id", "book", "book_title", "author_name", "borrowed_at",
            "due_at", "returned_at", "renewals", "is_overdue",
        ]
        read_only_fields = ["id", "borrowed_at", "due_at", "returned_at", "renewals"]
        # The one-active-loan-per-book rule is checked in services.create_loan (and by the database),
        # so DRF does not need to build its own unique validator from the constraint.
        validators = []
