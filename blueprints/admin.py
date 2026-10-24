"""
blueprints/admin.py – Admin panel routes (Local JSON storage)
"""
import json, csv, io, threading
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, current_app, Response)
from functools import wraps
from models import (db, User, Account, Transaction, RiskAssessment,
                    AuditLog, Notification, Blacklist, Verification,
                    BankApproval, CallLog, BankOfficerApplication)
from bank_gateway import execute_transfer

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Thread lock replaces Firestore transactions for local concurrency safety
_approve_lock = threading.Lock()

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Admin login required.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def _audit(event, actor, details, txn_id=None):
    log = AuditLog(transaction_id=txn_id, event_type=event, actor=actor, details=details)
    log.save()


def _notify(user_id, message):
    n = Notification(user_id=user_id, message=message)
    n.save()


def _get_admin():
    return User.get(session['admin_id'])


# ── Dashboard ────────────────────────────────────────────────────────────────

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    admin = _get_admin()

    all_users    = User.filter_by(role='USER').all()
    total_users  = len(all_users)
    all_txns     = Transaction.query_all().all()
    total_txns   = len(all_txns)
    all_risks    = RiskAssessment.query_all().all()
    flagged      = sum(1 for r in all_risks if r.level == 'HIGH')
    executed_txns = [t for t in all_txns if t.status == 'EXECUTED']
    total_volume  = sum(t.amount for t in executed_txns)

    risk_dist = {'LOW': 0, 'MEDIUM': 0, 'HIGH': 0}
    for ra in all_risks:
        if ra.level in risk_dist:
            risk_dist[ra.level] += 1

    pending_admin = [t for t in all_txns if t.status == 'PENDING_ADMIN_APPROVAL']
    pending_admin_with_risk = []
    for txn in pending_admin:
        ra = RiskAssessment.filter_by(transaction_id=txn.transaction_id).first()
        pending_admin_with_risk.append((txn, ra))
    pending_admin_with_risk.sort(key=lambda x: (x[1].score if x[1] else 0), reverse=True)

    pending_bank = [t for t in all_txns if t.status == 'PENDING_BANK_APPROVAL']
    pending_bank_with_risk = []
    for txn in pending_bank:
        ra = RiskAssessment.filter_by(transaction_id=txn.transaction_id).first()
        ba = BankApproval.filter_by(transaction_id=txn.transaction_id).first()
        pending_bank_with_risk.append((txn, ra, ba))
    pending_bank_with_risk.sort(key=lambda x: (x[1].score if x[1] else 0), reverse=True)

    pending_final = [t for t in all_txns if t.status == 'PENDING_ADMIN_FINAL_APPROVAL']
    pending_final_with_risk = []
    for txn in pending_final:
        ra = RiskAssessment.filter_by(transaction_id=txn.transaction_id).first()
        ba = BankApproval.filter_by(transaction_id=txn.transaction_id).first()
        pending_final_with_risk.append((txn, ra, ba))
    pending_final_with_risk.sort(key=lambda x: (x[1].score if x[1] else 0), reverse=True)

    return render_template('admin/dashboard.html',
                           admin=admin,
                           total_users=total_users, total_txns=total_txns,
                           pending=pending_admin_with_risk,
                           pending_bank=pending_bank_with_risk,
                           pending_final=pending_final_with_risk,
                           flagged=flagged, total_volume=total_volume,
                           risk_dist=json.dumps(risk_dist))


# ── Bank Officer Applications ─────────────────────────────────────────────────

