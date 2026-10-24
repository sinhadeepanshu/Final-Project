/* PayVanta AI – Main JS */

// ── OTP Input Handling ───────────────────────────────────────────────────────
function initOtpInputs() {
  const inputs = document.querySelectorAll('.otp-input');
  if (!inputs.length) return;

  inputs.forEach((input, idx) => {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !input.value && idx > 0) {
        inputs[idx - 1].focus();
      }
    });

    input.addEventListener('input', (e) => {
      // Only keep digits
      input.value = input.value.replace(/\D/g, '').slice(0, 1);
      if (input.value && idx < inputs.length - 1) {
        inputs[idx + 1].focus();
      }
    });

    input.addEventListener('paste', (e) => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g,'');
      [...text].slice(0, inputs.length).forEach((ch, i) => {
        if (inputs[i]) inputs[i].value = ch;
      });
      const last = Math.min(text.length, inputs.length) - 1;
      if (inputs[last]) inputs[last].focus();
    });
  });

  // Auto-focus first empty input
  const firstEmpty = [...inputs].find(i => !i.value);
  if (firstEmpty) firstEmpty.focus();
}

// ── OTP Timer ────────────────────────────────────────────────────────────────
function initOtpTimer(seconds) {
  const el = document.getElementById('otp-countdown');
  const btn = document.getElementById('resend-btn');
  if (!el) return;

  let remaining = seconds;
  function tick() {
    if (remaining <= 0) {
      el.textContent = 'OTP expired.';
      if (btn) { btn.disabled = false; btn.classList.remove('disabled'); }
      return;
    }
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    el.textContent = `OTP expires in ${m}:${s.toString().padStart(2, '0')}`;
    remaining--;
    setTimeout(tick, 1000);
  }
  tick();
}

// ── Resend Cooldown ──────────────────────────────────────────────────────────
function initResendCooldown(seconds) {
  const btn = document.getElementById('resend-btn');
  if (!btn) return;
  btn.disabled = true;
  let remaining = seconds;

  function tick() {
    if (remaining <= 0) {
      btn.disabled = false;
      btn.textContent = 'Resend OTP';
      return;
    }
    btn.textContent = `Resend in ${remaining}s`;
    remaining--;
    setTimeout(tick, 1000);
  }
  tick();
}

// ── Flash Message Auto-Dismiss ───────────────────────────────────────────────
function initFlashDismiss() {
  setTimeout(() => {
    document.querySelectorAll('.flash').forEach(el => {
      el.style.transition = 'opacity .5s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 500);
    });
  }, 5000);
}

// ── Amount Formatting (Indian) ────────────────────────────────────────────────
function formatINR(n) {
  n = parseInt(n);
  if (isNaN(n)) return '';
  const s = n.toString();
  if (s.length <= 3) return s;
  const last3 = s.slice(-3);
  const rest = s.slice(0, -3);
  return rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3;
}

function initAmountInput() {
  const inp = document.getElementById('amount-input');
  const display = document.getElementById('amount-display');
  if (!inp) return;
  inp.addEventListener('input', () => {
    const v = inp.value.replace(/[^0-9]/g, '');
    if (display) display.textContent = v ? 'Rs ' + formatINR(v) : '';
  });
}

// ── Confirm Dialog ───────────────────────────────────────────────────────────
function confirmAction(msg) {
  return confirm(msg || 'Are you sure?');
}

// ── Sidebar Active Link ──────────────────────────────────────────────────────
function initActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.sidebar-nav a').forEach(link => {
    if (link.getAttribute('href') === path) {
      link.classList.add('active');
    }
  });
}

// ── Chart helpers ─────────────────────────────────────────────────────────────
function initSpendingChart(canvasId, data) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return;
  const labels = Object.keys(data);
  const values = Object.values(data).map(v => Math.round(v / 1000));
  new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Amount (₹K)',
        data: values,
        backgroundColor: 'rgba(26,86,219,.7)',
        borderRadius: 6,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => '₹' + v + 'K' } } }
    }
  });
}

function initRiskPieChart(canvasId, data) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return;
  new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['LOW', 'MEDIUM', 'HIGH'],
      datasets: [{
        data: [data.LOW || 0, data.MEDIUM || 0, data.HIGH || 0],
        backgroundColor: ['#16a34a', '#d97706', '#dc2626'],
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      cutout: '65%',
      plugins: { legend: { position: 'bottom' } }
    }
  });
}

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initOtpInputs();
  initFlashDismiss();
  initAmountInput();
  initActiveNav();
});
