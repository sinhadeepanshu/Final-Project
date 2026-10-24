"""
blueprints/user.py – User panel routes (Local JSON storage)
"""
import json, re
from datetime import datetime, timezone, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, current_app, jsonify)
from functools import wraps
from models import (db, User, Account, Beneficiary, Transaction,
                    RiskAssessment, Notification, AuditLog, Verification,
                    BankApproval)
from otp_service import send_otp, verify_otp, mask_phone
from sms_service import normalize_phone
from verification import run_dual_verification
from fraud_model import assess_risk
from bank_gateway import execute_transfer

user_bp = Blueprint('user', __name__, url_prefix='/user')

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def _audit(event, actor, details, txn_id=None):
    log = AuditLog(transaction_id=txn_id, event_type=event, actor=actor, details=details)
    log.save()


def _get_current_user():
    return User.get(session['user_id'])


def _notify(user_id, message):
    n = Notification(user_id=user_id, message=message)
    n.save()


# ── Dashboard ────────────────────────────────────────────────────────────────

@user_bp.route('/dashboard')
@login_required
def dashboard():
    user    = _get_current_user()
    account = Account.filter_by(user_id=user.user_id, status='ACTIVE').first()
    recent  = []
    pending_count = total_sent = 0
    monthly_data  = {}

    if account:
        all_txns = Transaction.filter_by(sender_acc_id=account.account_id).all()
        all_txns.sort(key=lambda t: t.created_at or utcnow(), reverse=True)
        recent = all_txns[:5]

        pending_count = sum(1 for t in all_txns if t.status == 'PENDING_ADMIN_APPROVAL')
        executed = [t for t in all_txns if t.status == 'EXECUTED']
        total_sent = sum(t.amount for t in executed)

        for i in range(5, -1, -1):
            d = utcnow() - timedelta(days=30 * i)
            label = d.strftime('%b')
            month_txns = [t for t in executed
                          if t.created_at and t.created_at.month == d.month
                          and t.created_at.year == d.year]
            monthly_data[label] = sum(t.amount for t in month_txns)

    unread = len(Notification.filter_by(user_id=user.user_id, is_read=False).all())

    return render_template('user/dashboard.html',
                           user=user, account=account, recent=recent,
                           pending_count=pending_count, total_sent=total_sent,
                           monthly_data=json.dumps(monthly_data),
                           unread=unread)


# ── Profile / KYC ────────────────────────────────────────────────────────────

@user_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = _get_current_user()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'kyc':
            pan     = request.form.get('pan', '').strip().upper()
            aadhaar = request.form.get('aadhaar_last4', '').strip()
            address = request.form.get('address', '').strip()
            if not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', pan):
                flash('Invalid PAN format (e.g. ABCDE1234F).', 'danger')
            elif not re.match(r'^\d{4}$', aadhaar):
                flash('Aadhaar last 4 digits must be 4 numbers.', 'danger')
            elif not address:
                flash('Address is required.', 'danger')
            else:
                user.kyc_pan           = pan
                user.kyc_aadhaar_last4 = aadhaar
                user.kyc_address       = address
                user.kyc_status        = 'PENDING'
                user.save()
                _audit('KYC_SUBMITTED', user.phone, f'KYC submitted by {user.name}')
                flash('KYC submitted for verification.', 'success')

        elif action == 'change_password':
            return redirect(url_for('user.change_password'))

    account = Account.filter_by(user_id=user.user_id).first()
    return render_template('user/profile.html', user=user, account=account)


@user_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    user = _get_current_user()
    step = session.get('pwd_change_step', 1)

    if request.method == 'POST':
        action = request.form.get('action')

        if step == 1:
            old_pw  = request.form.get('old_password', '')
            new_pw  = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')
            if not user.check_password(old_pw):
                flash('Current password is incorrect.', 'danger')
            elif new_pw != confirm:
                flash('New passwords do not match.', 'danger')
            elif len(new_pw) < 8:
                flash('Password must be at least 8 characters.', 'danger')
            else:
                session['pwd_new'] = new_pw
                session['pwd_change_step'] = 2
                result = send_otp(user.phone, 'LOGIN', user_name=user.name)
                session['pwd_otp_dev_code'] = result.get('otp') if result.get('dev_fallback') else None
                flash('OTP sent for verification.', 'info')
                return redirect(url_for('user.change_password'))

        elif step == 2:
            if action == 'resend':
                result = send_otp(user.phone, 'LOGIN', user_name=user.name)
                session['pwd_otp_dev_code'] = result.get('otp') if result.get('dev_fallback') else None
                flash('OTP resent.', 'info')
            else:
                digits  = [request.form.get(f'otp{i}', '') for i in range(1, 7)]
                entered = ''.join(digits)
                result  = verify_otp(user.phone, 'LOGIN', entered)
                if not result['success']:
                    flash(result['message'], 'danger')
                else:
                    user.set_password(session.pop('pwd_new'))
                    user.save()
                    session.pop('pwd_change_step', None)
                    session.pop('pwd_otp_dev_code', None)
                    _audit('PASSWORD_CHANGED', user.phone, 'Password changed')
                    flash('Password changed successfully.', 'success')
                    return redirect(url_for('user.profile'))

    dev_code = session.get('pwd_otp_dev_code')
    return render_template('user/change_password.html', user=user, step=step,
                           masked_phone=mask_phone(user.phone),
                           dev_fallback=bool(dev_code), dev_code=dev_code)


