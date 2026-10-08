from django.http import JsonResponse

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


class ApiCsrfHeaderMiddleware:
    """
    CSRF protection for the cookie-authenticated API (the "custom request
    header" pattern): state-changing /api/ requests must send
    `X-Requested-With: XMLHttpRequest`. A cross-site page cannot add that
    header without a CORS preflight, and this backend never approves one.
    Requests that carry an Authorization header are not cookie-based and pass.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.startswith('/api/')
            and request.method not in SAFE_METHODS
            and 'HTTP_AUTHORIZATION' not in request.META
            and request.headers.get('X-Requested-With') != 'XMLHttpRequest'
        ):
            return JsonResponse(
                {'detail': 'CSRF check failed: missing X-Requested-With header.', 'code': 'csrf_failed'},
                status=403,
            )
        return self.get_response(request)
