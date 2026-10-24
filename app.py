import os, random, string
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv

load_dotenv()

from config import Config
from models import db, User, Account, Transaction, RiskAssessment, AuditLog, \
    Verification, BankOfficerApplication
from blueprints.auth import auth_bp
from blueprints.user import user_bp
from blueprints.admin import admin_bp
from blueprints.bank import bank_bp


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    csrf = CSRFProtect(app)

    @app.before_request
    def make_session_permanent():
        from flask import session as flask_session
        flask_session.permanent = True

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(bank_bp)

    # ── Template filters ──────────────────────────────────────────────────────
    @app.template_filter('inr')
    def inr_format(value):
        try:
            value = float(value)
            n = str(int(value))
            if len(n) > 3:
                last3 = n[-3:]
                rest  = n[:-3]
                groups = []
                while len(rest) > 2:
                    groups.append(rest[-2:])
                    rest = rest[:-2]
                if rest:
                    groups.append(rest)
                groups.reverse()
                return ','.join(groups) + ',' + last3
            return n
        except Exception:
            return str(value)

    @app.template_filter('amount_words')
    def amount_words(value):
        try:
            value = int(float(value))
            units = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
                     'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
                     'Seventeen', 'Eighteen', 'Nineteen']
            tens  = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

            def two_digits(n):
                if n < 20:
                    return units[n]
                return (tens[n // 10] + (' ' + units[n % 10] if n % 10 else '')).strip()

            def three_digits(n):
                if n >= 100:
                    return units[n // 100] + ' Hundred' + (' ' + two_digits(n % 100) if n % 100 else '')
                return two_digits(n)

            if value == 0:
                return 'Zero'
            result = ''
            cr  = value // 10_000_000
            lak = (value % 10_000_000) // 100_000
            tho = (value % 100_000) // 1_000
            rem = value % 1_000
            if cr:  result += three_digits(cr) + ' Crore '
            if lak: result += two_digits(lak) + ' Lakh '
            if tho: result += two_digits(tho) + ' Thousand '
            if rem: result += three_digits(rem)
            return result.strip() + ' Rupees'
        except Exception:
            return str(value)

    @app.template_filter('risk_badge')
    def risk_badge(level):
        classes = {'LOW': 'badge-low', 'MEDIUM': 'badge-medium', 'HIGH': 'badge-high'}
        return classes.get(level, 'badge-low')

    @app.template_filter('status_badge')
    def status_badge(status):
        mapping = {
            'EXECUTED': 'badge-success',
            'REJECTED': 'badge-danger',
            'FAILED':   'badge-danger',
            'CANCELLED':'badge-secondary',
            'PENDING_ADMIN_APPROVAL': 'badge-warning',
            'PENDING_BANK_APPROVAL':  'badge-info',
            'VERIFYING': 'badge-info',
            'PENDING_OTP': 'badge-info',
        }
        return mapping.get(status, 'badge-secondary')

    # ── Error handlers ────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template('error.html', code=404, message='Page not found.'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('error.html', code=403, message='Access denied.'), 403

    @app.errorhandler(500)
    def server_error(e):
        import traceback
        tb = traceback.format_exc()
        print(f'[PayVanta 500] {tb}')
        msg = tb if app.debug else 'Internal server error.'
        return render_template('error.html', code=500, message=msg), 500

    @app.route('/_debug/ping')
    def debug_ping():
        from flask import jsonify
        import sys
        return jsonify({
            'status':  'ok',
            'python':  sys.version,
            'backend': 'Local JSON Storage',
        })

    return app


def _gen_account_number():
    return ''.join(random.choices(string.digits, k=14))


def seed_data(app):
    """Seed admin, bank officer, and demo users (idempotent)."""
    with app.app_context():
        # ── Admin ────────────────────────────────────────────────────────────
        try:
            admin_phone = app.config.get('ADMIN_PHONE', '+919999999999')
            admin = User.filter_by(role='ADMIN').first()
            if not admin:
                admin = User(
                    name='PayVanta Admin',
                    phone=admin_phone,
                    email='admin@payvanta.in',
                    role='ADMIN',
                    kyc_status='VERIFIED',
                )
                admin.set_password('Admin@123')
                admin.save()
                print(f'[seed_data] Admin created: {admin_phone}')
        except Exception as e:
            print(f'[seed_data] Admin seed failed: {e}')

        # ── Bank Officer ──────────────────────────────────────────────────────
        try:
            officer_phone = '+919888888888'
            officer_email = 'officer@payvanta.in'
            officer = User.filter_by(phone=officer_phone).first()
            if not officer:
                officer = User(
                    name='PayVanta Bank Officer',
                    phone=officer_phone,
                    email=officer_email,
                    role='BANK_OFFICER',
                    kyc_status='VERIFIED',
                )
                officer.set_password('Officer@123')
                officer.save()
                print(f'[seed_data] Bank officer created: {officer_phone}')

            officer_app = BankOfficerApplication.filter_by(
                officer_id=officer.user_id, status='APPROVED').first()
            if not officer_app:
                officer_app = BankOfficerApplication(
                    bank_name='PayVanta Bank',
                    applicant_name=officer.name,
                    phone=officer.phone,
                    email=officer.email,
                    password_hash=officer.password_hash,
                    status='APPROVED',
                    officer_id=officer.user_id,
                )
                officer_app.save()
        except Exception as e:
            print(f'[seed_data] Officer seed failed: {e}')

        # ── Demo Users ────────────────────────────────────────────────────────
        try:
            demo_users = [
                ('Rahul Sharma', '+919876543210', 'rahul@demo.in', 'VERIFIED'),
                ('Priya Patel',  '+919765432109', 'priya@demo.in', 'VERIFIED'),
                ('Amit Kumar',   '+919654321098', 'amit@demo.in',  'PENDING'),
            ]
            for name, phone, email, kyc in demo_users:
                if not User.filter_by(phone=phone).first():
                    u = User(name=name, phone=phone, email=email, role='USER',
                             kyc_status=kyc, kyc_pan='ABCDE1234F',
                             kyc_aadhaar_last4='5678', kyc_address='123 MG Road, Bengaluru')
                    u.set_password('demo1234')
                    u.save()

                    acc_num = _gen_account_number()
                    while Account.filter_by(account_number=acc_num).first():
                        acc_num = _gen_account_number()

                    acc = Account(user_id=u.user_id, bank_name='PayVanta Bank',
                                  account_number=acc_num, ifsc='PVNT0001001',
                                  balance=1_000_000, status='ACTIVE')
                    acc.save()
                    print(f'[seed_data] Demo user created: {phone}')
        except Exception as e:
            print(f'[seed_data] Demo user seed failed: {e}')


def _warm_fraud_model(app):
    with app.app_context():
        try:
            from fraud_model import _load_model
            _load_model()
            print('✅ Fraud model ready.\n')
        except Exception as e:
            print('[WARN] Fraud model init: %s' % e)


# ── Top-level app object ──────────────────────────────────────────────────────
app = create_app()
seed_data(app)
_warm_fraud_model(app)


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    print('🚀 PayVanta AI running at http://127.0.0.1:5000\n')
    app.run(debug=debug, host='127.0.0.1', port=5000)
