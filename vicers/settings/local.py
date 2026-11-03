from .base import *
from decouple import config

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1']

CORS_ALLOWED_ORIGINS = [
    "https://vicegamers.com",
    "https://vicecreators.com",
    "http://localhost",
    "http://localhost:63343",
    "http://127.0.0.1",
    "null",
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Vice Arcade is served to the browser from Discord's proxy, not from our own
# domain, so its origin has to be allowed here. Built from the application id
# rather than written out, so the two can never drift apart. Left out entirely
# when the id is unset, which is how the bot and website deployments stay
# unaffected.
#
# [Unverified] whether requests through Discord's proxy carry an Origin header at
# all. Allowing it costs nothing either way and is checked against a real request
# in Phase 1a.
VICE_ARCADE_CLIENT_ID = config('VICE_ARCADE_CLIENT_ID', default='')
if VICE_ARCADE_CLIENT_ID:
    CORS_ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS + [
        f'https://{VICE_ARCADE_CLIENT_ID}.discordsays.com',
    ]
