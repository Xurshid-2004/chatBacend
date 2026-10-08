from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler


def _first_message(errors):
    """Pick the first human-readable message out of nested DRF errors."""
    if isinstance(errors, dict):
        ordered = [errors['non_field_errors']] if 'non_field_errors' in errors else []
        ordered += [value for key, value in errors.items() if key != 'non_field_errors']
        for value in ordered:
            message = _first_message(value)
            if message:
                return message
        return None
    if isinstance(errors, (list, tuple)):
        for value in errors:
            message = _first_message(value)
            if message:
                return message
        return None
    return str(errors) if errors else None


def api_exception_handler(exc, context):
    """
    Give every API error the same shape:
        {"detail": "<message to show>", "code": "<machine code>", "errors": {field: [...]}}
    `errors` is only present for validation errors.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, ValidationError):
        errors = response.data if isinstance(response.data, dict) else {'non_field_errors': response.data}
        response.data = {
            'detail': _first_message(errors) or 'Invalid input.',
            'code': 'invalid',
            'errors': errors,
        }
        return response

    detail = getattr(exc, 'detail', None)
    message = response.data.get('detail') if isinstance(response.data, dict) else None
    response.data = {
        'detail': str(message or detail or 'Request failed.'),
        'code': getattr(detail, 'code', None) or getattr(exc, 'default_code', 'error'),
    }
    return response
