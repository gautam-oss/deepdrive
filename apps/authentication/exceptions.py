from rest_framework import status
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    """
    Strip internal model/stack detail from API error responses in production.
    Clients get a safe error code and message — no internal structure exposed.
    """
    response = exception_handler(exc, context)

    if response is not None:
        # Replace any detailed error body with a safe structure
        safe_data = {
            "error": {
                "code": response.status_code,
                "message": _safe_message(response.status_code),
            }
        }
        # Preserve field-level validation errors (400) — they're user-facing
        if response.status_code == status.HTTP_400_BAD_REQUEST and isinstance(response.data, dict):
            safe_data["error"]["fields"] = response.data

        response.data = safe_data

    return response


def _safe_message(status_code: int) -> str:
    messages = {
        400: "Invalid request.",
        401: "Authentication required.",
        403: "You do not have permission to perform this action.",
        404: "Resource not found.",
        405: "Method not allowed.",
        429: "Too many requests. Please try again later.",
        500: "An internal error occurred.",
    }
    return messages.get(status_code, "An error occurred.")