@admin_bp.route('/bank-applications', methods=['GET', 'POST'])
@admin_required
def bank_applications():
    admin = _get_admin()
    if request.method == 'POST':
        app_id      = request.form.get('application_id')
        application = BankOfficerApplication.get_or_404(app_id)

        if application.status != 'PENDING':
            flash('This application has already been reviewed.', 'warning')
            return redirect(url_for('admin.bank_applications'))

        action = request.form.get('action')
        if action == 'approve':
            phone_user = User.filter_by(phone=application.phone).first()
            email_user = User.filter_by(email=application.email).first()
            existing_officer = None
            if (phone_user and email_user and
                    phone_user.user_id == email_user.user_id and
                    phone_user.role == 'BANK_OFFICER'):
                existing_officer = phone_user

            if (phone_user or email_user) and not existing_officer:
                flash('This phone or email is already attached to an account.', 'danger')
                return redirect(url_for('admin.bank_applications'))

            if existing_officer:
                officer = existing_officer
                officer.name          = application.applicant_name
                officer.password_hash = application.password_hash
            else:
                officer = User(
                    name=application.applicant_name,
                    phone=application.phone,
                    email=application.email,
                    password_hash=application.password_hash,
                    role='BANK_OFFICER',
                    kyc_status='VERIFIED',
                )
            officer.save()

            application.status        = 'APPROVED'
            application.reviewed_by   = admin.user_id
            application.reviewed_at   = utcnow()
            application.officer_id    = officer.user_id
            application.review_reason = request.form.get('reason', '').strip()
            application.save()
            _audit('BANK_OFFICER_APPLICATION_APPROVED', admin.phone,
                   f'{application.bank_name}: {application.applicant_name}')
            flash('Bank officer application approved.', 'success')

        elif action == 'reject':
            reason = request.form.get('reason', '').strip()
            if not reason:
                flash('Please provide a rejection reason.', 'danger')
                return redirect(url_for('admin.bank_applications'))
            application.status        = 'REJECTED'
            application.review_reason = reason
            application.reviewed_by   = admin.user_id
            application.reviewed_at   = utcnow()
            application.save()
            _audit('BANK_OFFICER_APPLICATION_REJECTED', admin.phone,
                   f'{application.bank_name}: {application.applicant_name}. {reason}')
            flash('Bank officer application rejected.', 'info')

        return redirect(url_for('admin.bank_applications'))

    all_apps = BankOfficerApplication.query_all().all()
    all_apps.sort(key=lambda a: a.created_at or utcnow(), reverse=True)
    return render_template('admin/bank_applications.html',
                           admin=admin, applications=all_apps)


# ── Transaction Detail ────────────────────────────────────────────────────────

@admin_bp.route('/transaction/<txn_id>')
@admin_required
def transaction_detail(txn_id):
    admin       = _get_admin()
    txn         = Transaction.get_or_404(txn_id)
    ra          = RiskAssessment.filter_by(transaction_id=txn_id).first()
    ba          = BankApproval.filter_by(transaction_id=txn_id).first()
    verifs      = Verification.filter_by(transaction_id=txn_id).all()
    sender_acc  = Account.get(txn.sender_acc_id)
    sender_user = User.get(sender_acc.user_id) if sender_acc else None

    history = []
    if sender_acc:
        all_hist = Transaction.filter_by(sender_acc_id=sender_acc.account_id, status='EXECUTED').all()
        all_hist.sort(key=lambda t: t.created_at or utcnow(), reverse=True)
        history = all_hist[:10]

    return render_template('admin/transaction_detail.html',
                           admin=admin, txn=txn, risk=ra, bank_approval=ba,
                           verifications=verifs,
                           sender_account=sender_acc, sender_user=sender_user,
                           history=history)


# ── Forward to Bank ───────────────────────────────────────────────────────────

@admin_bp.route('/transaction/<txn_id>/forward-to-bank', methods=['POST'])
@admin_required
def forward_to_bank(txn_id):
    admin = _get_admin()
    txn   = Transaction.get_or_404(txn_id)

    if txn.status != 'PENDING_ADMIN_APPROVAL':
        flash('Transaction is not in the admin review queue.', 'warning')
        return redirect(url_for('admin.transaction_detail', txn_id=txn_id))

    ba = BankApproval.filter_by(transaction_id=txn_id).first()
    if not ba:
        ba = BankApproval(transaction_id=txn_id, status='PENDING')

    ba.status       = 'PENDING'
    ba.forwarded_by = admin.user_id
    ba.forwarded_at = utcnow()
    ba.save()

    txn.status = 'PENDING_BANK_APPROVAL'
    txn.save()

    _audit('ADMIN_FORWARDED_TO_BANK', admin.phone,
           f'Admin forwarded Rs {txn.amount:,.0f} to bank for approval', txn_id)

    sender_acc  = Account.get(txn.sender_acc_id)
    sender_user = User.get(sender_acc.user_id) if sender_acc else None
    if sender_user:
        _notify(sender_user.user_id,
                f'🏦 Your transfer of Rs {txn.amount:,.0f} has been forwarded to the bank.')

    flash('✅ Transaction forwarded to bank officer for approval.', 'success')
    return redirect(url_for('admin.dashboard'))


# ── Reject Transaction ────────────────────────────────────────────────────────

