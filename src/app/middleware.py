from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

from app.providers import services


class HtmxLoginRedirectMiddleware:
    """Intercept login redirects for HTMX requests and use HX-Redirect instead.

    When a user's session expires, Django's LoginRequiredMiddleware returns a 302
    redirect to the login page. For normal requests this works fine, but HTMX
    silently follows the redirect and swaps the login page HTML into a partial target,
    resulting in broken content with no user notification.

    This middleware detects that scenario and converts it to an HX-Redirect header,
    which HTMX natively handles by performing a full-page navigation to the login page.
    """

    def __init__(self, get_response):
        """Initialize the middleware with the get_response callable."""
        self.get_response = get_response

    def __call__(self, request):
        """Process the request and convert login redirects for HTMX requests."""
        response = self.get_response(request)

        if (
            request.headers.get("HX-Request")
            and response.status_code in (301, 302)
            and hasattr(response, "url")
        ):
            redirect_url = response.url
            login_url = getattr(settings, "LOGIN_URL", "account_login")
            if login_url and (
                f"/{login_url}" in redirect_url or "/accounts/login" in redirect_url
            ):
                new_response = HttpResponse(status=200)
                new_response["HX-Redirect"] = redirect_url
                return new_response

        return response


class ProviderAPIErrorMiddleware:
    """Middleware to handle ProviderAPIError exceptions."""

    def __init__(self, get_response):
        """Initialize the middleware with the get_response callable."""
        self.get_response = get_response

    def __call__(self, request):
        """Process the request and handle exceptions."""
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Handle exceptions raised during request processing."""
        if isinstance(exception, services.ProviderAPIError):
            return render(
                request,
                "500.html",
                {
                    "error_message": str(exception),
                    "provider": exception.provider,
                },
                status=500,
            )
        return None