# ── Beneficiaries ─────────────────────────────────────────────────────────────

@user_bp.route('/beneficiaries', methods=['GET', 'POST'])
@login_required
def beneficiaries():
    user = _get_current_user()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name    = request.form.get('name', '').strip()
            acc_num = request.form.get('account_number', '').strip()
            ifsc    = request.form.get('ifsc', '').strip().upper()
            bank    = request.form.get('bank_name', '').strip()

            if not re.match(r'^\d{9,18}$', acc_num):
                flash('Account number must be 9–18 digits.', 'danger')
            elif not re.match(r'^[A-Z]{4}0[A-Z0-9]{6}$', ifsc):
                flash('Invalid IFSC format (e.g. HDFC0001234).', 'danger')
            else:
                b = Beneficiary(user_id=user.user_id, name=name,
                                account_number=acc_num, ifsc=ifsc, bank_name=bank)
                b.save()
                _audit('BENEFICIARY_ADDED', user.phone, f'Added beneficiary {name}')
                flash(f'Beneficiary {name} added.', 'success')

        elif action == 'delete':
            b_id = request.form.get('bene_id')
            b = Beneficiary.filter_by(id=b_id, user_id=user.user_id).first()
            if b:
                b.delete()
                flash('Beneficiary removed.', 'info')

    benes = Beneficiary.filter_by(user_id=user.user_id).all()
    benes.sort(key=lambda x: x.created_at or utcnow(), reverse=True)
    return render_template('user/beneficiaries.html', user=user, beneficiaries=benes)


# ── Transfer ──────────────────────────────────────────────────────────────────

@user_bp.route('/transfer', methods=['GET', 'POST'])
@login_required
def transfer():
    user    = _get_current_user()
    account = Account.filter_by(user_id=user.user_id, status='ACTIVE').first()

    if user.kyc_status != 'VERIFIED':
        flash('KYC verification required before initiating transfers.', 'warning')
        return redirect(url_for('user.profile'))

    if not account:
        flash('No active account found.', 'danger')
        return redirect(url_for('user.dashboard'))

    benes = Beneficiary.filter_by(user_id=user.user_id, status='ACTIVE').all()

    if request.method == 'POST':
        bene_id = request.form.get('beneficiary_id')
        amount  = request.form.get('amount', '0').replace(',', '')
        purpose = request.form.get('purpose', '')
        channel = request.form.get('channel', 'RTGS')
        remarks = request.form.get('remarks', '').strip()

        try:
            amount = float(amount)
        except ValueError:
            flash('Invalid amount.', 'danger')
            return render_template('user/transfer.html', user=user,
                                   account=account, beneficiaries=benes)

        min_t = current_app.config.get('MIN_TRANSFER', 200_000)
        max_t = current_app.config.get('MAX_TRANSFER', 10_000_000)
        if not (min_t <= amount <= max_t):
            flash(f'Amount must be between Rs {min_t:,.0f} and Rs {max_t:,.0f}.', 'danger')
            return render_template('user/transfer.html', user=user,
                                   account=account, beneficiaries=benes)

        bene = Beneficiary.filter_by(id=bene_id, user_id=user.user_id).first()
        if not bene:
            flash('Invalid beneficiary selected.', 'danger')
            return render_template('user/transfer.html', user=user,
                                   account=account, beneficiaries=benes)

        txn = Transaction(
            sender_acc_id=account.account_id,
            receiver_name=bene.name,
            receiver_acc=bene.account_number,
            receiver_ifsc=bene.ifsc,
            receiver_bank=bene.bank_name,
            amount=amount,
            purpose=purpose,
            channel=channel,
            remarks=remarks,
            status='PENDING_OTP',
        )
        txn.save()

        result = send_otp(user.phone, 'PAYMENT',
                          transaction_id=txn.transaction_id,
                          amount=amount, receiver_name=bene.name,
                          user_name=user.name)
        session['payment_txn_id']       = txn.transaction_id
        session['payment_otp_dev_code'] = result.get('otp') if result.get('dev_fallback') else None

        return redirect(url_for('user.payment_otp'))

    return render_template('user/transfer.html', user=user,
                           account=account, beneficiaries=benes)


