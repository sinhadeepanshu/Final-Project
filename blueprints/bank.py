"""
blueprints/bank.py – Bank Officer portal (Firestore backend)
"""
import json
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash)
from functools import wraps
from werkzeug.security import generate_password_hash
import re
from models import (db, User, Account, Transaction, RiskAssessment,
                    BankApproval, AuditLog, Notification, BankOfficerApplication)

bank_bp = Blueprint('bank', __name__, url_prefix='/bank')

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def bank_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'bank_id' not in session:
            flash('Bank Officer login required.', 'warning')
            return redirect(url_for('bank.login'))
        return f(*args, **kwargs)
    return decorated


def _audit(event, actor, details, txn_id=None):
    log = AuditLog(transaction_id=txn_id, event_type=event, actor=actor, details=details)
    log.save()


def _notify(user_id, message):
    n = Notification(user_id=user_id, message=message)
    n.save()


# ── Login / Signup / Logout ───────────────────────────────────────────────────

@bank_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        bank_name = request.form.get('bank_name', '').strip()
        name      = request.form.get('name', '').strip()
        phone     = request.form.get('phone', '').strip()
        email     = request.form.get('email', '').strip().lower()
        password  = request.form.get('password', '')
        confirm   = request.form.get('confirm_password', '')

        from sms_service import normalize_phone
        phone = normalize_phone(phone)

        if not all([bank_name, name, phone, email, password, confirm]):
            flash('Please complete all fields.', 'danger')
        elif not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            flash('Enter a valid email address.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
        else:
            phone_user = User.filter_by(phone=phone).first()
            email_user = User.filter_by(email=email).first()
            existing_user = phone_user or email_user

            # Check if it's already-approved legacy officer
            legacy_officer = False
            if (phone_user and email_user and phone_user.user_id == email_user.user_id
                    and phone_user.role == 'BANK_OFFICER'):
                approved = BankOfficerApplication.filter_by(
                    officer_id=phone_user.user_id, status='APPROVED').first()
                if not approved:
                    legacy_officer = True

            pending_apps = BankOfficerApplication.filter_by(status='PENDING').all()
            pending_application = next(
                (a for a in pending_apps if a.phone == phone or a.email == email), None)

            if (existing_user and not legacy_officer) or pending_application:
                flash('Phone or email is already registered or awaiting review.', 'danger')
            else:
                application = BankOfficerApplication(
                    bank_name=bank_name,
                    applicant_name=name,
                    phone=phone,
                    email=email,
                    password_hash=generate_password_hash(password),
                )
                application.save()
                flash('Application submitted. You can log in after admin approval.', 'success')
                return redirect(url_for('bank.login'))

    return render_template('bank/signup.html')


@bank_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'bank_id' in session:
        return redirect(url_for('bank.dashboard'))

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password', '')

        from sms_service import normalize_phone
        user = (User.filter_by(phone=normalize_phone(identifier)).first()
                or User.filter_by(email=identifier).first())

        if not user or user.role != 'BANK_OFFICER' or not user.check_password(password):
            flash('Invalid bank officer credentials.', 'danger')
            return render_template('bank/login.html')

        application = BankOfficerApplication.filter_by(
            officer_id=user.user_id, status='APPROVED').first()
        if not application:
            flash('Bank officer access requires admin approval.', 'danger')
            return render_template('bank/login.html')

        if not user.is_active:
            flash('Account deactivated.', 'danger')
            return render_template('bank/login.html')

        session['bank_id']   = user.user_id
        session['bank_name'] = user.name
        _audit('BANK_OFFICER_LOGIN', user.phone, f'Bank officer {user.name} logged in')
        return redirect(url_for('bank.dashboard'))

    return render_template('bank/login.html')


@bank_bp.route('/logout')
def logout():
    session.pop('bank_id', None)
    session.pop('bank_name', None)
    flash('Logged out from Bank Portal.', 'info')
    return redirect(url_for('bank.login'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@bank_bp.route('/dashboard')
@bank_required
def dashboard():
    officer = User.get(session['bank_id'])

    # Get all BankApprovals that are PENDING and have been forwarded
    all_bas = BankApproval.filter_by(status='PENDING').all()
    forwarded_bas = [ba for ba in all_bas if ba.forwarded_at is not None]

    pending_with_risk = []
    for ba in forwarded_bas:
        txn = Transaction.get(ba.transaction_id)
        if not txn or txn.status != 'PENDING_BANK_APPROVAL':
            continue
        ra          = RiskAssessment.filter_by(transaction_id=ba.transaction_id).first()
        sender_acc  = Account.get(txn.sender_acc_id) if txn else None
        sender_user = User.get(sender_acc.user_id) if sender_acc else None
        pending_with_risk.append({
            'approval': ba, 'txn': txn, 'risk': ra,
            'sender': sender_user, 'account': sender_acc
        })
    pending_with_risk.sort(key=lambda x: (x['risk'].score if x['risk'] else 0), reverse=True)

    all_bas_all    = BankApproval.query_all().all()
    total_pending  = len(pending_with_risk)
    total_approved = sum(1 for b in all_bas_all if b.status == 'APPROVED')
    total_rejected = sum(1 for b in all_bas_all if b.status == 'REJECTED')

    return render_template('bank/dashboard.html',
                           officer=officer,
                           items=pending_with_risk,
                           total_pending=total_pending,
                           total_approved=total_approved,
                           total_rejected=total_rejected)


# ── Transaction Detail ────────────────────────────────────────────────────────

@bank_bp.route('/transaction/<txn_id>')
@bank_required
def transaction_detail(txn_id):
    officer     = User.get(session['bank_id'])
    txn         = Transaction.get_or_404(txn_id)
    ba          = BankApproval.filter_by(transaction_id=txn_id).first()
    if not ba:
        from flask import abort
        abort(404)
    ra          = RiskAssessment.filter_by(transaction_id=txn_id).first()
    sender_acc  = Account.get(txn.sender_acc_id)
    sender_user = User.get(sender_acc.user_id) if sender_acc else None

    history = []
    if sender_acc:
        hist = Transaction.filter_by(sender_acc_id=sender_acc.account_id, status='EXECUTED').all()
        hist.sort(key=lambda t: t.created_at or utcnow(), reverse=True)
        history = hist[:5]

    risk_reasons = []
    if ra and ra.reasons:
        try:
            risk_reasons = json.loads(ra.reasons)
        except Exception:
            risk_reasons = [ra.reasons]

    return render_template('bank/transaction_detail.html',
                           officer=officer, txn=txn, ba=ba, risk=ra,
                           risk_reasons=risk_reasons,
                           sender_account=sender_acc, sender_user=sender_user,
                           history=history)


# ── Approve ───────────────────────────────────────────────────────────────────

@bank_bp.route('/transaction/<txn_id>/approve', methods=['POST'])
@bank_required
def approve(txn_id):
    officer = User.get(session['bank_id'])
    ba      = BankApproval.filter_by(transaction_id=txn_id).first()
    if not ba:
        from flask import abort
        abort(404)

    if ba.status != 'PENDING':
        flash('This request has already been decided.', 'warning')
        return redirect(url_for('bank.transaction_detail', txn_id=txn_id))

    txn = Transaction.get(txn_id)
    if not txn or txn.status != 'PENDING_BANK_APPROVAL':
        flash('This transaction is not awaiting bank approval.', 'warning')
        return redirect(url_for('bank.dashboard'))

    remarks = request.form.get('remarks', '').strip()
    ba.status       = 'APPROVED'
    ba.officer_id   = officer.user_id
    ba.officer_name = officer.name
    ba.remarks      = remarks or 'Approved by bank officer.'
    ba.decided_at   = utcnow()
    ba.save()

    txn.status = 'PENDING_ADMIN_FINAL_APPROVAL'
    txn.save()

    _audit('BANK_APPROVED', officer.phone,
           f'Bank approved txn Rs {txn.amount:,.0f}. Returning to admin for final approval.',
           txn_id)

    sender_acc  = Account.get(txn.sender_acc_id)
    sender_user = User.get(sender_acc.user_id) if sender_acc else None
    if sender_user:
        _notify(sender_user.user_id,
                f'🏦 Bank approved your transfer of Rs {txn.amount:,.0f}. Awaiting final admin approval.')

    flash('✅ Transaction approved. Admin will now give final approval.', 'success')
    return redirect(url_for('bank.dashboard'))


# ── Reject ────────────────────────────────────────────────────────────────────

@bank_bp.route('/transaction/<txn_id>/reject', methods=['POST'])
@bank_required
def reject(txn_id):
    officer = User.get(session['bank_id'])
    ba      = BankApproval.filter_by(transaction_id=txn_id).first()
    if not ba:
        from flask import abort
        abort(404)

    if ba.status != 'PENDING':
        flash('This request has already been decided.', 'warning')
        return redirect(url_for('bank.transaction_detail', txn_id=txn_id))

    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Rejection reason is required.', 'danger')
        return redirect(url_for('bank.transaction_detail', txn_id=txn_id))

    ba.status       = 'REJECTED'
    ba.officer_id   = officer.user_id
    ba.officer_name = officer.name
    ba.remarks      = reason
    ba.decided_at   = utcnow()
    ba.save()

    txn = Transaction.get(txn_id)
    if txn:
        txn.status        = 'REJECTED'
        txn.reject_reason = f'[Bank Rejected] {reason}'
        txn.save()

    _audit('BANK_REJECTED', officer.phone, f'Bank rejected: {reason}', txn_id)

    sender_acc  = Account.get(txn.sender_acc_id) if txn else None
    sender_user = User.get(sender_acc.user_id) if sender_acc else None
    if sender_user and txn:
        _notify(sender_user.user_id,
                f'❌ Your transfer of Rs {txn.amount:,.0f} to {txn.receiver_name} '
                f'was rejected by the bank. Reason: {reason}')

    flash('❌ Transaction rejected by bank.', 'info')
    return redirect(url_for('bank.dashboard'))
