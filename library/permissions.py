from rest_framework.permissions import SAFE_METHODS, BasePermission


class ReadOrCreateForAnyone(BasePermission):
    """Anyone can read and post a review; editing or deleting needs a logged-in user."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS or request.method == "POST":
            return True
        return bool(request.user and request.user.is_authenticated)
