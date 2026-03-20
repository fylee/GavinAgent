from django.core.paginator import Paginator


def paginate_queryset(queryset, page: int = 1, per_page: int = 20):
    """Helper to paginate a queryset."""
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(page)
    return page_obj
