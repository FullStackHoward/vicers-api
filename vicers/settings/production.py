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

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ]
}