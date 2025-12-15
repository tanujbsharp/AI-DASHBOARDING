"""
Django settings for AI Dashboarding Engine.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from parent directory's .env
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR.parent / '.env'
load_dotenv(ENV_PATH)

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'dashboarding',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# No database needed - we use OpenSearch
DATABASES = {}

# Static files
STATIC_URL = 'static/'

# CORS settings
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
    ],
    'UNAUTHENTICATED_USER': None,
}

# AWS Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'anthropic.claude-3-sonnet-20240229-v1:0')
USE_LLM = os.getenv('USE_LLM', 'true').lower() == 'true'

# OpenSearch Configuration
OPENSEARCH_DOMAIN_ENDPOINT = os.getenv('OPENSEARCH_DOMAIN_ENDPOINT')
OPENSEARCH_MASTER_USER = os.getenv('OPENSEARCH_MASTER_USER')
OPENSEARCH_MASTER_PASS = os.getenv('OPENSEARCH_MASTER_PASS')

# Index Configuration - Using only module_consumption_data
OPENSEARCH_INDEXES = {
    'module_consumption_data': os.getenv('MODULE_CONSUMPTION_DATA', 'converse_lm_consumption_summary_reports_prod'),
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

