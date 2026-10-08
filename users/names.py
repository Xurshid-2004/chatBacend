"""Usernames for accounts created on the start screen (from the chosen name)."""

import re
import secrets
import unicodedata

ADJECTIVES = [
    "Brave", "Bright", "Calm", "Cheerful", "Clever", "Cosy", "Eager", "Gentle", "Happy", "Jolly",
    "Kind", "Lucky", "Merry", "Proud", "Quick", "Quiet", "Sunny", "Swift", "Witty", "Bold",
]
ANIMALS = [
    "Otter", "Panda", "Fox", "Koala", "Tiger", "Falcon", "Dolphin", "Owl", "Rabbit", "Penguin",
    "Lynx", "Bear", "Hedgehog", "Swan", "Wolf", "Deer", "Seal", "Robin", "Turtle", "Whale",
]

# Uzbek / Russian Cyrillic to Latin, so "Алишер" becomes "alisher".
CYRILLIC = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j', 'з': 'z',
    'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h',
}


def guest_identity():
    """Return (display name, username) made of a random adjective and animal."""
    adjective = secrets.choice(ADJECTIVES)
    animal = secrets.choice(ANIMALS)
    number = 1000 + secrets.randbelow(9000)
    return f'{adjective} {animal}', f'{adjective}_{animal}_{number}'.lower()


def username_base(name):
    """Latin letters/digits from a display name, or None when nothing usable is left."""
    text = ''.join(CYRILLIC.get(char, char) for char in name.lower())
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    text = re.sub(r'^[^a-z]+', '', text)[:20].strip('_')
    return text if len(text) >= 2 else None


def username_candidates(name, attempts=10):
    """Usernames to try for `name`: "alisher_4821"; random ones if the name has no Latin form."""
    base = username_base(name)
    for _ in range(attempts):
        if base:
            yield f'{base}_{1000 + secrets.randbelow(9000)}'
        else:
            yield guest_identity()[1]
