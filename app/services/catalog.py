"""
Campus reference data: activities, venues and meeting presets.

This is the "variable part" of the product: rolling out to another campus or city
means replacing these lists (or loading them from a registry), the core logic stays.
"""
from typing import NamedTuple, Optional

ACTIVITIES = [
    ("Волейбол", "🏐"),
    ("Футбол", "⚽"),
    ("Баскетбол", "🏀"),
    ("Бег", "🏃"),
    ("Велопрогулка", "🚴"),
    ("Настольный теннис", "🏓"),
    ("Другое", "🎯"),
]

OTHER_ACTIVITY = "Другое"


class Location(NamedTuple):
    name: str
    latitude: float
    longitude: float


# Pilot campus: Moscow State University (Leninskie Gory).
# Coordinates are taken from OpenStreetMap objects.
LOCATIONS = [
    Location("Волейбольная площадка у спорткомплекса МГУ", 55.698710, 37.538128),
    Location("Баскетбольная площадка у спорткомплекса МГУ", 55.698961, 37.537701),
    Location("Стадион МГУ, футбольное поле", 55.701006, 37.536577),
    Location("Спортивный трёхзальный корпус МГУ", 55.699740, 37.538099),
    Location("Смотровая площадка Воробьёвых гор", 55.709900, 37.541118),
    Location("Главное здание МГУ", 55.702910, 37.530755),
    Location("Воробьёвская набережная", 55.713941, 37.542203),
]

_LOCATIONS_BY_NAME = {loc.name: loc for loc in LOCATIONS}


def find_location(name: str) -> Optional[Location]:
    return _LOCATIONS_BY_NAME.get(name)


# Minutes offered as buttons after an hour is chosen (any other time can be typed)
MINUTE_OPTIONS = (0, 15, 30, 45)

# Quick choices of (min_quorum, max_participants); any other size can be typed
SIZE_PRESETS = [(2, 4), (4, 6), (6, 10), (8, 12), (10, 16)]
MIN_PARTICIPANTS = 2
MAX_PARTICIPANTS = 100

SKILL_LEVELS = ["Любой", "Любительский", "Средний", "Продвинутый"]

# How many days ahead a meeting can be scheduled
DAYS_AHEAD = 30

ACTIVITY_EMOJI = dict(ACTIVITIES)


def activity_emoji(activity_type: str) -> str:
    return ACTIVITY_EMOJI.get(activity_type, "🎯")
