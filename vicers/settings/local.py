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

# The arcade's Vite dev server, for running the page against a local API.
CORS_ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS + [
    'http://localhost:5173',
]
