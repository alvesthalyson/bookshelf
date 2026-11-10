from django.contrib import admin

from .models import Author, Book, Review


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "birth_year"]


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "published_year", "in_stock"]
    list_filter = ["in_stock"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["book", "reviewer_name", "rating", "created_at"]
