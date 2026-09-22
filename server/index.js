'use strict';

require('dotenv').config({ quiet: true });

const express = require('express');
const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const cors = require('cors');
const { Server } = require('socket.io');
const mediasoup = require('mediasoup');
const uuidv4 = () => crypto.randomUUID(); // ex-uuid: ora nativo Node (crypto)

const config = require('./config');
const Room = require('./Room');
const connlog = require('./connlog');
const updateCheck = require('./update-check');
const db = require('./db');
const { sendInvite, sendRaw, getSmtpStatus } = require('./mailer');
const { t: i18nT, locale: defaultLocale, normalizeLanguage } = require('./i18n');
const { syncUsersFromEnv } = require('./users-env');
const brand = require('./brand');
const bcrypt = require('bcryptjs');

// ⭐ Safety net: log invece di crashare il processo su errori asincroni inattesi.
// Mediasoup ritorna Promise da rtpObserver.removeProducer/addProducer e altri metodi:
// se il producer è già stato chiuso (es. race su disconnect/cleanup), rejecta con
// "Producer not found" → senza handler, Node ≥15 termina il processo.
// Loggare e continuare è la scelta giusta: la stanza si autoripara al prossimo evento.
process.on('unhandledRejection', (reason, promise) => {
  console.error('[unhandledRejection]', reason?.message || reason);
  // Niente process.exit: vogliamo che il server resti vivo per le altre stanze.
});
process.on('uncaughtException', (err) => {
  console.error('[uncaughtException]', err?.message || err, err?.stack);
  // Anche qui: log + continua. Errori veri (memoria esaurita, ecc.) il SO ci penserà.
});

// ─── Auth helpers ─────────────────────────────────────────────────────────────
// Gli utenti stanno SOLO nel DB (bcrypt). Si creano da Impostazioni → Utenti o
// con USERS in .env (users-env.js). Il vecchio fallback SHA256 hardcoded è rimosso:
// tutti gli utenti esistenti erano già stati migrati a bcrypt.
const _DUMMY_HASH = bcrypt.hashSync('timing-equalizer', 10);
async function verifyUserPassword(username, password) {
  if (!username || !password) return null;
  const dbUser = db.getUser(String(username).toLowerCase());
  if (!dbUser) {
    // confronto finto: stesso tempo di risposta con utente inesistente (anti user-enumeration)
    await bcrypt.compare(String(password), _DUMMY_HASH).catch(() => false);
    return null;
  }
  try {
    const ok = await bcrypt.compare(String(password), dbUser.password_hash);
    return ok ? { ok: true, displayName: dbUser.display_name || dbUser.username, role: dbUser.role || 'host' } : null;
  } catch (_) { return null; }
}

function getDisplayName(username) {
  if (!username) return username;
  const dbUser = db.getUser(String(username).toLowerCase());
  return (dbUser && dbUser.display_name) || username;
}