@user_bp.route('/payment-otp', methods=['GET', 'POST'])
@login_required
def payment_otp():
    user   = _get_current_user()
    txn_id = session.get('payment_txn_id')
    if not txn_id:
        return redirect(url_for('user.transfer'))

    txn = Transaction.get(txn_id)
    if not txn or txn.status not in ('PENDING_OTP',):
        flash('Transaction not found or already processed.', 'danger')
        return redirect(url_for('user.transactions'))

    dev_code = session.get('payment_otp_dev_code')

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'resend':
            result = send_otp(user.phone, 'PAYMENT', transaction_id=txn_id,
                              amount=txn.amount, receiver_name=txn.receiver_name,
                              user_name=user.name)
            session['payment_otp_dev_code'] = result.get('otp') if result.get('dev_fallback') else None
            flash('OTP resent.', 'info')
            return redirect(url_for('user.payment_otp'))

        digits  = [request.form.get(f'otp{i}', '') for i in range(1, 7)]
        entered = ''.join(digits)
        result  = verify_otp(user.phone, 'PAYMENT', entered, transaction_id=txn_id)

        if not result['success']:
            flash(result['message'], 'danger')
            return render_template('user/payment_otp.html', user=user, txn=txn,
                                   masked_phone=mask_phone(user.phone),
                                   dev_fallback=bool(dev_code), dev_code=dev_code)

        txn.status = 'VERIFYING'
        txn.save()
        session.pop('payment_txn_id', None)
        session.pop('payment_otp_dev_code', None)

        _process_transaction(txn, user)
        return redirect(url_for('user.transaction_detail', txn_id=txn.transaction_id))

    return render_template('user/payment_otp.html', user=user, txn=txn,
                           masked_phone=mask_phone(user.phone),
                           dev_fallback=bool(dev_code), dev_code=dev_code)


def _process_transaction(txn, user):
    """Run dual verification + AI risk and queue for admin review."""
    account = Account.get(txn.sender_acc_id)

    v_result = run_dual_verification(txn)
    if v_result['reject_reason']:
        txn.status        = 'REJECTED'
        txn.reject_reason = v_result['reject_reason']
        txn.save()
        _notify(user.user_id,
                f'Transaction Rs {txn.amount:,.0f} rejected: {txn.reject_reason}')
        _audit('TRANSACTION_REJECTED', user.phone, txn.reject_reason, txn.transaction_id)
        return

    risk = assess_risk(txn, account)
    ra = RiskAssessment(
        transaction_id=txn.transaction_id,
        score=risk['score'],
        level=risk['level'],
        flags=json.dumps(risk['flags']),
        reasons=json.dumps(risk['reasons']),
    )
    ra.save()

    txn.status = 'PENDING_ADMIN_APPROVAL'
    txn.save()

    ba = BankApproval(transaction_id=txn.transaction_id, status='PENDING')
    ba.save()

    _notify(user.user_id,
            f'⏳ Transfer of Rs {txn.amount:,.0f} is under review. Risk: {risk["level"]}')
    _audit('TRANSACTION_SUBMITTED', user.phone,
           f'Rs {txn.amount:,.0f} to {txn.receiver_name} – risk {risk["level"]}',
           txn.transaction_id)


# ── Transaction History ───────────────────────────────────────────────────────

@user_bp.route('/transactions')
@login_required
def transactions():
    user    = _get_current_user()
    account = Account.filter_by(user_id=user.user_id).first()
    txns    = []
    if account:
        all_txns = Transaction.filter_by(sender_acc_id=account.account_id).all()
        status_filter = request.args.get('status')
        if status_filter:
            all_txns = [t for t in all_txns if t.status == status_filter]
        search = request.args.get('q', '').strip().lower()
        if search:
            all_txns = [t for t in all_txns if
                        search in (t.receiver_name or '').lower() or
                        search in (t.receiver_acc or '').lower() or
                        search in (t.transaction_id or '').lower()]
        all_txns.sort(key=lambda t: t.created_at or utcnow(), reverse=True)
        txns = all_txns[:100]
    return render_template('user/transactions.html', user=user, transactions=txns,
                           status_filter=request.args.get('status', ''),
                           search_q=request.args.get('q', ''))


@user_bp.route('/transactions/<txn_id>')
@login_required
def transaction_detail(txn_id):
    user    = _get_current_user()
    account = Account.filter_by(user_id=user.user_id).first()
    txn     = Transaction.get_or_404(txn_id)
    if not account or txn.sender_acc_id != account.account_id:
        flash('Transaction not found.', 'danger')
        return redirect(url_for('user.transactions'))
    risk   = RiskAssessment.filter_by(transaction_id=txn_id).first()
    verifs = Verification.filter_by(transaction_id=txn_id).all()
    return render_template('user/transaction_detail.html',
                           user=user, txn=txn, risk=risk, verifications=verifs)


@user_bp.route('/notifications')
@login_required
def notifications():
    user   = _get_current_user()
    notifs = Notification.filter_by(user_id=user.user_id).all()
    notifs.sort(key=lambda n: n.created_at or utcnow(), reverse=True)
    for n in notifs:
        if not n.is_read:
            n.is_read = True
            n.save()
    return render_template('user/notifications.html', user=user, notifications=notifs)
