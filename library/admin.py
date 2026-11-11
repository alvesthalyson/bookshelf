from django.contrib import admin

from .models import Author, Book, Loan, Review


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "birth_year"]


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "genre", "published_year", "pages", "in_stock"]
    list_filter = ["in_stock", "genre"]
    search_fields = ["title", "author__name"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["book", "reviewer_name", "rating", "created_at"]


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ["book", "user", "borrowed_at", "due_at", "returned_at", "renewals"]
    list_filter = ["returned_at"]
    search_fields = ["book__title", "user__username"]
