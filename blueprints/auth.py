"""
blueprints/auth.py – Registration, Login (2-step), Logout
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, current_app)
from werkzeug.security import generate_password_hash
from models import db, User, Account, PendingRegistration, AuditLog
from otp_service import send_otp, verify_otp, mask_phone
from sms_service import normalize_phone
import re, random, string
from datetime import datetime, timezone

auth_bp = Blueprint('auth', __name__)

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _audit(event, actor, details, txn_id=None):
    log = AuditLog(transaction_id=txn_id, event_type=event, actor=actor, details=details)
    log.save()

def _gen_account_number():
    return ''.join(random.choices(string.digits, k=14))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_user_and_account(pending: PendingRegistration) -> User:
    user = User(
        name=pending.name,
        phone=normalize_phone(pending.phone),
        email=pending.email,
        password_hash=pending.password_hash,
        role='USER',
        kyc_status='PENDING',
    )
    user.save()

    acc_num = _gen_account_number()
    while Account.filter_by(account_number=acc_num).first():
        acc_num = _gen_account_number()

    account = Account(
        user_id=user.user_id,
        bank_name='PayVanta Bank',
        account_number=acc_num,
        ifsc='PVNT0001001',
        balance=current_app.config.get('INITIAL_BALANCE', 1_000_000),
        status='ACTIVE',
    )
    account.save()
    pending.delete()
    _audit('USER_CREATED', user.phone, f'New user {user.name} registered')
    return user


def _clear_otp_pending(purpose: str):
    session.pop(f'otp_{purpose}_phone', None)
    session.pop(f'otp_{purpose}_role', None)
    session.pop(f'otp_{purpose}_user_id', None)
    session.pop(f'otp_{purpose}_dev_code', None)


# ── Routes ───────────────────────────────────────────────────────────────────

@auth_bp.route('/', methods=['GET', 'POST'])
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        tab = request.form.get('tab', 'user')

        if tab == 'signup':
            return _handle_signup()

        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password', '')

        user = (User.filter_by(phone=normalize_phone(identifier)).first()
                or User.filter_by(email=identifier).first())

        role_needed = 'ADMIN' if tab == 'admin' else 'USER'
        if not user or user.role != role_needed or not user.check_password(password):
            flash('Invalid credentials.', 'danger')
            return render_template('auth/login.html', active_tab=tab)

        if not user.is_active:
            flash('Your account has been deactivated.', 'danger')
            return render_template('auth/login.html', active_tab=tab)

        result = send_otp(user.phone, 'LOGIN', user_name=user.name)
        session['otp_LOGIN_phone']    = user.phone
        session['otp_LOGIN_role']     = user.role
        session['otp_LOGIN_user_id']  = user.user_id
        session['otp_LOGIN_dev_code'] = result.get('otp') if result.get('dev_fallback') else None

        return redirect(url_for('auth.otp_verify', purpose='LOGIN'))

    return render_template('auth/login.html', active_tab='user')


def _handle_signup():
    name     = request.form.get('name', '').strip()
    phone    = normalize_phone(request.form.get('phone', '').strip())
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    confirm  = request.form.get('confirm_password', '')

    if not all([name, phone, email, password, confirm]):
        flash('All fields are required.', 'danger')
        return render_template('auth/login.html', active_tab='signup')

    if password != confirm:
        flash('Passwords do not match.', 'danger')
        return render_template('auth/login.html', active_tab='signup')

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return render_template('auth/login.html', active_tab='signup')

    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        flash('Invalid email address.', 'danger')
        return render_template('auth/login.html', active_tab='signup')

    if (User.filter_by(phone=phone).first() or User.filter_by(email=email).first()):
        flash('Phone or email already registered.', 'danger')
        return render_template('auth/login.html', active_tab='signup')

    existing_pending = PendingRegistration.filter_by(phone=phone).first()
    if existing_pending:
        existing_pending.delete()

    pending = PendingRegistration(
        name=name, phone=phone, email=email,
        password_hash=generate_password_hash(password)
    )
    pending.save()

    result = send_otp(phone, 'SIGNUP', user_name=name)
    session['otp_SIGNUP_phone']    = phone
    session['otp_SIGNUP_dev_code'] = result.get('otp') if result.get('dev_fallback') else None

    return redirect(url_for('auth.otp_verify', purpose='SIGNUP'))


@auth_bp.route('/otp/<purpose>', methods=['GET', 'POST'])
def otp_verify(purpose):
    purpose = purpose.upper()
    if purpose not in {'SIGNUP', 'LOGIN'}:
        return redirect(url_for('auth.login'))

    phone    = session.get(f'otp_{purpose}_phone')
    dev_code = session.get(f'otp_{purpose}_dev_code')

    if not phone:
        flash('Session expired. Please start again.', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'resend':
            user_name = ''
            if purpose == 'SIGNUP':
                pr = PendingRegistration.filter_by(phone=phone).first()
                user_name = pr.name if pr else phone
            else:
                u = User.filter_by(phone=phone).first()
                user_name = u.name if u else phone

            result = send_otp(phone, purpose, user_name=user_name)
            if not result['success']:
                flash(result['message'], 'danger')
            else:
                session[f'otp_{purpose}_dev_code'] = result.get('otp') if result.get('dev_fallback') else None
                flash('OTP resent.', 'success')
            return redirect(url_for('auth.otp_verify', purpose=purpose))

        digits  = [request.form.get(f'otp{i}', '') for i in range(1, 7)]
        entered = ''.join(digits).strip()
        result  = verify_otp(phone, purpose, entered)

        if not result['success']:
            flash(result['message'], 'danger')
            return render_template('auth/otp.html', purpose=purpose,
                                   masked_phone=mask_phone(phone),
                                   dev_fallback=bool(dev_code),
                                   dev_code=dev_code)

        if purpose == 'SIGNUP':
            pending = PendingRegistration.filter_by(phone=phone).first()
            if not pending:
                flash('Registration data expired. Please sign up again.', 'danger')
                return redirect(url_for('auth.login'))
            user = _create_user_and_account(pending)
            _clear_otp_pending('SIGNUP')
            flash(f'Welcome {user.name}! Account created successfully.', 'success')
            return redirect(url_for('auth.login'))

        else:  # LOGIN
            user_id = session.pop('otp_LOGIN_user_id', None)
            role    = session.pop('otp_LOGIN_role', None)
            _clear_otp_pending('LOGIN')
            user = User.get(user_id)
            if not user:
                flash('User not found.', 'danger')
                return redirect(url_for('auth.login'))
            if role == 'ADMIN':
                session['admin_id']   = user.user_id
                session['admin_name'] = user.name
                _audit('ADMIN_LOGIN', user.phone, 'Admin logged in')
                return redirect(url_for('admin.dashboard'))
            else:
                session['user_id']   = user.user_id
                session['user_name'] = user.name
                _audit('USER_LOGIN', user.phone, 'User logged in')
                return redirect(url_for('user.dashboard'))

    return render_template('auth/otp.html', purpose=purpose,
                           masked_phone=mask_phone(phone),
                           dev_fallback=bool(dev_code),
                           dev_code=dev_code)


@auth_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password', '')

        user = (User.filter_by(phone=normalize_phone(identifier)).first()
                or User.filter_by(email=identifier).first())

        if not user or user.role != 'ADMIN' or not user.check_password(password):
            flash('Invalid admin credentials.', 'danger')
            return render_template('auth/admin_login.html')

        if not user.is_active:
            flash('Admin account is deactivated.', 'danger')
            return render_template('auth/admin_login.html')

        result = send_otp(user.phone, 'LOGIN', user_name=user.name)
        session['otp_LOGIN_phone']    = user.phone
        session['otp_LOGIN_role']     = user.role
        session['otp_LOGIN_user_id']  = user.user_id
        session['otp_LOGIN_dev_code'] = result.get('otp') if result.get('dev_fallback') else None

        return redirect(url_for('auth.otp_verify', purpose='LOGIN'))

    return render_template('auth/admin_login.html')


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        user = (User.filter_by(phone=normalize_phone(identifier)).first()
                or User.filter_by(email=identifier).first())
        if not user:
            flash('No account found with that phone or email.', 'danger')
            return render_template('auth/forgot_password.html')

        result = send_otp(user.phone, 'LOGIN', user_name=user.name)
        session['fp_user_id']  = user.user_id
        session['fp_phone']    = user.phone
        session['fp_step']     = 1
        session['fp_dev_code'] = result.get('otp') if result.get('dev_fallback') else None
        flash('OTP sent for verification.', 'info')
        return redirect(url_for('auth.reset_password'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    user_id = session.get('fp_user_id')
    phone   = session.get('fp_phone')
    if not user_id or not phone:
        flash('Session expired. Please start again.', 'warning')
        return redirect(url_for('auth.forgot_password'))

    dev_code = session.get('fp_dev_code')
    step     = session.get('fp_step', 1)

    if request.method == 'POST':
        action = request.form.get('action')

        if step == 1:
            if action == 'resend':
                user = User.get(user_id)
                result = send_otp(phone, 'LOGIN', user_name=user.name if user else phone)
                session['fp_dev_code'] = result.get('otp') if result.get('dev_fallback') else None
                flash('OTP resent.', 'info')
                return redirect(url_for('auth.reset_password'))

            digits  = [request.form.get(f'otp{i}', '') for i in range(1, 7)]
            entered = ''.join(digits)
            result  = verify_otp(phone, 'LOGIN', entered)
            if not result['success']:
                flash(result['message'], 'danger')
                return render_template('auth/reset_password.html', step=1,
                                       masked_phone=mask_phone(phone),
                                       dev_fallback=bool(dev_code),
                                       dev_code=dev_code)
            session['fp_step'] = 2
            return redirect(url_for('auth.reset_password'))

        elif step == 2:
            new_pw  = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')
            if new_pw != confirm:
                flash('Passwords do not match.', 'danger')
            elif len(new_pw) < 8:
                flash('Password must be at least 8 characters.', 'danger')
            else:
                user = User.get(user_id)
                user.set_password(new_pw)
                user.save()
                _audit('PASSWORD_RESET', phone, 'Password reset via forgot password')
                for k in ('fp_user_id', 'fp_phone', 'fp_step', 'fp_dev_code'):
                    session.pop(k, None)
                flash('Password reset successfully! Please log in.', 'success')
                return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', step=step,
                           masked_phone=mask_phone(phone),
                           dev_fallback=bool(dev_code),
                           dev_code=dev_code)


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    _clear_otp_pending('SIGNUP')
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/logout/user')
def logout_user():
    session.pop('user_id', None)
    session.pop('user_name', None)
    flash('You have been logged out from the user portal.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/logout/admin')
def logout_admin():
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    flash('You have been logged out from the admin portal.', 'info')
    return redirect(url_for('auth.admin_login'))
