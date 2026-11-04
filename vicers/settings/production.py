from .base import *
from decouple import config

DEBUG = False

ALLOWED_HOSTS = ['api.vicers.net', 'www.api.vicers.net']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT', default='5432'),
    }
}

CORS_ALLOWED_ORIGINS = [
    "https://vicegamers.com",
    "https://www.vicegamers.com",
    "https://vicecreators.com",
    "https://www.vicecreators.com",
]

CSRF_TRUSTED_ORIGINS = [
    'https://api.vicers.net',
    'https://www.api.vicers.net',
]

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ]
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
