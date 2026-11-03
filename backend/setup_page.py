"""Server-rendered pairing page for the Blink account."""

SETUP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Blink Live Bridge — Pair</title>
<style>
  :root{
    --ink:#fcfcfa; --muted:#939293; --base:#2d2a2e; --deep:#221f22;
    --line:#403e41; --red:#ff6188; --orange:#fc9867; --yellow:#ffd866;
    --green:#a9dc76; --cyan:#78dce8; --violet:#ab9df2;
  }
  *{box-sizing:border-box}
  html,body{margin:0;min-height:100%;background:var(--deep);color:var(--ink)}
  body{
    font:15px/1.55 "IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    background:
      radial-gradient(900px 500px at 15% -10%, rgba(255,97,136,.12), transparent 70%),
      radial-gradient(700px 420px at 100% 0%, rgba(120,220,232,.08), transparent 70%),
      var(--deep);
  }
  a{color:var(--cyan)}
  .grain{position:fixed;inset:0;pointer-events:none;opacity:.15;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.35'/%3E%3C/svg%3E");}
  .wrap{position:relative;width:min(1060px,92vw);margin:0 auto;padding:52px 0 64px}
  .top{display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid var(--line);padding-bottom:18px}
  .brand{display:flex;align-items:center;gap:14px}
  .mark{width:34px;height:34px;display:grid;grid-template-columns:1fr 1fr;gap:3px;transform:rotate(45deg)}
  .mark i{background:var(--yellow);display:block}
  .mark i:nth-child(2){background:var(--red)}
  .mark i:nth-child(3){background:var(--violet)}
  .mark i:nth-child(4){background:var(--cyan)}
  .brand b{letter-spacing:.18em;font-size:13px}
  .brand span{display:block;color:var(--muted);font-size:10px;letter-spacing:.18em;margin-top:3px}
  .pill{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);padding:8px 12px;font-size:11px;letter-spacing:.12em;color:var(--muted)}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--yellow);box-shadow:0 0 12px currentColor}
  .dot.ok{background:var(--green)} .dot.bad{background:var(--red)}
  h1{font-size:clamp(38px,7vw,76px);line-height:.94;letter-spacing:-.045em;margin:58px 0 12px;font-weight:800;max-width:780px}
  h1 em{font-style:normal;color:var(--yellow)}
  .lede{max-width:720px;color:var(--muted);font-size:14px;margin:0 0 38px}
  .grid{display:grid;grid-template-columns:1.15fr .85fr;gap:18px;align-items:start}
  @media(max-width:790px){.grid{grid-template-columns:1fr}.wrap{padding-top:30px}h1{margin-top:36px}}
  .card{border:1px solid var(--line);background:rgba(45,42,46,.72);backdrop-filter:blur(8px);padding:26px}
  .card.dark{background:rgba(34,31,34,.72)}
  .eyebrow{font-size:10px;letter-spacing:.22em;text-transform:uppercase;color:var(--orange);margin:0 0 16px}
  h2{font-size:20px;letter-spacing:-.02em;margin:0 0 8px}
  p{margin:0 0 16px}
  label{display:block;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 7px}
  input{width:100%;background:var(--deep);border:1px solid var(--line);color:var(--ink);padding:14px 13px;font:inherit;outline:none}
  input:focus{border-color:var(--yellow);box-shadow:0 0 0 1px var(--yellow)}
  input::placeholder{color:#6a686b}
  .field{margin-bottom:16px}
  button{font:inherit;font-weight:700;letter-spacing:.06em;border:1px solid var(--yellow);background:var(--yellow);color:#221f22;padding:13px 19px;cursor:pointer}
  button:hover{filter:brightness(1.06)}
  button:disabled{opacity:.55;cursor:wait}
  button.ghost{background:transparent;color:var(--muted);border-color:var(--line)}
  button.danger{background:transparent;color:var(--red);border-color:rgba(255,97,136,.4)}
  .actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:6px}
  .msg{display:none;margin:16px 0 0;padding:12px 13px;border-left:2px solid var(--red);background:rgba(255,97,136,.08);font-size:13px}
  .msg.show{display:block}
  .msg.ok{border-color:var(--green);background:rgba(169,220,118,.08)}
  .steps{list-style:none;margin:0;padding:0;counter-reset:s}
  .steps li{position:relative;padding:0 0 18px 36px;color:var(--muted);font-size:13px;counter-increment:s}
  .steps li::before{content:counter(s,decimal-leading-zero);position:absolute;left:0;top:0;color:var(--violet);font-size:10px;letter-spacing:.1em}
  .steps b{color:var(--ink);display:block;font-size:13px;margin-bottom:3px}
  .url{display:block;margin-top:10px;padding:10px 12px;border:1px dashed var(--line);color:var(--cyan);word-break:break-all;font-size:12px}
  .foot{margin-top:32px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:11px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}
  .cams{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
  .cam{border:1px solid var(--line);padding:8px 10px;font-size:11px;color:var(--ink)}
  .cam span{color:var(--muted)}
  .hidden{display:none!important}
  .code{letter-spacing:.45em;font-size:24px;text-align:center}
  .mono{color:var(--muted);font-size:11px}
</style>
</head>
<body>
<div class="grain"></div>
<main class="wrap">
  <header class="top">
    <div class="brand"><span class="mark"><i></i><i></i><i></i><i></i></span>
      <div><b>BLINK / LIVE</b><span>LOCAL BRIDGE</span></div>
    </div>
    <div class="pill"><span id="healthDot" class="dot"></span><span id="healthText">CHECKING BRIDGE</span></div>
  </header>

  <h1>One quiet<br><em>handshake.</em></h1>
  <p class="lede">This bridge runs on your LAN. It signs in to Blink, keeps the session on this machine, and relays your cameras to the TV. Your password is used once and never written to disk.</p>

  <div class="grid">
    <section class="card" id="authCard">
      <p class="eyebrow" id="stepLabel">Step 01 / Account</p>
      <div id="stepOne">
        <h2>Link your Blink account</h2>
        <p class="mono">Use the account that owns the cameras. Blink will email a verification code.</p>
        <form id="loginForm" autocomplete="on">
          <div class="field"><label for="email">Blink email</label>
            <input id="email" name="email" type="email" autocomplete="username" placeholder="you@example.com" required>
          </div>
          <div class="field"><label for="password">Password</label>
            <input id="password" name="password" type="password" autocomplete="current-password" placeholder="••••••••••••" required>
          </div>
          <div class="actions"><button id="loginBtn" type="submit">Send sign-in code</button></div>
        </form>
        <div class="msg" id="loginMsg" role="alert"></div>
      </div>

      <div id="stepTwo" class="hidden">
        <h2>Enter the email code</h2>
        <p class="mono">Blink sent a one-time code. It usually arrives within a minute.</p>
        <form id="codeForm">
          <div class="field"><label for="code">Verification code</label>
            <input id="code" class="code" name="code" inputmode="numeric" pattern="[0-9]*" maxlength="8" autocomplete="one-time-code" placeholder="000000" required>
          </div>
          <div class="actions"><button id="codeBtn" type="submit">Verify and link</button>
          <button id="backBtn" type="button" class="ghost">Use another account</button></div>
        </form>
        <div class="msg" id="codeMsg" role="alert"></div>
      </div>

      <div id="linked" class="hidden">
        <h2>Bridge linked</h2>
        <p class="mono">The TV can now open live views. Keep this machine awake and on the LAN.</p>
        <div class="cams" id="camList"></div>
        <div class="actions" style="margin-top:22px">
          <button id="resetBtn" type="button" class="danger">Unlink account</button>
        </div>
        <div class="msg ok" id="linkedMsg">Ready. Open the Blink Live app on your TV.</div>
      </div>
    </section>

    <aside class="card dark">
      <p class="eyebrow">How it works</p>
      <ol class="steps">
        <li><b>Sign in once</b>Password and 2FA are exchanged directly with Blink over TLS. Only a refresh token stays here.</li>
        <li><b>Point the TV at this host</b>The app defaults to <span id="hostHint">this bridge</span>. Change it with the red key if your server moves.</li>
        <li><b>Watch live</b>Select a camera with the remote. Video and camera audio are remuxed locally, never uploaded.</li>
      </ol>
      <p class="eyebrow" style="margin-top:26px">Bridge address</p>
      <span class="url" id="bridgeUrl">…</span>
      <p class="mono" style="margin-top:14px">LAN only. Do not expose this port to the internet. Blink may end a live view after about five minutes; the app reconnects automatically.</p>
    </aside>
  </div>

  <footer class="foot">
    <span>BLINK LIVE BRIDGE · <span id="version">v1.0.0</span></span>
    <span>Not affiliated with Blink or Amazon.</span>
  </footer>
</main>

<script>
(() => {
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || '';
  const $ = (id) => document.getElementById(id);
  const bridgeUrl = `${location.protocol}//${location.host}`;
  $('bridgeUrl').textContent = bridgeUrl;
  $('hostHint').textContent = location.host;

  const headers = {'Content-Type': 'application/json'};
  if (token) headers['X-Bridge-Token'] = token;

  const api = async (path, options = {}) => {
    const res = await fetch(path, { ...options, headers: {...headers, ...(options.headers || {})} });
    const data = await res.json().catch(() => ({error: `HTTP ${res.status}`}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  };

  const showMsg = (el, text, ok = false) => {
    el.textContent = text;
    el.classList.add('show');
    el.classList.toggle('ok', ok);
  };
  const clearMsg = (el) => { el.textContent = ''; el.classList.remove('show', 'ok'); };
  const busy = (button, on, label) => {
    button.disabled = on;
    if (on) { button.dataset.label = button.textContent; button.textContent = label; }
    else if (button.dataset.label) button.textContent = button.dataset.label;
  };

  const setLinked = (cameras = []) => {
    $('stepOne').classList.add('hidden');
    $('stepTwo').classList.add('hidden');
    $('linked').classList.remove('hidden');
    $('stepLabel').textContent = 'Ready / Cameras';
    $('camList').textContent = '';
    cameras.forEach(cam => {
      const el = document.createElement('div');
      el.className = 'cam';
      el.textContent = cam.display_name || cam.name;
      const meta = document.createElement('span');
      meta.textContent = `  ${String(cam.kind || 'camera').toUpperCase()}${cam.online ? ' · ONLINE' : ' · OFFLINE'}`;
      el.appendChild(meta);
      $('camList').appendChild(el);
    });
    if (!cameras.length) {
      const el = document.createElement('div');
      el.className = 'cam';
      el.textContent = 'No cameras reported yet — refresh in a moment.';
      $('camList').appendChild(el);
    }
  };

  const refresh = async () => {
    try {
      const health = await api('/api/health');
      $('version').textContent = health.version || 'v1.0.0';
      $('healthDot').className = 'dot ' + (health.blink.ready ? 'ok' : 'bad');
      $('healthText').textContent = health.blink.ready ? 'BLINK LINKED' : 'BLINK NOT LINKED';
      if (health.blink.ready) {
        const cams = await api('/api/cameras');
        setLinked(cams.cameras || []);
      }
    } catch (err) {
      $('healthDot').className = 'dot bad';
      $('healthText').textContent = 'BRIDGE UNREACHABLE';
    }
  };

  $('loginForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    clearMsg($('loginMsg'));
    const button = $('loginBtn');
    busy(button, true, 'Contacting Blink…');
    try {
      const result = await api('/api/auth/start', {
        method: 'POST',
        body: JSON.stringify({ email: $('email').value, password: $('password').value }),
      });
      if (result.status === 'ready') {
        await refresh();
      } else if (result.status === '2fa_required') {
        $('stepOne').classList.add('hidden');
        $('stepTwo').classList.remove('hidden');
        $('stepLabel').textContent = 'Step 02 / Verify';
        $('code').focus();
        showMsg($('codeMsg'), 'Code sent. Check the inbox for this account.', true);
      } else {
        showMsg($('loginMsg'), result.message || 'Sign-in failed.');
      }
    } catch (err) {
      showMsg($('loginMsg'), err.message || 'Sign-in failed.');
    } finally {
      busy(button, false);
    }
  });

  $('codeForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    clearMsg($('codeMsg'));
    const button = $('codeBtn');
    busy(button, true, 'Verifying…');
    try {
      const result = await api('/api/auth/verify', {
        method: 'POST',
        body: JSON.stringify({ code: $('code').value }),
      });
      if (result.status === 'ready') await refresh();
      else showMsg($('codeMsg'), result.message || 'That code was not accepted.');
    } catch (err) {
      showMsg($('codeMsg'), err.message || 'Verification failed.');
    } finally {
      busy(button, false);
    }
  });

  $('backBtn').addEventListener('click', () => {
    $('stepTwo').classList.add('hidden');
    $('stepOne').classList.remove('hidden');
    $('stepLabel').textContent = 'Step 01 / Account';
    $('password').value = '';
    clearMsg($('loginMsg'));
  });

  $('resetBtn').addEventListener('click', async () => {
    if (!confirm('Unlink this Blink account from the bridge?')) return;
    try {
      await api('/api/auth/reset', {method: 'POST', body: '{}'});
      location.reload();
    } catch (err) {
      showMsg($('linkedMsg'), err.message || 'Could not unlink.');
    }
  });

  refresh();
})();
</script>
</body>
</html>
"""


def render_setup_page() -> str:
    return SETUP_HTML