// ⭐ FIX BUG A: helper base64url (URL-safe, no /,+,=) e ri-padding per decode.
// I token vecchi erano in base64 standard → contenevano '/','+','=' → quando finivano
// in URL `/join/TOKEN` rompevano il routing Express in alcuni client email (Outlook
// percent-encoda diversamente da Gmail), generando 404 / redirect alla home / token
// non verificabili. Soluzione standard: base64url RFC 4648 §5.
// La verify accetta sia il nuovo formato che il vecchio per non invalidare token già emessi.
function _b64urlEncode(buf) {
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function _b64urlDecode(s) {
  // Accetta sia base64url (-,_) che base64 standard (+,/) per retrocompat
  let str = String(s).replace(/-/g, '+').replace(/_/g, '/');
  // Ri-padding
  while (str.length % 4) str += '=';
  return Buffer.from(str, 'base64');
}

function generateToken(username, extra = {}) {
  const payload = JSON.stringify({ username, ...extra, exp: Math.floor(Date.now() / 1000) + config.auth.tokenExpiresSec });
  const b64 = _b64urlEncode(Buffer.from(payload));
  const sig = crypto.createHmac('sha256', config.server.secret).update(b64).digest('hex');
  return `${b64}.${sig}`;
}

function verifyToken(token) {
  if (!token) return null;
  try {
    const [b64, sig] = String(token).split('.');
    if (!b64 || !sig) return null;
    const expected = crypto.createHmac('sha256', config.server.secret).update(b64).digest('hex');
    // Confronto constant-time per impedire timing attacks
    if (sig.length !== expected.length) return null;
    if (!crypto.timingSafeEqual(Buffer.from(sig, 'hex'), Buffer.from(expected, 'hex'))) return null;
    const payload = JSON.parse(_b64urlDecode(b64).toString());
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch { return null; }
}

// Token guest (non richiede username registrato, solo roomId valido)
function generateGuestToken(roomId, expHours) {
  if (!expHours) expHours = config.auth.guestTokenDefaultHours;
  const payload = JSON.stringify({ guest: true, roomId, exp: Math.floor(Date.now() / 1000) + expHours * 3600 });
  const b64 = _b64urlEncode(Buffer.from(payload));
  const sig = crypto.createHmac('sha256', config.server.secret).update(b64).digest('hex');
  return `${b64}.${sig}`;
}

function verifyGuestToken(token) {
  const payload = verifyToken(token);
  return payload?.guest === true ? payload : null;
}

function _reqToken(req) { return req.headers['x-auth-token'] || req.query.token; }

// ⭐ R12: oltre alla firma, l'utente deve ESISTERE ancora nel DB (prima un utente
// eliminato restava dentro fino alla scadenza del token, 8 ore).
function authMiddleware(req, res, next) {
  const payload = verifyToken(_reqToken(req));
  if (!payload || payload.guest) return res.status(401).json({ error: 'Non autorizzato' });
  const u = db.getUser(payload.username);
  if (!u) return res.status(401).json({ error: 'Utente non più valido' });
  req.user = { username: u.username, displayName: u.display_name || u.username, role: u.role || 'host', source: u.source || 'ui' };
  next();
}

function adminMiddleware(req, res, next) {
  authMiddleware(req, res, () => {
    if (req.user.role !== 'admin') return res.status(403).json({ error: 'Serve un account amministratore' });
    next();
  });
}

// ─── Rate limit login ─────────────────────────────────────────────────────────

function getClientIp(req) {
  // ⭐ FIX R11: prima si prendeva il PRIMO valore di X-Forwarded-For, che è
  // quello scritto dal client → chiunque poteva aggirare il rate limit login
  // mandando un XFF diverso a ogni tentativo. Ora Express risolve l'IP reale
  // fidandosi SOLO dei proxy in TRUST_PROXY (default: reti private = NPMplus).
  return req.ip || req.socket?.remoteAddress || 'unknown';
}

function checkLoginRateLimit(ip) {
  const max = config.auth.loginMaxAttempts;
  const win = config.auth.loginLockoutMin;
  const n = db.countRecentFailedLogins(ip, win);
  if (n >= max) {
    const err = new Error(`Troppi tentativi di login. Riprova tra ${win} minuti.`);
    err.status = 429;
    throw err;
  }
}

// ─── Express ──────────────────────────────────────────────────────────────────

const app = express();
// ⭐ FIX R11: proxy fidati per X-Forwarded-For (NPMplus sta su rete privata).
// Override con TRUST_PROXY=<ip/cidr,...> o TRUST_PROXY=false se esposto diretto.
{
  const tp = (process.env.TRUST_PROXY || 'loopback, linklocal, uniquelocal').trim();
  app.set('trust proxy', tp === 'false' ? false : tp.split(',').map(x => x.trim()).filter(Boolean));
}

// CORS: origins da env CORS_ORIGINS, wildcard solo se non configurato (sviluppo)
const _corsOrigins = config.server.corsOrigins;
if (_corsOrigins.length > 0) {
  app.use(cors({
    origin: function (origin, cb) {
      // Permetti richieste senza origin (mobile apps, curl, server-to-server)
      if (!origin) return cb(null, true);
      if (_corsOrigins.includes(origin)) return cb(null, true);
      return cb(new Error('Origin non permessa: ' + origin));
    },
    credentials: true,
  }));
} else {
  console.warn('[CORS] CORS_ORIGINS non configurato — CORS aperto (ok solo in sviluppo)');
  app.use(cors());
}
app.use(express.json());

// Serve mediasoup-client dal bundle compilato in build
app.get('/assets/js/mediasoup-client.min.js', (req, res) => {
  const p = path.join(__dirname, '../public/assets/js/mediasoup-client.min.js');
  if (fs.existsSync(p)) return res.sendFile(p);
  res.redirect('https://cdn.jsdelivr.net/npm/mediasoup-client@3.7.19/lib/mediasoup-client.min.js');
});

// ⭐ FIX ordine middleware: il nosniff su /assets/uploads era registrato DOPO
// express.static → static serviva i file prima e l'header non veniva MAI
// applicato. Deve stare PRIMA della static.
app.use('/assets/uploads', (req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  next();
});

// Le pagine HTML passano SOLO dalle route (brand iniettato): niente .html statici diretti
app.use((req, res, next) => {
  if (/\.html?$/i.test(req.path)) return res.status(404).end();
  next();
});
app.use(express.static(path.join(__dirname, '../public'), { index: false }));
const PUBLIC_DIR = path.join(__dirname, '../public');
const sendPage = (res, file, extraHead = '') => brand.sendPage(res, path.join(PUBLIC_DIR, file), db, config, extraHead);

// ── Pagine ────────────────────────────────────────────────────────────────────
app.get('/', (req, res) => sendPage(res, 'login.html'));
app.get('/home', (req, res) => sendPage(res, 'index.html'));
app.get('/schedule', (req, res) => sendPage(res, 'schedule.html'));
app.get('/room/:id', (req, res) => sendPage(res, 'room.html'));
app.get('/settings', (req, res) => sendPage(res, 'settings.html'));
app.get('/join/:token', (req, res) => {
  const payload = verifyGuestToken(req.params.token);

  if (!payload) {
    const b = brand.getBrand(db, config);
    return res.status(400).send(`<!DOCTYPE html>
<html lang="it"><head><meta charset="UTF-8"><title>Link non valido · ${brand.esc(b.platformName)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f6f8;color:#16181d}div{text-align:center;padding:24px}h2{font-weight:500;margin:0 0 8px}p{color:#565c67;margin:0}</style>
</head><body><div><h2>Questo link non è valido o è scaduto</h2><p>Chiedi a chi ti ha invitato un nuovo link.</p></div></body></html>`);
  }

  const roomId = payload.roomId;
  const b = brand.getBrand(db, config);
  const baseUrl = process.env.BASE_URL || `${req.protocol}://${req.get('host')}`;
  const pageUrl = `${baseUrl}/join/${req.params.token}`;
  const imageUrl = /^https?:/i.test(b.logoUrl) ? b.logoUrl : `${baseUrl}${b.logoUrl}`;
  const E = brand.esc;
  const ogTags = `
    <meta property="og:type" content="website" />
    <meta property="og:url" content="${E(pageUrl)}" />
    <meta property="og:title" content="Sei invitato a una riunione · ${E(b.platformName)}" />
    <meta property="og:description" content="Entra nella stanza ${E(roomId)} su ${E(b.platformName)}." />
    <meta property="og:image" content="${E(imageUrl)}" />
    <meta property="og:site_name" content="${E(b.platformName)}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="Sei invitato a una riunione · ${E(b.platformName)}" />
    <meta name="twitter:description" content="Entra nella stanza ${E(roomId)} su ${E(b.platformName)}." />
    <meta name="twitter:image" content="${E(imageUrl)}" />`;
  sendPage(res, 'guest.html', ogTags);
});

// ── Auth API ──────────────────────────────────────────────────────────────────
app.post('/api/login', async (req, res) => {
  const ip = getClientIp(req);
  try {
    checkLoginRateLimit(ip);
  } catch (e) {
    return res.status(e.status || 429).json({ error: e.message });
  }

  const { username, password } = req.body || {};
  if (!username || !password) return res.status(400).json({ error: 'Credenziali mancanti' });

  const result = await verifyUserPassword(username, password);
  if (!result) {
    db.recordLoginAttempt(ip, username, false);
    return res.status(401).json({ error: 'Credenziali errate' });
  }
  db.recordLoginAttempt(ip, username, true);
  const uname = username.toLowerCase();
  res.json({ token: generateToken(uname), displayName: result.displayName, role: result.role, expiresIn: config.auth.tokenExpiresSec });
});

app.get('/api/auth/verify', (req, res) => {
  const token = req.headers['x-auth-token'] || req.query.token;
  const payload = verifyToken(token);
  const u = payload && !payload.guest ? db.getUser(payload.username) : null;
  if (!u) return res.status(401).json({ valid: false });
  res.json({ valid: true, username: u.username, displayName: u.display_name || u.username, role: u.role || 'host' });
});

// ── Cambio password / nome dell'utente corrente ──────────────────────────────
app.post('/api/auth/change-password', authMiddleware, async (req, res) => {
  try {
    const { old_password, new_password } = req.body || {};
    if (!old_password || !new_password) return res.status(400).json({ error: 'Password attuale e nuova obbligatorie' });
    if (String(new_password).length < 8) return res.status(400).json({ error: 'Password troppo corta (min 8 caratteri)' });
    if (_envLocked(req.user.username)) return res.status(409).json({ error: 'Utente gestito da .env (USERS_MODE=sync): cambia la password lì' });
    const result = await verifyUserPassword(req.user.username, old_password);
    if (!result) return res.status(401).json({ error: 'Password attuale errata' });
    db.updateUser(req.user.username, { passwordHash: await bcrypt.hash(String(new_password), 12) });
    res.json({ ok: true });
  } catch (e) {
    console.error('[change-password]', e);
    res.status(500).json({ error: 'Errore interno' });
  }
});

// ── Gestione utenti (solo admin) ──────────────────────────────────────────────
// Utenti "env" in USERS_MODE=sync sono in sola lettura: .env è la fonte di verità.
let _envUsers = { mode: 'create', envUsernames: new Set() };
const _envLocked = (username) => _envUsers.mode === 'sync' && _envUsers.envUsernames.has(String(username).toLowerCase());
const _USERNAME_RE = /^[a-z0-9_.\-]{2,40}$/;
const _cleanRole = (r) => (r === 'admin' ? 'admin' : 'host');

app.get('/api/users', adminMiddleware, (req, res) => {
  res.json({
    mode: _envUsers.mode,
    users: db.listUsers().map(u => ({ ...u, locked: _envLocked(u.username), me: u.username === req.user.username })),
  });
});

app.post('/api/users', adminMiddleware, async (req, res) => {
  try {
    const { username, password, display_name, role } = req.body || {};
    const uname = String(username || '').toLowerCase().trim();
    if (!_USERNAME_RE.test(uname)) return res.status(400).json({ error: 'Username non valido (2-40 caratteri: a-z 0-9 _ - .)' });
    if (!password || String(password).length < 8) return res.status(400).json({ error: 'Password troppo corta (min 8 caratteri)' });
    if (db.getUser(uname)) return res.status(400).json({ error: 'Utente già esistente' });
    db.createUser({
      username: uname, passwordHash: await bcrypt.hash(String(password), 12),
      displayName: _safeStr(display_name, 50).trim() || uname, role: _cleanRole(role), source: 'ui',
    });
    res.json({ ok: true });
  } catch (e) {
    console.error('[create user]', e);
    res.status(500).json({ error: 'Errore interno' });
  }
});

app.put('/api/users/:username', adminMiddleware, async (req, res) => {
  try {
    const uname = String(req.params.username).toLowerCase().trim();
    const existing = db.getUser(uname);
    if (!existing) return res.status(404).json({ error: 'Utente non trovato' });
    if (_envLocked(uname)) return res.status(409).json({ error: 'Utente gestito da .env (USERS_MODE=sync): modificalo lì' });
    const { password, display_name, role } = req.body || {};
    const upd = {};
    if (password) {
      if (String(password).length < 8) return res.status(400).json({ error: 'Password troppo corta (min 8 caratteri)' });
      upd.passwordHash = await bcrypt.hash(String(password), 12);
    }
    if (display_name !== undefined) upd.displayName = _safeStr(display_name, 50).trim() || uname;
    if (role !== undefined) {
      const r = _cleanRole(role);
      if (existing.role === 'admin' && r !== 'admin' && db.countAdmins() <= 1) return res.status(400).json({ error: 'Deve restare almeno un amministratore' });
      upd.role = r;
    }
    db.updateUser(uname, upd);
    res.json({ ok: true });
  } catch (e) {
    console.error('[update user]', e);
    res.status(500).json({ error: 'Errore interno' });
  }
});

app.delete('/api/users/:username', adminMiddleware, (req, res) => {
  const uname = String(req.params.username).toLowerCase().trim();
  if (req.user.username === uname) return res.status(400).json({ error: 'Non puoi eliminare il tuo stesso utente' });
  const u = db.getUser(uname);
  if (!u) return res.status(404).json({ error: 'Utente non trovato' });
  if (_envLocked(uname)) return res.status(409).json({ error: 'Utente gestito da .env (USERS_MODE=sync): rimuovilo da lì' });
  if (u.role === 'admin' && db.countAdmins() <= 1) return res.status(400).json({ error: 'Deve restare almeno un amministratore' });
  db.deleteUser(uname);
  res.json({ ok: true });
});

// ── Stanze API ────────────────────────────────────────────────────────────────
app.post('/api/room', authMiddleware, (req, res) => {
  const { roomName } = req.body || {};

  let roomId;
  if (roomName && roomName.trim()) {
    // Sanifica: uppercase, spazi→trattino, solo A-Z 0-9 -, max 32 char
    roomId = roomName.trim()
      .toUpperCase()
      .replace(/\s+/g, '-')
      .replace(/[^A-Z0-9\-]/g, '')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 32);
  }
  // Fallback a UUID se il nome risulta vuoto dopo la sanitizzazione
  if (!roomId) roomId = uuidv4().split('-')[0].toUpperCase();

  res.json({ roomId, url: `/room/${roomId}` });
});

app.get('/api/room/:roomId', authMiddleware, (req, res) => {
  const room = rooms.get(req.params.roomId);
  if (!room) return res.status(404).json({ error: 'Stanza non trovata' });
  res.json(room.toJSON());
});

// Genera link invito guest per una stanza
app.post('/api/room/:roomId/invite', authMiddleware, (req, res) => {
  const { roomId } = req.params;
  const baseUrl = process.env.BASE_URL || `${req.protocol}://${req.get('host')}`;

  // ⭐ FIX schedule-link: se la stanza ha una meeting associata, riusa il
  // guest_token persistito invece di generarne uno nuovo a ogni click.
  // Prima ogni click di "Copia link" sul pannello pianifica produceva un token
  // diverso da quello mandato in email → confusione e durate incoerenti.
  // Per stanze ad-hoc senza meeting, comportamento invariato (genera token nuovo 48h).
  const meeting = db.getMeetingByRoomId(roomId);
  if (meeting && meeting.guest_token) {
    return res.json({ url: `${baseUrl}/join/${meeting.guest_token}`, token: meeting.guest_token, persisted: true });
  }

  const token = generateGuestToken(roomId, 48);
  res.json({ url: `${baseUrl}/join/${token}`, token, persisted: false });
});

// Verifica token guest (usato da guest.html)
app.get('/api/guest/verify/:token', (req, res) => {
  const payload = verifyGuestToken(req.params.token);
  if (!payload) return res.status(400).json({ valid: false, error: 'Token non valido o scaduto' });

  // ⭐ Countdown pre-riunione: se la stanza ha una meeting pianificata associata,
  // esponi titolo/orario/durata (nessun dato sensibile: niente invitees/notes).
  // guest.html mostra la pagina countdown finché mancano >10 min all'inizio.
  let meeting = null;
  try {
    const m = db.getMeetingByRoomId(payload.roomId);
    if (m) meeting = { title: m.title, scheduled_at: m.scheduled_at, duration_min: m.duration_min };
  } catch (e) { /* nessuna meeting associata: stanza ad-hoc */ }

  // ⭐ Sblocco anticipato: se un HOST è già in stanza, il guest può entrare
  // anche prima dei 10 min (countdown.js lo controlla al load e in polling).
  let hostInRoom = false;
  try {
    const r = rooms.get(payload.roomId);
    hostInRoom = !!r && [...r.peers.values()].some(p => !p.isGuest);
  } catch (e) { /* ignore */ }

  res.json({ valid: true, roomId: payload.roomId, meeting, hostInRoom, serverNow: Math.floor(Date.now() / 1000) });
});

// ⭐ FIX R11: normalizza e valida la lista invitati (max 50, formato email base)
const _EMAIL_RE = /^[^\s@<>"',;]+@[^\s@<>"',;]+\.[^\s@<>"',;]{2,}$/;
function _cleanInvitees(list) {
  if (!Array.isArray(list)) return [];
  const out = new Set();
  for (const e of list) {
    const v = String(e || '').trim().toLowerCase();
    if (v.length <= 254 && _EMAIL_RE.test(v)) out.add(v);
    if (out.size >= 50) break;
  }
  return [...out];
}

// ── Meetings API ──────────────────────────────────────────────────────────────
app.get('/api/meetings', authMiddleware, (req, res) => {
  res.json(db.getMeetings());
});

// Email invitati usate in precedenza (distinte, ordinate per frequenza)
app.get('/api/invitees', authMiddleware, (req, res) => {
  try {
    const freq = new Map();
    for (const m of db.getAllMeetings()) {
      for (const e of (m.invitees || [])) {
        const email = String(e).trim().toLowerCase();
        if (email.includes('@')) freq.set(email, (freq.get(email) || 0) + 1);
      }
    }
    const list = [...freq.entries()].sort((a, b) => b[1] - a[1]).map(([e]) => e);
    res.json(list);
  } catch (e) {
    res.json([]);
  }
});

app.post('/api/meetings', authMiddleware, async (req, res) => {
  try {
    const payload = { username: req.user.username };
    const displayName = req.user.displayName;
    const uiLanguage = normalizeLanguage(req.get('x-ui-language') || defaultLocale);

    const { title, scheduled_at, notes = '' } = req.body || {};
    if (!title || !scheduled_at) return res.status(400).json({ error: 'title e scheduled_at obbligatori' });
    if (isNaN(new Date(scheduled_at).getTime())) return res.status(400).json({ error: 'scheduled_at non valido' });
    // ⭐ FIX R11: invitati validati (prima qualsiasi stringa finiva a nodemailer)
    const invitees = _cleanInvitees(req.body.invitees);
    const duration_min = Math.max(5, Math.min(1440, parseInt(req.body.duration_min, 10) || 60));

    const roomId = uuidv4().split('-')[0].toUpperCase();
    const meetId = uuidv4();
    const baseUrl = process.env.BASE_URL || `${req.protocol}://${req.get('host')}`;

    // ⭐ FIX schedule-link: genera UN guest_token stabile per la meeting,
    // condiviso da tutte le email guest e dal pulsante "Copia link" del pannello.
    // Durata = fino a fine meeting + 24h di buffer, minimo 72h.
    // Prima ogni email aveva un token diverso (72h), ogni click su "Copia link"
    // ne generava ancora uno nuovo (48h) → confusione totale + durate incoerenti.
    const scheduledTs = Math.floor(new Date(scheduled_at).getTime() / 1000);
    const endTs = scheduledTs + duration_min * 60;
    const nowTs = Math.floor(Date.now() / 1000);
    const hoursUntilEndPlus24 = Math.ceil((endTs + 24 * 3600 - nowTs) / 3600);
    const tokenHours = Math.max(72, hoursUntilEndPlus24);
    const sharedGuestToken = generateGuestToken(roomId, tokenHours);

    const meeting = db.createMeeting({
      id: meetId, title, room_id: roomId,
      created_by: payload.username,
      scheduled_at: scheduledTs,
      duration_min, invitees, notes,
      guest_token: sharedGuestToken,   // ⭐ persistito una volta sola
    });

    // ⭐ FIX BUG B v3 — regola definitiva: TUTTI gli invitati sono GUEST.
    // Nessuna euristica su username/dominio: chiunque venga inserito tra gli
    // invitati riceve SEMPRE il link guest /join/<token> (con countdown).
    // L'UNICA email host (link /room/ diretto) è quella di conferma inviata
    // a HOST_NOTIFY_EMAIL qui sotto.
    const emailResults = [];
    for (const email of invitees) {
      const inviteUrl = `${baseUrl}/join/${sharedGuestToken}`;
      try {
        await sendInvite({
          to: email, meeting, guestUrl: inviteUrl, hostName: displayName,
          isHost: false, language: uiLanguage,
        });
        emailResults.push({ email, sent: true });
        console.log(`[meetings] invito guest → ${email}`);
      } catch (err) {
        console.error('[mailer]', email, err.message);
        emailResults.push({ email, sent: false, error: err.message });
      }
    }

    const hostUrl = `${baseUrl}/room/${roomId}`;
    // ⭐ L'email host arriva SOLO a questo indirizzo, sempre.
    // HOST_NOTIFY_EMAIL facoltativa: se vuota, nessuna email host.
    const hostEmail = (process.env.HOST_NOTIFY_EMAIL || '').trim();
    if (hostEmail) {
      try { await sendInvite({ to: hostEmail, meeting, guestUrl: hostUrl, hostName: displayName, isHost: true, language: uiLanguage }); } catch (e) { console.error('[mailer host]', e.message); }
    }

    res.json({ meeting, hostUrl, emailResults });
  } catch (err) {
    console.error('[meetings POST]', err);
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/meetings/:id', authMiddleware, (req, res) => {
  const id = req.params.id;
  // Utente autenticato = admin, può eliminare qualsiasi meeting
  // Leggo created_by dal record per passare il filtro della prepared statement
  let createdBy = 'external';
  try {
    if (typeof db.getMeeting === 'function') {
      const m = db.getMeeting(id);
      if (m && m.created_by) createdBy = m.created_by;
    }
  } catch (e) { }
  const deleted = db.deleteMeeting(id, createdBy);
  deleted ? res.json({ deleted: true }) : res.status(404).json({ error: 'Meeting non trovato' });
});

app.put('/api/meetings/:id', authMiddleware, async (req, res) => {
  const payload = { username: req.user.username };
  const { title, scheduled_at, notes } = req.body || {};
  const ts = new Date(scheduled_at).getTime();
  if (!title || isNaN(ts)) return res.status(400).json({ error: 'title e scheduled_at validi obbligatori' });
  const updated = db.updateMeeting({
    id: req.params.id, created_by: payload.username,
    title, scheduled_at: Math.floor(ts / 1000),
    duration_min: Math.max(5, Math.min(1440, parseInt(req.body.duration_min, 10) || 60)),
    invitees: _cleanInvitees(req.body.invitees), notes,
  });
  updated ? res.json(updated) : res.status(404).json({ error: 'Meeting non trovato' });
});

// ── Settings ─────────────────────────────────────────────────────────────────
// Diagnostica connessioni (host): tdConnLog() dalla console in stanza
app.get('/api/connlog', authMiddleware, (req, res) => {
  res.json({ events: connlog.list({ room: req.query.room, peer: req.query.peer, limit: req.query.limit }) });
});

// Pubblico (no auth): solo dati non sensibili
app.get('/api/settings/public', (req, res) => {
  const all = brand.getPublicSettings(db, config);
  res.json(all);
});

// Completo (admin): categorie modificabili + stato di sistema in sola lettura (.env)
app.get('/api/settings', adminMiddleware, (req, res) => {
  const pub = brand.getPublicSettings(db, config);
  res.json({
    ...pub,
    system: {
      appNameEnv: config.brand.appName,
      baseUrl: process.env.BASE_URL || '',
      smtp: getSmtpStatus(),
      turn: { host: (config.mediasoup.webRtcTransportOptions.iceServers[0]?.urls || '').replace(/^stun:/, ''), auth: !!(process.env.TURN_USER && process.env.TURN_PASSWORD) },
      announcedIp: process.env.ANNOUNCED_IP || '',
      hostNotifyEmail: process.env.HOST_NOTIFY_EMAIL || '',
      usersMode: _envUsers.mode,
      version: (() => { try { return require('./package.json').version; } catch { return ''; } })(),
    },
  });
});

app.post('/api/settings/:category', adminMiddleware, (req, res) => {
  try {
    const saved = brand.saveCategory(db, req.params.category, req.body || {});
    res.json({ ok: true, saved });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

// Test invio email (SMTP da .env)
app.post('/api/settings/smtp/test', adminMiddleware, async (req, res) => {
  const to = String((req.body || {}).to || '').trim();
  if (!_EMAIL_RE.test(to.toLowerCase())) return res.status(400).json({ error: 'Email destinatario non valida' });
  try {
    const b = brand.getBrand(db, config);
    const language = normalizeLanguage(req.get('x-ui-language') || defaultLocale);
    const tr = value => i18nT(value, language);
    await sendRaw({
      to, subject: tr('Email di prova · {0}').replace('{0}', b.platformName),
      text: tr('Se leggi questo messaggio, la configurazione SMTP funziona.'),
      html: `<p>${brand.esc(tr('Se leggi questo messaggio, la configurazione SMTP di {0} funziona.').replace('{0}', b.platformName))}</p>`,
    });
    res.json({ ok: true });
  } catch (e) {
    console.error('[smtp test]', e);
    res.status(500).json({ error: e.message });
  }
});

// ── Upload file (logo, background custom) ────────────────────────────────────
const UPLOAD_DIR = path.join(__dirname, '../public/assets/uploads');
if (!fs.existsSync(UPLOAD_DIR)) fs.mkdirSync(UPLOAD_DIR, { recursive: true });

// .svg rimosso dalla whitelist: gli SVG possono contenere <script> e sarebbero
// serviti da express.static → XSS stored nel dominio della videochat.
const ALLOWED_UPLOAD_EXTS = new Set(['png', 'jpg', 'jpeg', 'webp']);

// ⭐ FIX: `type: '*/*'` NON basta — se il browser manda il file senza header
// Content-Type (succede con fetch + ArrayBuffer) body-parser salta il parsing e
// il body arriva vuoto. Con `type: () => true` il corpo viene sempre letto.
app.post('/api/upload/:kind', authMiddleware, express.raw({ type: () => true, limit: '8mb' }), (req, res) => {
  const { kind } = req.params;  // 'logo' | 'favicon' | 'background'
  if (!['logo', 'favicon', 'background'].includes(kind)) return res.status(400).json({ error: 'kind non valido' });
  if (kind !== 'background' && req.user.role !== 'admin') return res.status(403).json({ error: 'Serve un account amministratore' });
  const ext = String(req.query.ext || 'png').toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 4);
  if (!ALLOWED_UPLOAD_EXTS.has(ext)) return res.status(400).json({ error: 'Formato non supportato (png/jpg/jpeg/webp)' });

  // Body può arrivare come Buffer (express.raw) oppure oggetto vuoto {} se il client non imposta content-type
  const body = req.body;
  if (!Buffer.isBuffer(body) || body.length === 0) {
    return res.status(400).json({ error: 'File vuoto o non ricevuto: riprova a selezionarlo' });
  }
  // ⭐ R12: verifica i magic bytes (prima bastava rinominare un file qualsiasi)
  const sig = body.subarray(0, 12);
  const isPng = sig[0] === 0x89 && sig[1] === 0x50 && sig[2] === 0x4E && sig[3] === 0x47;
  const isJpg = sig[0] === 0xFF && sig[1] === 0xD8 && sig[2] === 0xFF;
  const isWebp = sig.toString('ascii', 0, 4) === 'RIFF' && sig.toString('ascii', 8, 12) === 'WEBP';
  const okSig = (ext === 'png' && isPng) || ((ext === 'jpg' || ext === 'jpeg') && isJpg) || (ext === 'webp' && isWebp);
  if (!okSig) return res.status(400).json({ error: 'Il contenuto del file non corrisponde a un\'immagine ' + ext });

  // Filename con random crypto-safe (Date.now() era prevedibile → enumeration)
  const rand = crypto.randomBytes(8).toString('hex');
  const filename = `${kind}-${rand}.${ext}`;
  const fullPath = path.join(UPLOAD_DIR, filename);
  fs.writeFileSync(fullPath, body);
  res.json({ url: `/assets/uploads/${filename}` });
});

// ── Lista background disponibili (preset + uploaded) ─────────────────────────
app.get('/api/backgrounds', (req, res) => {
  const presets = [];
  const bgDir = path.join(__dirname, '../public/assets/backgrounds');
  if (fs.existsSync(bgDir)) {
    for (const f of fs.readdirSync(bgDir)) {
      if (/\.(jpg|jpeg|png|webp|svg)$/i.test(f)) {
        const label = f.replace(/\.\w+$/, '').replace(/[-_]/g, ' ');
        presets.push({ url: `/assets/backgrounds/${f}`, label: label.charAt(0).toUpperCase() + label.slice(1) });
      }
    }
  }

  const uploads = [];
  if (fs.existsSync(UPLOAD_DIR)) {
    for (const f of fs.readdirSync(UPLOAD_DIR)) {
      if (f.startsWith('background-') && /\.(jpg|jpeg|png|webp|svg)$/i.test(f)) {
        uploads.push({ url: `/assets/uploads/${f}`, label: 'Personalizzato' });
      }
    }
  }
  res.json({ presets, uploads });
});

// ─── External API (integrazione CRM calendario) ──────────────────────────────
const externalApi = require('./external-api');
externalApi.install(app, { generateGuestToken, config, db });

// ─── HTTP/HTTPS ───────────────────────────────────────────────────────────────
let server;
const sslKey = process.env.SSL_KEY;
const sslCert = process.env.SSL_CERT;
if (sslKey && sslCert && fs.existsSync(sslKey) && fs.existsSync(sslCert)) {
  server = https.createServer({ key: fs.readFileSync(sslKey), cert: fs.readFileSync(sslCert) }, app);
  console.log('[Meet] HTTPS');
} else {
  server = http.createServer(app);
  console.log('[Meet] HTTP (usa reverse proxy HTTPS)');
}

const io = new Server(server, {
  cors: {
    origin: _corsOrigins.length > 0 ? _corsOrigins : '*',
    credentials: _corsOrigins.length > 0,
  },
  transports: ['websocket'],
  // ⭐ FIX BUG 14: aumenta buffer Socket.IO (default 1MB era troppo basso per
  // disegni pesanti / bulk events). 5MB è un buon compromesso (MiroTalk usa 10MB).
  maxHttpBufferSize: 5e6,
  // ⭐ Ping più frequente per rilevare disconnessioni mobile/background più velocemente
  pingTimeout: 30000,   // ⭐ 20s→30s: su mobile un buco di 20-30s è comune; il socket moriva prima del recupero ICE (35s)
  pingInterval: 15000,  // default 25000 → ridotto a 15s
});

// ─── Stato globale ────────────────────────────────────────────────────────────
const workers = [];
let workerIndex = 0;
const rooms = new Map();

// ─── mediasoup Workers ────────────────────────────────────────────────────────
async function createWorkers() {
  const { numWorkers, workerSettings } = config.mediasoup;
  for (let i = 0; i < numWorkers; i++) {
    const worker = await mediasoup.createWorker(workerSettings);
    // ⭐ FIX BUG 10: worker.on('died') prima chiamava `createWorkers()` che
    // RICREAVA TUTTI i 4 worker (push su array senza pulire), lasciando le stanze
    // attive sul worker morto orfane e raddoppiando i processi a ogni morte.
    // Il pattern corretto (anche di MiroTalk) è: log + uscita pulita, lascia che
    // Docker (restart: unless-stopped) ricrei il container fresco.
    worker.on('died', (err) => {
      console.error(`[Meet mediasoup] Worker ${worker.pid} morto:`, err?.message || err);
      console.error('[Meet mediasoup] Riavvio container in 3 secondi...');
      setTimeout(() => process.exit(1), 3000);
    });
    workers.push(worker);
    console.log(`[Meet mediasoup] Worker ${i + 1}/${numWorkers} (pid: ${worker.pid})`);
  }
}
function getNextWorker() { const w = workers[workerIndex]; workerIndex = (workerIndex + 1) % workers.length; return w; }

// ⭐ FIX BUG 11+12: validators per socket events (anti-injection, anti-DoS-by-payload).
// Tutti gli input dai socket vengono normalizzati e tagliati prima dell'uso.
const _isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const _safeStr = (v, max = 200) => {
  if (typeof v !== 'string') return '';
  // Strip null bytes e taglio
  return v.replace(/\u0000/g, '').slice(0, max);
};
const _safeBool = (v) => v === true || v === 1 || v === 'true';
// Whitelist di tipi validi per mediaState
const _MEDIA_STATE_TYPES = new Set(['audio', 'video']);
const _MEDIA_TYPES = new Set(['audio', 'video', 'screen', 'screen-audio']);
const _roomPolicy = () => brand.getRoomPolicy(db);

// ⭐ FIX BUG 13: rate-limiter per-socket events.
// Senza questo, un client malevolo (o un bug client) può flood-are un evento
// migliaia di volte al secondo, intasando il server e tutti i peer.
// Token bucket semplice: ogni evento consuma 1 token, refill al ratio configurato.
function makeRateLimiter(capacity, refillPerSec) {
  return {
    tokens: capacity,
    last: Date.now(),
    capacity,
    refillPerSec,
    take() {
      const now = Date.now();
      const elapsed = (now - this.last) / 1000;
      this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.refillPerSec);
      this.last = now;
      if (this.tokens < 1) return false;
      this.tokens -= 1;
      return true;
    }
  };
}

// ⭐ FIX race: due joinRoom simultanei sulla stessa stanza (2 persone cliccano
// il link nello stesso istante) passavano entrambi il check rooms.has() e
// creavano DUE router: la seconda Room sovrascriveva la prima nella Map →
// il primo peer restava su un router orfano, isolato da tutti.
// Pattern: promise pendente condivisa — il secondo chiamante attende la stessa.
const _pendingRooms = new Map();
async function getOrCreateRoom(roomId) {
  if (rooms.has(roomId)) return rooms.get(roomId);
  if (_pendingRooms.has(roomId)) return _pendingRooms.get(roomId);
  const promise = _createRoom(roomId).finally(() => _pendingRooms.delete(roomId));
  _pendingRooms.set(roomId, promise);
  return promise;
}
async function _createRoom(roomId) {
  const router = await getNextWorker().createRouter(config.mediasoup.routerOptions);
  const room = new Room(roomId, router);
  room.onBroadcast = (event, data) => io.to(roomId).emit(event, data);
  room.onEmitTo = (socketId, event, data) => io.to(socketId).emit(event, data);
  rooms.set(roomId, room);
  console.log(`[Meet room] Creata: ${roomId}`);

  // ⭐ FIX BUG 9: aggancia gli eventi audioLevelObserver per dominant-speaker detection.
  // L'observer è creato dentro Room.constructor in modo asincrono, quindi aspettiamo
  // che sia pronto. Notifica tutti i client della stanza con eventi 'activeSpeaker'
  // e 'speakerSilence', che sostituiscono il polling consumer.getStats() lato client.
  const _attachAlo = () => {
    if (!room.audioLevelObserver) {
      setTimeout(_attachAlo, 50);
      return;
    }
    room.audioLevelObserver.on('volumes', (volumes) => {
      if (!volumes || !volumes.length) return;
      const { producer, volume } = volumes[0];  // dominant speaker (maxEntries: 1)
      const peer = room.getPeerByProducerId(producer.id);
      if (!peer) return;
      io.to(room.id).emit('activeSpeaker', { peerId: peer.id, volume });
    });
    room.audioLevelObserver.on('silence', () => {
      io.to(room.id).emit('speakerSilence');
    });
  };
  _attachAlo();

  return room;
}

// ─── Socket.io Signaling ──────────────────────────────────────────────────────
io.on('connection', (socket) => {
  let currentRoom = null;
  let currentPeer = null;
  // ⭐ FIX R11: un guest rimesso in lobby (ultimo host uscito) conservava
  // currentPeer nella closure → poteva ancora mandare chat, disegni, reazioni
  // e aprire consumer. Ora ogni handler verifica che il peer sia DAVVERO in stanza.
  const _inRoom = () => !!(currentRoom && currentPeer && currentRoom.peers.get(socket.id) === currentPeer);

  // ⭐ FIX BUG 13: rate limiters per-socket.
  // Ogni socket ha i suoi bucket — limita il flood senza penalizzare gli altri peer.
  // Capacità = burst breve, refillPerSec = sostenibile.
  const _rl = {
    diag: makeRateLimiter(10, 1),    // diagnostica connessione (net/ice/visibility): 10 burst, 1/s
    chat: makeRateLimiter(10, 2),    // chat: max 10 burst, 2 msg/s sostenuti
    wb: makeRateLimiter(80, 40),   // annotazioni: burst 80, 40 msg/s (disegno fluido)
    ptr: makeRateLimiter(40, 30),  // puntatore laser: 30 pos/s
    media: makeRateLimiter(20, 5),    // mediaState/handRaise: 20 burst, 5/s
    reaction: makeRateLimiter(5, 1),     // reactions: 5 burst, 1/s (anti-spam emoji)
  };
  const _checkRl = (bucket, eventName) => {
    if (!_rl[bucket].take()) {
      console.warn(`[ratelimit] socket=${socket.id} event=${eventName} bucket=${bucket} drop`);
      return false;
    }
    return true;
  };

  // ── Diagnostica connessione inviata dal client (vedi connlog.js) ──────────
  const _diagPeer = () => currentPeer?.displayName || socket.id;
  socket.on('clientNetInfo', (d) => {
    if (!_checkRl('diag', 'clientNetInfo') || !currentRoom || !d || typeof d !== 'object') return;
    const c = d.conn || {};
    connlog.log(currentRoom.id, _diagPeer(), 'net', {
      why: String(d.why || '').slice(0, 20), online: d.online,
      type: c.type, eff: c.effectiveType, downMbps: c.downlink, rttMs: c.rtt, saveData: c.saveData,
    });
  });
  socket.on('clientIceState', (d) => {
    if (!_checkRl('diag', 'clientIceState') || !currentRoom || !d || typeof d !== 'object') return;
    connlog.log(currentRoom.id, _diagPeer(), 'client-ice', { transport: String(d.label || '').slice(0, 20), state: String(d.state || '').slice(0, 20) });
  });
  socket.on('clientVisibility', (d) => {
    if (!_checkRl('diag', 'clientVisibility') || !currentRoom || !d || typeof d !== 'object') return;
    connlog.log(currentRoom.id, _diagPeer(), 'visibility', { state: String(d.state || '').slice(0, 10), note: d.state === 'hidden' ? 'app in background / schermo bloccato' : undefined });
  });

  socket.on('joinRoom', async ({ roomId, displayName, token, guestToken, audioMuted, videoOff }, callback) => {
    try {
      if (!roomId || !displayName) throw new Error('roomId e displayName obbligatori');

      // Limiti lunghezza input (anti-flood)
      roomId = String(roomId).slice(0, 64);
      displayName = String(displayName).slice(0, 50).trim();
      if (!displayName) throw new Error('displayName non valido');

      // Sanitizza roomId: solo A-Z 0-9 -
      if (!/^[A-Z0-9\-]+$/i.test(roomId)) throw new Error('roomId non valido');

      // Verifica auth: utente registrato O guest con token valido
      let isGuest = false;
      if (guestToken) {
        const gp = verifyGuestToken(guestToken);
        if (!gp || gp.roomId !== roomId) return callback({ error: 'AUTH_REQUIRED', message: 'Token guest non valido' });
        isGuest = true;
      } else {
        const payload = verifyToken(token);
        if (!payload) return callback({ error: 'AUTH_REQUIRED', message: 'Sessione scaduta' });
      }

      // Cap numero totale stanze attive (anti-DoS)
      const MAX_ACTIVE_ROOMS = parseInt(process.env.MAX_ACTIVE_ROOMS || '100');
      if (!rooms.has(roomId) && rooms.size >= MAX_ACTIVE_ROOMS) {
        throw new Error('Limite stanze attive raggiunto, riprova più tardi');
      }

      const room = await getOrCreateRoom(roomId);

      // ── LOBBY: guest senza host in stanza → in attesa ──────────────────────
      // Strict: il guest non può entrare nemmeno come peer finché non c'è
      // almeno un utente loggato (host) in stanza. Niente transport, niente
      // producer, niente consumer. Il client mostrerà l'overlay di attesa.
      if (isGuest && !room.hasHost()) {
        room.addToLobby(socket.id, displayName);
        currentRoom = room;
        // currentPeer resta null finché non viene effettivamente ammesso
        // ⭐ FIX R11: NIENTE socket.join qui. Prima il guest in lobby era nella
        // room socket.io e riceveva chat, reazioni, disegni e activeSpeaker della
        // riunione senza essere stato ammesso. Il cleanup usa currentRoom, non la room.
        console.log(`[Meet lobby] ${displayName} → lobby ${roomId} (no host)`);
        return callback({
          waitingForHost: true,
          roomId,
          peerId: socket.id,
          isGuest: true,
        });
      }

      // ⭐ FIX ghost-peer: se questo socket era in lobby (guest ammesso dopo
      // 'hostAvailable'), va RIMOSSO dalla lobby prima di diventare peer.
      // Senza questo, alla disconnessione l'handler 'disconnect' vedeva
      // hasInLobby()===true e faceva return anticipato SENZA removePeer →
      // tile fantasma per tutti, producer orfani, stanza mai eliminata.
      room.removeFromLobby(socket.id);

      const peer = room.addPeer(socket.id, displayName, isGuest);
      // ⭐ FIX entrata-muto: registra lo stato iniziale comunicato dal client,
      // così il broadcast peerJoined porta l'icona mute corretta agli altri peer.
      peer.audioMuted = _safeBool(audioMuted);
      peer.videoOff = _safeBool(videoOff);
      currentRoom = room; currentPeer = peer;
      socket.join(roomId);
      socket.to(roomId).emit('peerJoined', peer.toJSON());
      console.log(`[Meet room] ${displayName}${isGuest ? ' (guest)' : ''} → ${roomId}`);
      peer.joinedAt = Date.now();
      connlog.log(roomId, displayName, 'join', { guest: isGuest, ip: connlog.clientIp(socket), ...connlog.summarizeUA(socket.handshake.headers['user-agent']) });

      // ── Se è entrato un HOST e ci sono guest in lobby → li sblocca ─────────
      if (!isGuest && room.lobby.size > 0) {
        const lobbyIds = room.getLobbySocketIds();
        console.log(`[Meet lobby] host ${displayName} entrato, sblocco ${lobbyIds.length} guest in lobby`);
        for (const lobbySocketId of lobbyIds) {
          io.to(lobbySocketId).emit('hostAvailable', { roomId });
        }
      }

      callback({
        roomId, peerId: socket.id, isGuest,
        routerRtpCapabilities: room.router.rtpCapabilities,
        peers: room.getPeersData(socket.id),
        chatHistory: room.chatHistory,
        annotations: room.annotationsSnapshot(),
        annOpen: Object.fromEntries(room.annOpen),
        policy: _roomPolicy(),
        isHostRole: !isGuest,
      });
    } catch (err) { callback({ error: err.message }); }
  });

  socket.on('createWebRtcTransport', async ({ direction, isScreenShare = false }, callback) => {
    try {
      if (!_inRoom()) throw new Error('Non in stanza');
      const { transport, params } = await currentRoom.createWebRtcTransport(socket.id, isScreenShare);
      transport.appData = { direction };
      // Passa iceServers al client (mediasoup non li include automaticamente)
      const opts = isScreenShare
        ? config.mediasoup.screenShareTransportOptions
        : config.mediasoup.webRtcTransportOptions;
      callback({ params: { ...params, iceServers: opts.iceServers || [] } });
    } catch (err) { callback({ error: err.message }); }
  });

  socket.on('connectWebRtcTransport', async ({ transportId, dtlsParameters }, callback) => {
    try {
      const t = currentRoom?.getTransport(socket.id, transportId);
      if (!t) throw new Error('Transport non trovato');
      await t.connect({ dtlsParameters });
      callback({ connected: true });
    } catch (err) { callback({ error: err.message }); }
  });

  // ⭐ FIX BUG 5: restartIce per recovery automatico su disconnect transport.
  // Il client lo chiama quando rileva connectionstatechange === 'disconnected' o 'failed'.
  socket.on('restartIce', async ({ transportId }, callback) => {
    try {
      if (!_inRoom()) throw new Error('Non in stanza');
      const iceParameters = await currentRoom.restartIce(socket.id, transportId);
      callback({ iceParameters });
    } catch (err) {
      console.warn(`[restartIce] socket=${socket.id} transport=${transportId}:`, err.message);
      callback({ error: err.message });
    }
  });

  // ⭐ FIX R11: a fine screen share il client chiude il suo transport dedicato.
  // Prima restava aperto lato server (fino al timeout ICE 35s) → porte RTC
  // e memoria sprecate a ogni condivisione.
  socket.on('closeTransport', ({ transportId } = {}, callback) => {
    try {
      if (!_inRoom()) throw new Error('Non in stanza');
      const t = currentRoom.getTransport(socket.id, _safeStr(transportId, 64));
      if (t && !t.closed) t.close();
      callback?.({ closed: true });
    } catch (err) { callback?.({ error: err.message }); }
  });

  socket.on('produce', async ({ transportId, kind, rtpParameters, appData }, callback) => {
    try {
      if (!_inRoom()) throw new Error('Non in stanza');
      // ⭐ R12: appData ridotto al solo mediaType (whitelist). Prima l'oggetto del
      // client veniva rigirato a tutti i peer così com'era.
      const mt = _isPlainObject(appData) && _MEDIA_TYPES.has(appData.mediaType) ? appData.mediaType : (kind === 'audio' ? 'audio' : 'video');
      if ((mt === 'screen' || mt === 'screen-audio') && currentPeer.isGuest && !_roomPolicy().guestScreenShare) {
        throw new Error('La condivisione schermo è riservata agli organizzatori');
      }
      const producer = await currentRoom.createProducer(socket.id, transportId, rtpParameters, kind, { mediaType: mt });
      if (mt === 'screen') {
        // stato iniziale "disegno aperto a tutti" dalla policy stanza (modificabile live)
        const open = !!_roomPolicy().annotateAll;
        currentRoom.annOpen.set(socket.id, open);
        io.to(currentRoom.id).emit('annOpen', { sid: socket.id, open });
      }
      socket.to(currentRoom.id).emit('newProducer', {
        producerId: producer.id, peerId: socket.id,
        displayName: currentPeer.displayName, kind: producer.kind, appData: producer.appData,
        // ⭐ FIX mute-fantasma: allega lo stato REALE di mic/camera. L'avvio dello
        // screen share genera una raffica di eventi socket che può far droppare
        // 'peerMediaState' dal rate limiter, lasciando il condivisore "mutato"
        // per gli altri. Così ogni newProducer riallinea l'icona.
        audioMuted: currentPeer.audioMuted, videoOff: currentPeer.videoOff,
      });
      callback({ producerId: producer.id });
    } catch (err) { callback({ error: err.message }); }
  });

  socket.on('consume', async ({ producerId, producerPeerId, rtpCapabilities }, callback) => {
    try {
      if (!_inRoom()) throw new Error('Non in stanza');
      const { consumer, params } = await currentRoom.createConsumer(socket.id, producerPeerId, producerId, rtpCapabilities);
      callback({ params });
    } catch (err) { callback({ error: err.message }); }
  });

  socket.on('resumeConsumer', async ({ consumerId }, callback) => {
    try {
      const consumer = currentPeer?.consumers.get(consumerId);
      if (!consumer) throw new Error('Consumer non trovato');
      await consumer.resume();
      callback({ resumed: true });
    } catch (err) { callback({ error: err.message }); }
  });

  // ⭐ ROUND 5: layer adattivo (equivalente "manuale" dell'adaptiveStream di LiveKit).
  // Il client chiede lo spatial/temporal layer in base al ruolo del tile nel layout:
  //   relatore/main → spatial 2 (1080p) | griglia → spatial 1 (540p) | miniatura → spatial 0 (270p)
  // Fire-and-forget lato client (callback opzionale). Su consumer non-simulcast
  // (es. screen share = single layer) skippa senza errore. Input clampato 0..2
  // per evitare che un client mandi indici fuori range.
  socket.on('setConsumerLayers', async ({ consumerId, spatialLayer, temporalLayer } = {}, callback) => {
    try {
      const consumer = currentPeer?.consumers.get(consumerId);
      if (!consumer) { callback?.({ error: 'Consumer non trovato' }); return; }
      // setPreferredLayers ha senso solo su simulcast/svc; su 'simple' è no-op → skip
      if (consumer.kind !== 'video' || consumer.type === 'simple') { callback?.({ skipped: true }); return; }
      if (!Number.isInteger(spatialLayer)) { callback?.({ skipped: true }); return; }
      const sl = Math.max(0, Math.min(2, spatialLayer));
      const tl = Number.isInteger(temporalLayer) ? Math.max(0, Math.min(2, temporalLayer)) : 2;
      await consumer.setPreferredLayers({ spatialLayer: sl, temporalLayer: tl });
      callback?.({ ok: true, spatialLayer: sl, temporalLayer: tl });
    } catch (err) { callback?.({ error: err.message }); }
  });

  socket.on('closeProducer', ({ producerId }, callback) => {
    try {
      // Salva mediaType prima di chiudere (serve ai client per distinguere screen da camera)
      const _mediaType = currentPeer?.producers.get(producerId)?.appData?.mediaType;
      if (!_inRoom()) throw new Error('Non in stanza');
      currentRoom.closeProducer(socket.id, producerId);
      socket.to(currentRoom.id).emit('producerClosed', { producerId, peerId: socket.id, mediaType: _mediaType });
      callback?.({ closed: true });
    } catch (err) { callback?.({ error: err.message }); }
  });

  // ⭐ FIX mute-decoupling: pausa/resume del producer SERVER-side (pattern MiroTalk).
  // Mettendo in pausa il producer sul server, mediasoup pausa automaticamente tutti i
  // consumer e notifica i client via 'producerpause'/'producerresume'. Lo stato mute
  // diventa autoritativo a livello media, non più dipendente dall'evento UI 'mediaState'
  // (che può essere droppato dal rate limiter o da congestione socket).
  socket.on('pauseProducer', async ({ producerId } = {}, callback) => {
    try {
      const producer = currentPeer?.producers.get(producerId);
      if (!producer) throw new Error('Producer non trovato');
      if (!producer.paused) await producer.pause();
      // Verità lato server anche per getPeersData (icona corretta ai late-join)
      if (producer.kind === 'audio' && producer.appData?.mediaType === 'audio') currentPeer.audioMuted = true;
      if (producer.kind === 'video' && producer.appData?.mediaType === 'video') currentPeer.videoOff = true;
      callback?.({ paused: true });
    } catch (err) { callback?.({ error: err.message }); }
  });

  socket.on('resumeProducer', async ({ producerId } = {}, callback) => {
    try {
      const producer = currentPeer?.producers.get(producerId);
      if (!producer) throw new Error('Producer non trovato');
      if (producer.paused) await producer.resume();
      if (producer.kind === 'audio' && producer.appData?.mediaType === 'audio') currentPeer.audioMuted = false;
      if (producer.kind === 'video' && producer.appData?.mediaType === 'video') currentPeer.videoOff = false;
      callback?.({ resumed: true });
    } catch (err) { callback?.({ error: err.message }); }
  });

  socket.on('mediaState', ({ type, status } = {}) => {
    if (!_inRoom()) return;
    // ⭐ FIX BUG 12: whitelist type, coerci status a bool. Senza, un client può
    // mandare type:'__proto__' (prototype pollution) o oggetti annidati.
    if (!_MEDIA_STATE_TYPES.has(type)) return;
    if (!_checkRl('media', 'mediaState')) return;
    const s = _safeBool(status);
    if (type === 'audio') currentPeer.audioMuted = s;
    if (type === 'video') currentPeer.videoOff = s;
    socket.to(currentRoom.id).emit('peerMediaState', { peerId: socket.id, type, status: s });
  });

  socket.on('handRaise', ({ raised } = {}) => {
    if (!_inRoom()) return;
    if (!_checkRl('media', 'handRaise')) return;
    const r = _safeBool(raised);
    currentPeer.handRaised = r;
    io.to(currentRoom.id).emit('peerHandRaise', { peerId: socket.id, displayName: currentPeer.displayName, raised: r });

    // Auto-abbassamento lato server dopo 10s (backup se il client non manda l'evento)
    clearTimeout(currentPeer._handTimer);
    if (r) {
      currentPeer._handTimer = setTimeout(() => {
        if (currentPeer && currentPeer.handRaised) {
          currentPeer.handRaised = false;
          io.to(currentRoom.id).emit('peerHandRaise', { peerId: socket.id, displayName: currentPeer.displayName, raised: false });
        }
      }, 10000);
    }
  });

  socket.on('reaction', ({ emoji } = {}) => {
    if (!_inRoom()) return;
    if (!_checkRl('reaction', 'reaction')) return;
    // ⭐ FIX BUG 11: limita lunghezza emoji (max 8 char: copre anche emoji compositi tipo 👨‍👩‍👧)
    const e = _safeStr(emoji, 8);
    if (!e) return;
    io.to(currentRoom.id).emit('peerReaction', { peerId: socket.id, displayName: currentPeer.displayName, emoji: e });
  });

  socket.on('chatMessage', ({ message, type = 'text' } = {}) => {
    if (!_inRoom()) return;
    if (!_checkRl('chat', 'chatMessage')) return;
    // ⭐ FIX BUG 11+12: valida sia message che type. Senza, un client poteva
    // fingersi 'system' nel rendering della chat o mandare oggetti enormi.
    const clean = _safeStr(message, 2000).trim();
    if (!clean) return;
    const safeType = (type === 'system' || type === 'file') ? 'text' : _safeStr(type, 16);
    const finalType = ['text'].includes(safeType) ? safeType : 'text';
    const msg = currentRoom.addChatMessage(socket.id, currentPeer.displayName, clean, finalType);
    io.to(currentRoom.id).emit('chatMessage', msg);
  });

  // ── Annotazioni sullo schermo condiviso ───────────────────────────────────
  // Superficie: sid = socket id di chi sta condividendo lo schermo.
  // Permessi: chi presenta + organizzatori; gli ospiti solo se chi presenta
  // (o un organizzatore) ha aperto il disegno a tutti.
  const _ANN_TOOLS = new Set(['pen', 'hl', 'arrow', 'rect', 'ellipse']);
  const _annSid = (sid) => (typeof sid === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(sid) ? sid : null);
  const _canDraw = (sid) => {
    if (!_inRoom() || !sid) return false;
    const owner = currentRoom.peers.get(sid);
    if (!owner || !owner.isScreenSharing) return false;
    if (sid === socket.id || !currentPeer.isGuest) return true;
    return currentRoom.annOpen.get(sid) === true;
  };
  const _canManage = (sid) => _inRoom() && sid && (sid === socket.id || !currentPeer.isGuest);

  socket.on('annDraw', (st) => {
    if (!_isPlainObject(st) || !_checkRl('wb', 'annDraw')) return;
    const sid = _annSid(st.sid);
    if (!_canDraw(sid)) return;
    if (!_ANN_TOOLS.has(st.tool)) return;
    if (!Array.isArray(st.pts) || !st.pts.length || st.pts.length > 1200) return;
    const pts = [];
    for (const p of st.pts) {
      if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) return;
      pts.push([Math.max(-0.1, Math.min(1.1, +p[0].toFixed(4))), Math.max(-0.1, Math.min(1.1, +p[1].toFixed(4)))]);
    }
    const clean = {
      sid, id: _safeStr(st.id, 24) || crypto.randomBytes(6).toString('hex'),
      tool: st.tool,
      color: /^#[0-9a-f]{6}$/i.test(st.color) ? st.color : '#f0a44b',
      w: Math.max(1, Math.min(60, Number(st.w) || 4)),
      pts, live: st.live === true, append: st.append === true,
      peerId: socket.id, name: currentPeer.displayName,
    };
    if (!clean.live) currentRoom.addAnnotation(clean);
    socket.to(currentRoom.id).emit('annDraw', clean);
  });

  socket.on('annPointer', (d) => {
    if (!_isPlainObject(d) || !_checkRl('ptr', 'annPointer')) return;
    const sid = _annSid(d.sid);
    if (!_canDraw(sid)) return;
    const hide = d.hide === true;
    if (!hide && (!Number.isFinite(d.x) || !Number.isFinite(d.y))) return;
    socket.volatile.to(currentRoom.id).emit('annPointer', {
      sid, peerId: socket.id, name: currentPeer.displayName, hide,
      x: hide ? 0 : Math.max(0, Math.min(1, d.x)), y: hide ? 0 : Math.max(0, Math.min(1, d.y)),
      color: /^#[0-9a-f]{6}$/i.test(d.color) ? d.color : '#f0a44b',
    });
  });

  socket.on('annUndo', (d) => {
    if (!_isPlainObject(d) || !_checkRl('wb', 'annUndo')) return;
    const sid = _annSid(d.sid);
    if (!_inRoom() || !sid) return;
    if (currentRoom.undoAnnotation(sid, socket.id)) {
      io.to(currentRoom.id).emit('annSync', { sid, strokes: currentRoom.getAnnotations(sid) });
    }
  });

  socket.on('annClear', (d) => {
    if (!_isPlainObject(d) || !_checkRl('wb', 'annClear')) return;
    const sid = _annSid(d.sid);
    if (!_canManage(sid)) return;
    currentRoom.clearAnnotations(sid);
    io.to(currentRoom.id).emit('annSync', { sid, strokes: [], by: currentPeer.displayName });
  });

  socket.on('annSetOpen', (d) => {
    if (!_isPlainObject(d) || !_checkRl('media', 'annSetOpen')) return;
    const sid = _annSid(d.sid);
    if (!sid || !_canManage(sid)) return;
    const open = _safeBool(d.open);
    currentRoom.annOpen.set(sid, open);
    io.to(currentRoom.id).emit('annOpen', { sid, open, by: currentPeer.displayName });
  });

  // ── Disconnect ─────────────────────────────────────────────────────────────
  socket.on('disconnect', async (reason) => {
    if (!currentRoom) return;

    // ⭐ Diagnostica cadute: reason socket.io spiegata + durata + snapshot
    // stats dei transport/producer (packet loss, jitter, relay TURN) PRIMA
    // che removePeer li chiuda. Vedi connlog.js.
    if (currentPeer && !currentRoom.hasInLobby(socket.id)) {
      const durSec = currentPeer.joinedAt ? Math.round((Date.now() - currentPeer.joinedAt) / 1000) : undefined;
      let snap = null;
      try { snap = await Promise.race([currentRoom.peerStatsSnapshot(socket.id), new Promise(r => setTimeout(() => r(null), 800))]); } catch (e) { /* ignore */ }
      connlog.log(currentRoom.id, currentPeer.displayName, 'leave', {
        reason, meaning: connlog.explainReason(reason), durationSec: durSec,
        transports: snap?.transports, producers: snap?.producers,
      });
    }

    // Se era in lobby (mai ammesso come peer)
    if (currentRoom.hasInLobby(socket.id)) {
      currentRoom.removeFromLobby(socket.id);
      console.log(`[Meet lobby] socket ${socket.id} uscito dalla lobby di ${currentRoom.id}`);
      if (currentRoom.isFullyEmpty()) {
        console.log(`[Meet room] ${currentRoom.id} vuota (lobby), eliminata`);
        currentRoom.close(); rooms.delete(currentRoom.id);
      }
      currentRoom = null; currentPeer = null;
      return;
    }

    // Era peer normale
    const wasHost = currentPeer && !currentPeer.isGuest;
    const peer = currentRoom.removePeer(socket.id);
    if (peer) socket.to(currentRoom.id).emit('peerLeft', { peerId: socket.id, displayName: peer.displayName });

    // Se era l'ULTIMO host → caccia tutti i guest in lobby
    if (wasHost && !currentRoom.hasHost() && currentRoom.peers.size > 0) {
      const evicted = currentRoom.evictGuestsToLobby();
      console.log(`[Meet lobby] ultimo host uscito da ${currentRoom.id}, ${evicted.length} guest rimessi in lobby`);
      for (const m of evicted) {
        // Notifica il guest: chiuderà i propri media e mostrerà overlay
        io.to(m.socketId).emit('hostLeft');
        // ⭐ FIX R11: fuori dalla room socket.io → niente più broadcast al guest in lobby
        io.in(m.socketId).socketsLeave(currentRoom.id);
        // E avvisa gli altri peer rimasti che questo è "uscito" (per pulire UI)
        socket.to(currentRoom.id).emit('peerLeft', { peerId: m.socketId, displayName: m.displayName });
      }
    }

    if (currentRoom.isFullyEmpty()) {
      console.log(`[Meet room] ${currentRoom.id} vuota, eliminata`);
      currentRoom.close(); rooms.delete(currentRoom.id);
    }
    currentRoom = null; currentPeer = null;
  });
});

// ─── Avvio ────────────────────────────────────────────────────────────────────
(async () => {
  try {
    _envUsers = await syncUsersFromEnv(db);
  } catch (e) { console.error('[users-env] errore:', e.message); }
  if (db.listUsers().length === 0) {
    console.warn('════════════════════════════════════════════════════════════════');
    console.warn('[auth] NESSUN UTENTE nel database: nessuno può accedere.');
    console.warn('       Imposta USERS=nome:password:Nome:admin in .env e riavvia.');
    console.warn('════════════════════════════════════════════════════════════════');
  }
  await createWorkers();
  server.listen(config.server.port, () => console.log(`[Meet] Porta ${config.server.port}`));

updateCheck.schedule(); // ⭐ check aggiornamenti notturno (02:00) con email a info@
})();