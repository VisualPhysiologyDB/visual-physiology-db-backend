"""Validation shared by admin, imports and public submissions."""
import calendar
import math
import re
from django.core.exceptions import ValidationError

MAX_FINITE = 1.7976931348623157e308


def positive_finite(value):
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValidationError('Use a finite number greater than zero.')


def partial_date(value):
    """ISO year, year-month, or full date; never manufacture missing parts."""
    if not value:
        return
    if not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value):
        raise ValidationError('Use YYYY, YYYY-MM, or YYYY-MM-DD.')
    parts = [int(p) for p in value.split('-')]
    if not 1 <= parts[0] <= 9999:
        raise ValidationError('Invalid year.')
    if len(parts) > 1 and not 1 <= parts[1] <= 12:
        raise ValidationError('Invalid month.')
    if len(parts) > 2 and not 1 <= parts[2] <= calendar.monthrange(*parts[:2])[1]:
        raise ValidationError('Invalid day.')
