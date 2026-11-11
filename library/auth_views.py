"""Login, logout and sign-up for the web page. Plain Django views, so Django's CSRF check applies."""
import json
import re

from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from . import fines, services

USERNAME_RE = re.compile(r"^[\w.@+-]{3,30}$")


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _session(request):
    user = request.user
    data = {
        "authenticated": bool(user.is_authenticated),
        "username": user.get_username() if user.is_authenticated else None,
        "is_staff": bool(user.is_authenticated and user.is_staff),
        "rules": {
            "loan_days": services.loan_days(),
            "max_active_loans": services.max_active_loans(),
            "max_renewals": services.max_renewals(),
            "hold_days": services.hold_days(),
            "max_active_reservations": services.max_active_reservations(),
            "fine_per_day": fines.per_day(),
            "fine_max_per_loan": fines.max_per_loan(),
            "fine_currency": fines.currency(),
        },
        # unpaid: owed for books already returned late (blocks new loans). accruing: growing on books still out.
        "fines": {
            "unpaid": services.unpaid_fines_total(user) if user.is_authenticated else 0,
            "accruing": services.accruing_fines_total(user) if user.is_authenticated else 0,
        },
    }
    return data


def _error(detail, code, status=400, field_errors=None, error_codes=None):
    body = {"detail": detail, "code": code}
    if field_errors:
        body["field_errors"] = field_errors
    if error_codes:
        # Machine-readable version of field_errors, so the page can show the message in its own language.
        body["error_codes"] = error_codes
    return JsonResponse(body, status=status)


@require_GET
def me(request):
    return JsonResponse(_session(request))


@require_POST
def login_view(request):
    data = _body(request)
    user = authenticate(
        request,
        username=str(data.get("username", "")).strip(),
        password=str(data.get("password", "")),
    )
    if user is None:
        return _error("Wrong username or password.", "invalid_credentials")
    login(request, user)
    return JsonResponse(_session(request))


@require_POST
def logout_view(request):
    logout(request)
    return JsonResponse(_session(request))


@require_POST
def register_view(request):
    User = get_user_model()
    data = _body(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    errors = {}
    codes = {}

    if not USERNAME_RE.match(username):
        errors["username"] = ["Use 3 to 30 letters, numbers or . @ + - _ characters."]
        codes["username"] = [{"code": "username_invalid", "params": {}}]
    elif User.objects.filter(username__iexact=username).exists():
        errors["username"] = ["This username is taken."]
        codes["username"] = [{"code": "username_taken", "params": {}}]

    if not errors:
        try:
            password_validation.validate_password(password, user=User(username=username))
        except ValidationError as exc:
            errors["password"] = list(exc.messages)
            codes["password"] = [
                {"code": item.code, "params": item.params or {}} for item in exc.error_list
            ]

    if errors:
        return _error(
            "Check the form and try again.", "validation_error",
            field_errors=errors, error_codes=codes,
        )

    user = User.objects.create_user(username=username, password=password)
    login(request, user)
    return JsonResponse(_session(request), status=201)