@admin_bp.route('/transaction/<txn_id>/reject', methods=['POST'])
@admin_required
def reject_transaction(txn_id):
    admin  = _get_admin()
    reason = request.form.get('reject_reason', '').strip()
    if not reason:
        flash('Rejection reason is required.', 'danger')
        return redirect(url_for('admin.transaction_detail', txn_id=txn_id))

    txn = Transaction.get_or_404(txn_id)
    if txn.status not in ('PENDING_ADMIN_APPROVAL', 'PENDING_ADMIN_FINAL_APPROVAL'):
        flash('Transaction cannot be rejected at this stage.', 'warning')
        return redirect(url_for('admin.transaction_detail', txn_id=txn_id))

    txn.status        = 'REJECTED'
    txn.reject_reason = reason
    txn.save()
    _audit('TRANSACTION_REJECTED', admin.phone, f'Admin rejected: {reason}', txn_id)

    sender_acc  = Account.get(txn.sender_acc_id)
    sender_user = User.get(sender_acc.user_id) if sender_acc else None
    if sender_user:
        _notify(sender_user.user_id,
                f'❌ Transfer of Rs {txn.amount:,.0f} to {txn.receiver_name} rejected. '
                f'Reason: {reason}')
    flash('Transaction rejected.', 'info')
    return redirect(url_for('admin.dashboard'))


# ── Final Approve ─────────────────────────────────────────────────────────────

@admin_bp.route('/transaction/<txn_id>/approve', methods=['POST'])
@admin_required
def approve_transaction(txn_id):
    admin = _get_admin()

    # Lock ensures only one request can approve at a time (replaces Firestore txn)
    with _approve_lock:
        txn = Transaction.get_or_404(txn_id)
        if txn.status != 'PENDING_ADMIN_FINAL_APPROVAL':
            flash('Transaction is already being processed or has been completed.', 'warning')
            return redirect(url_for('admin.transaction_detail', txn_id=txn_id))

        account = Account.get(txn.sender_acc_id)
        if not account:
            flash('Sender account not found.', 'danger')
            return redirect(url_for('admin.transaction_detail', txn_id=txn_id))

        if account.balance < txn.amount:
            flash('Insufficient balance; no debit was made.', 'warning')
            return redirect(url_for('admin.transaction_detail', txn_id=txn_id))

        # Mark as PROCESSING while we call the gateway
        txn.status = 'PROCESSING'
        txn.save()

    result = execute_transfer(txn)

    with _approve_lock:
        txn = Transaction.get(txn_id)   # re-read after gateway call
        account = Account.get(txn.sender_acc_id)

        if result['success']:
            if account.balance < txn.amount:
                txn.status        = 'FAILED'
                txn.reject_reason = 'Insufficient balance at settlement.'
                txn.save()
                flash('Settlement failed: insufficient balance.', 'danger')
                return redirect(url_for('admin.dashboard'))

            account.balance -= txn.amount
            account.save()

            txn.status         = 'EXECUTED'
            txn.bank_reference = result['reference']
            txn.save()

            _audit('TRANSACTION_APPROVED', admin.phone,
                   f'Final approved Rs {txn.amount:,.0f} – ref {result["reference"]}', txn_id)

            sender_user = User.get(account.user_id)
            if sender_user:
                _notify(sender_user.user_id,
                        f'✅ Transfer of Rs {txn.amount:,.0f} to {txn.receiver_name} executed. '
                        f'Ref: {result["reference"]}')
                call = CallLog(
                    transaction_id=txn_id, user_id=sender_user.user_id,
                    phone=sender_user.phone,
                    message=(f'Dear {sender_user.name}, your PayVanta payment of '
                             f'Rs {txn.amount:,.0f} to {txn.receiver_name} has been successfully '
                             f'processed. Reference: {result["reference"]}. '
                             f'If you did not authorize this, call 1800-XXX-XXXX immediately.'),
                    status='PLACED')
                call.save()
                _audit('CALL_PLACED', admin.phone,
                       f'Call placed to {sender_user.phone} for ref {result["reference"]}', txn_id)

            flash(f'✅ Transfer executed. Bank ref: {result["reference"]}.', 'success')

        else:
            txn.status        = 'FAILED'
            txn.reject_reason = result['message']
            txn.save()
            _audit('TRANSACTION_FAILED', admin.phone, result['message'], txn_id)

            sender_user = User.get(account.user_id)
            if sender_user:
                _notify(sender_user.user_id,
                        f'❌ Transfer of Rs {txn.amount:,.0f} failed: {result["message"]}')
            flash(f'Bank gateway failure: {result["message"]}', 'danger')

    return redirect(url_for('admin.dashboard'))


