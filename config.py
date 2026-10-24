import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    SESSION_COOKIE_NAME = '__session'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'

    # No SQL database – using Firebase Firestore
    SQLALCHEMY_DATABASE_URI    = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    WTF_CSRF_ENABLED         = True
    SESSION_COOKIE_HTTPONLY  = True
    SESSION_COOKIE_SAMESITE  = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

    ADMIN_PHONE = os.environ.get('ADMIN_PHONE', '+919999999999')
    DEV_MODE = os.environ.get('DEV_MODE', 'false').lower() == 'true'

    # Transfer limits (INR)
    MIN_TRANSFER = 200_000     # Rs 2,00,000
    MAX_TRANSFER = 10_000_000  # Rs 1,00,00,000
    DAILY_LIMIT  = 20_000_000  # Rs 2,00,00,000

    # OTP
    OTP_EXPIRY_SECONDS  = 300  # 5 minutes
    OTP_MAX_ATTEMPTS    = 3
    OTP_MAX_RESENDS     = 3
    OTP_RESEND_COOLDOWN = 30   # seconds

    # Initial balance for new users
    INITIAL_BALANCE = 1_000_000  # Rs 10,00,000
