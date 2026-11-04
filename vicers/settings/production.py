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

# Vice Arcade is a web page on its own subdomain that calls this API from the
# browser, so its origin has to be allowed. Sign-in happens through Discord's
# OAuth2 redirect, which never touches CORS; only these API calls do.
CORS_ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS + [
    'https://arcade.vicers.net',
]
