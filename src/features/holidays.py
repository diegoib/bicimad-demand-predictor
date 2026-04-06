"""Madrid public holiday lookup for 2024-2027.

Uses the `holidays` package for Spain (prov="MD") which covers:
- National Spanish holidays
- Comunidad de Madrid additions (May 2, Easter Thursday/Friday)

Nov 9 (Almudena, Madrid city holiday) is added manually as it is not
included in the Comunidad de Madrid province calendar.
"""

from datetime import date, datetime

import holidays as hols

_MADRID_HOLIDAYS: hols.HolidayBase = hols.Spain(prov="MD", years=range(2024, 2028))  # type: ignore[attr-defined]

# Nov 9 (Almudena) is a Madrid city holiday not included in the province calendar
for _year in range(2024, 2028):
    _MADRID_HOLIDAYS[date(_year, 11, 9)] = "Nuestra Señora de la Almudena"


def is_holiday(dt: date | datetime) -> bool:
    """Return True if dt is a Madrid public holiday (2024-2027).

    Args:
        dt: A date or datetime object to check.

    Returns:
        True if the date is a public holiday in Madrid, False otherwise.
    """
    if isinstance(dt, datetime):
        dt = dt.date()
    return dt in _MADRID_HOLIDAYS
