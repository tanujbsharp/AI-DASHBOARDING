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

# Minimal database config (app doesn’t use a relational DB, but Django requires an ENGINE)
# Use a lightweight SQLite file so management commands (migrate, makemigrations) don’t fail.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

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
USE_CONVERSATION_CONTEXT = os.getenv('USE_CONVERSATION_CONTEXT', 'false').lower() == 'true'

# OpenSearch Configuration
OPENSEARCH_DOMAIN_ENDPOINT = os.getenv('OPENSEARCH_DOMAIN_ENDPOINT')
OPENSEARCH_MASTER_USER = os.getenv('OPENSEARCH_MASTER_USER')
OPENSEARCH_MASTER_PASS = os.getenv('OPENSEARCH_MASTER_PASS')

# Index Configuration - Multiple indices for different data types
OPENSEARCH_INDEXES = {
    'module_consumption_data': os.getenv('MODULE_CONSUMPTION_DATA', 'converse_lm_consumption_summary_reports_prod'),
    'user_profile_data': os.getenv('LEARNBEE_MODULE_REPORTS_DATA', 'learnbee_module_reports_user_summary_prod'),
    'module_catalog_data': os.getenv('LM_SUMMARY_DATA', 'converse_lm_summary_reports_prod'),
    # Monthly per-user activity summary (counts + points). Env var name per project: MONTHY_USER_ACTIVITY
    'monthly_user_activity_data': os.getenv('MONTHY_USER_ACTIVITY', 'monthly_user_activity_summary_prod'),
    # Daily per-user activity summary (counts + points). Env var name per project: DAILY_USER_ACTIVITY
    'daily_user_activity_data': os.getenv('DAILY_USER_ACTIVITY', 'daily_user_activity_summary_prod'),
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

