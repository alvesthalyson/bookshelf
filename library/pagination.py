from rest_framework.pagination import PageNumberPagination


class ShelfPagination(PageNumberPagination):
    """12 per page, like PAGE_SIZE. The shelf view of the home page asks for more with ?page_size=, up to 48."""
    page_size = 12
    page_size_query_param = "page_size"
    max_page_size = 48