# ── User Management ───────────────────────────────────────────────────────────

@admin_bp.route('/users')
@admin_required
def users():
    admin     = _get_admin()
    all_users = User.filter_by(role='USER').all()
    all_users.sort(key=lambda u: u.created_at or utcnow(), reverse=True)
    return render_template('admin/users.html', admin=admin, users=all_users)


@admin_bp.route('/users/<user_id>', methods=['GET', 'POST'])
@admin_required
def user_detail(user_id):
    admin    = _get_admin()
    target   = User.get_or_404(user_id)
    accounts = Account.filter_by(user_id=user_id).all()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'verify_kyc':
            target.kyc_status = 'VERIFIED'
            target.save()
            _notify(target.user_id, '✅ Your KYC has been verified.')
            _audit('KYC_VERIFIED', admin.phone, f'KYC verified for {target.name}')
            flash('KYC verified.', 'success')
        elif action == 'reject_kyc':
            target.kyc_status = 'REJECTED'
            target.save()
            _notify(target.user_id, '❌ Your KYC was rejected. Please resubmit.')
            _audit('KYC_REJECTED', admin.phone, f'KYC rejected for {target.name}')
            flash('KYC rejected.', 'warning')
        elif action == 'freeze':
            for acc in accounts:
                acc.status = 'FROZEN'
                acc.save()
            _audit('ACCOUNT_FROZEN', admin.phone, f'Account frozen for {target.name}')
            flash('Account(s) frozen.', 'warning')
        elif action == 'unfreeze':
            for acc in accounts:
                acc.status = 'ACTIVE'
                acc.save()
            _audit('ACCOUNT_UNFROZEN', admin.phone, f'Account unfrozen for {target.name}')
            flash('Account(s) unfrozen.', 'success')

    txns = []
    if accounts:
        txns = Transaction.filter_by(sender_acc_id=accounts[0].account_id).all()
        txns.sort(key=lambda t: t.created_at or utcnow(), reverse=True)
        txns = txns[:20]
    return render_template('admin/user_detail.html',
                           admin=admin, target=target, accounts=accounts, txns=txns)


# ── Blacklist ─────────────────────────────────────────────────────────────────

@admin_bp.route('/blacklist', methods=['GET', 'POST'])
@admin_required
def blacklist():
    admin = _get_admin()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            acc_num = request.form.get('account_number', '').strip()
            reason  = request.form.get('reason', '').strip()
            if acc_num and reason:
                if not Blacklist.filter_by(account_number=acc_num).first():
                    bl = Blacklist(account_number=acc_num, reason=reason, added_by=admin.phone)
                    bl.save()
                    _audit('BLACKLIST_ADDED', admin.phone, f'{acc_num}: {reason}')
                    flash(f'Account {acc_num} blacklisted.', 'success')
                else:
                    flash('Already blacklisted.', 'warning')
        elif action == 'remove':
            bl_id = request.form.get('bl_id')
            bl = Blacklist.get(bl_id)
            if bl:
                _audit('BLACKLIST_REMOVED', admin.phone, f'{bl.account_number} removed')
                bl.delete()
                flash('Removed from blacklist.', 'info')

    entries = Blacklist.query_all().all()
    entries.sort(key=lambda e: e.created_at or utcnow(), reverse=True)
    return render_template('admin/blacklist.html', admin=admin, entries=entries)


# ── Audit Logs ────────────────────────────────────────────────────────────────

@admin_bp.route('/audit-logs')
@admin_required
def audit_logs():
    admin        = _get_admin()
    event_filter = request.args.get('event', '')
    actor_filter = request.args.get('actor', '')

    logs = AuditLog.query_all().all()
    if event_filter:
        logs = [l for l in logs if event_filter.lower() in (l.event_type or '').lower()]
    if actor_filter:
        logs = [l for l in logs if actor_filter.lower() in (l.actor or '').lower()]
    logs.sort(key=lambda l: l.timestamp or utcnow(), reverse=True)
    logs = logs[:500]

    if request.args.get('export') == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['log_id','transaction_id','event_type','actor','timestamp','details'])
        for log in logs:
            writer.writerow([log.log_id, log.transaction_id, log.event_type,
                             log.actor,
                             log.timestamp.isoformat() if log.timestamp else '',
                             log.details])
        output.seek(0)
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment;filename=audit_logs.csv'})

    return render_template('admin/audit_logs.html', admin=admin, logs=logs,
                           event_filter=event_filter, actor_filter=actor_filter)
