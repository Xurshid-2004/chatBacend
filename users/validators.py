from django.core.validators import RegexValidator
from django.utils.deconstruct import deconstructible


@deconstructible
class UsernameValidator(RegexValidator):
    regex = r'^[A-Za-z][A-Za-z0-9_]{2,31}\Z'
    message = (
        'Username must be 3-32 characters long, start with a letter and contain '
        'only letters, digits and underscores.'
    )
    flags = 0
