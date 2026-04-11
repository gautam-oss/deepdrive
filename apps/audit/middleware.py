"""
Audit middleware — automatically logs login/logout events and
attaches request context (IP, user agent, request ID) so all
audit log entries written during a request have full context.

Sensitive model-level actions (patient record view, appointment
changes) are logged explicitly in the service layer via AuditLogger.
"""
import threading
import uuid

import structlog
from django.utils.deprecation import MiddlewareMixin

logger = structlog.get_logger(__name__)

# Thread-local storage for current request context
_request_context = threading.local()


def get_current_request_context() -> dict:
    """Return audit context for the current request thread."""
    return getattr(_request_context, "context", {})


class AuditMiddleware(MiddlewareMixin):
    """
    Attaches audit context to every request and logs auth events.
    Must run after AuthenticationMiddleware so request.user is available.
    """

    def process_request(self, request):
        request_id = str(uuid.uuid4())
        request.request_id = request_id

        ip = self._get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        # Store in thread-local for use by AuditLogger in service layer
        _request_context.context = {
            "request_id": request_id,
            "ip_address": ip,
            "user_agent": user_agent,
        }

        # Bind to structlog context so every log in this request includes it
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            ip=ip,
        )

    def process_response(self, request, response):
        structlog.contextvars.clear_contextvars()
        _request_context.context = {}
        return response

    @staticmethod
    def _get_client_ip(request) -> str:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            # Take the first (leftmost) IP — closest to client
            return forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")
