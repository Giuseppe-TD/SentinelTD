/* ==========================================================================
   Sentinel TD — app logic (Alpine.js, nessun build step)
   Stessa API di sempre sotto: /api/sites, /api/install, /api/security, ...
   ========================================================================== */
function b64uToBuf(s) { s = s.replace(/-/g, '+').replace(/_/g, '/'); const pad = '='.repeat((4 - s.length % 4) % 4); const bin = atob(s + pad); const b = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); return b.buffer; }
function bufToB64u(buf) { const b = new Uint8Array(buf); let s = ''; for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }

function sentinel() {
  return {
    // ---------- auth ----------
    token: localStorage.getItem('tdp_token') || '',
    imageToken: localStorage.getItem('tdp_image_token') || '',
    imgNonce: 0,
    dashHover: null,
    meta: { version: '', name: 'Sentinel TD', vendor: 'Tastiere Digitali', vendor_url: 'https://www.tastieredigitali.it', author: 'Giuseppe Sciarra' },
    pendingToken: '', step: 'pwd', user: '', pwd: '', code: '', methods: [], err: '', needs2faSetup: false,

    // ---------- data ----------
    sites: [], loading: false, busy: {}, toast: '', toastTimer: null,
    detail: null, detailTab: 'overview', history: [], histSummary: null, siteHistory: [],
    sec: { summary: null, items: [], loading: false, sev: '' },
    conn: { list: [], regKey: '', hubUrl: '', hubSaved: '', msg: '', err: '', busy: false },
    brand: { logo_url: '/static/logo.png', favicon_url: '/static/favicon.png', custom_logo: false, custom_favicon: false, busy: false },
    exp: { domains: [], components: [], loadingDomains: false, loadingComponents: false },
    expiryForm: { id: null, platform: 'both', name: '', provider: '', notes: '', date: '', recur: 12, recurCustom: 0 }, expiryEdit: false, expiryErr: '',
    prefsLoaded: false,
    prefs: { domain_alert_days: [30,14,7], component_alert_days: [30,14,7], domain_alert_text: '30, 14, 7', component_alert_text: '30, 14, 7', domain_scan_days: 7, domain_parallel_lookups: 4, expiry_warning_days: 30, expiry_critical_days: 7, screenshot_every_hours: 12, history_retention_days: 400, busy: false, msg: '', err: '' },

    // ---------- ui ----------
    route: { page: 'dashboard', folder: null, siteId: null, tab: 'overview' },
    sideOpen: false, q: '', flt: 'all', sortKey: 'name', sortDir: 1,
    selMode: false, selIds: [],
    drawer: null,      // 'site' | 'install' | 'tags' | null
    form: {}, formErr: '', confirm: null,
    inst: { mode: 'install', cms: 'wp', kind: 'plugin', activate: true, file: null, q: '', results: [], searched: false, rmItems: [], sel: [], busy: false, out: [], openKeys: [] },
    tagsForm: { add: '', remove: '' },
    account: { username: '', oldPw: '', newPw: '', msg: '', err: '' },
    notif: { events: [], cur: null, edit: null, preview: null, msg: '', err: '', busy: false, tab: 'email', previewTab: 'email', source: false },
    stats: { period: '', scope: '__all__', months: 12, data: null, trend: null, periods: [], scopes: [], mode: 'month', cmpA: '', cmpB: '', cmp: null, busy: false, hover: null, days: 30 },
    drep: { from: '', to: '', range: 'q3', mode: 'all', folder: '', ids: [], filter: '', history: true, failed: true, format: 'pdf', busy: false, oldest: '' },
    rep: { cfg: null, template: '', defaultTemplate: '', periods: [], period: '', scopes: [], scope: '__all__', trend: null, trendMonths: 12, html: '', busy: false, msg: '', err: '', advanced: false, dirty: false },
    sec2fa: { totp: false, passkeys: [], setup: null, code: '', msg: '', err: '' },

    // ---------- init ----------
    async init() {
      await this.loadBrand();
      this._readHash();
      window.addEventListener('hashchange', () => { this._readHash(); this._onRoute(); });
      window.addEventListener('languagechange', async () => {
        // Static labels are handled by i18n.js; backend-owned notification defaults
        // and previews must be requested again in the newly selected language.
        if (this.token && this.route.page === 'notifications') await this.loadNotif(true);
      });
      if (this.token) {
        await this.refreshImageToken();
        this.loadMeta();
        await this.load();
      }
      setInterval(() => { if (this.token) this.refreshImageToken(); }, 7 * 60 * 1000);
      // ← → sfogliano i mesi nelle statistiche (non mentre si scrive in un campo)
      window.addEventListener('keydown', (e) => {
        if (this.route.page !== 'stats' || this.stats.mode !== 'month') return;
        const t = e.target, tag = (t && t.tagName) || '';
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) || (t && t.isContentEditable)) return;
        if (e.key === 'ArrowLeft') { e.preventDefault(); this.shiftStatMonth(-1); }
        if (e.key === 'ArrowRight') { e.preventDefault(); this.shiftStatMonth(1); }
      });
      setInterval(() => { if (this.token && !document.hidden) this.load(true); }, 60000);
    },
    h(extra = {}) { return { 'Authorization': 'Bearer ' + this.token, 'Content-Type': 'application/json', 'X-UI-Language': (window.I18n && I18n.locale) || 'it', ...extra }; },
    hp() { return { 'Authorization': 'Bearer ' + this.pendingToken, 'Content-Type': 'application/json' }; },
    // Il token delle immagini dura 10 minuti (scope ristretto, per i link diretti che
    // non possono mandare l'header Authorization). Va rinnovato, altrimenti dopo dieci
    // minuti anteprime, download connettori e PDF report rispondono 401.
    async loadMeta() {
      try { const r = await this.api('/api/version'); if (r.ok) this.meta = { ...this.meta, ...(await r.json()) }; } catch (e) { }
    },
    async refreshImageToken() {
      if (!this.token) return '';
      try {
        const r = await fetch('/api/image-token', { headers: { 'Authorization': 'Bearer ' + this.token } });
        if (!r.ok) return this.imageToken;
        const d = await r.json();
        if (d.image_token) {
          this.imageToken = d.image_token;
          localStorage.setItem('tdp_image_token', this.imageToken);
        }
      } catch (e) { /* rete assente: tengo quello vecchio */ }
      return this.imageToken;
    },
    // apre un link che usa il token immagine, rinnovandolo prima (finestra aperta
    // subito per non farla bloccare dal popup blocker)
    async openTokenLink(urlFn) {
      const w = window.open('', '_blank');
      await this.refreshImageToken();
      const url = urlFn();
      if (w) w.location.href = url; else window.location.href = url;
    },
    async onShotError(site, el) {
      if (el.dataset.retried === '1') return;
      el.dataset.retried = '1';
      await this.refreshImageToken();
      this.imgNonce++;
      el.src = this.shotUrl(site);
    },

    _setTokens(d) { this.token = d.token; this.imageToken = d.image_token || ''; localStorage.setItem('tdp_token', this.token); localStorage.setItem('tdp_image_token', this.imageToken); },
    async api(path, opts = {}) {
      const r = await fetch(path, { ...opts, headers: { ...this.h(), ...(opts.headers || {}) } });
      if (r.status === 401) { this.logout(); throw new Error('Sessione scaduta'); }
      return r;
    },
    say(t, ms = 2800) { this.toast = t; clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => this.toast = '', ms); },

    // ---------- login ----------
    async login() {
      this.err = '';
      const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: this.user, password: this.pwd }) });
      if (r.status === 429) { this.err = 'Troppi tentativi. Riprova tra qualche minuto.'; return; }
      if (!r.ok) { this.err = 'Credenziali errate'; return; }
      const d = await r.json();
      if (d.token) { this._setTokens(d); this.needs2faSetup = !!d.needs_2fa_setup; this.pwd = ''; await this.load(); return; }
      this.pendingToken = d.token_pending; this.methods = d.methods || []; this.step = '2fa'; this.pwd = '';
    },
    async verifyTotp() {
      this.err = '';
      const r = await fetch('/api/2fa/totp/verify', { method: 'POST', headers: this.hp(), body: JSON.stringify({ code: this.code }) });
      if (r.status === 429) { this.err = 'Troppi tentativi. Riprova tra qualche minuto.'; return; }
      if (!r.ok) { this.err = 'Codice non valido'; return; }
      this._setTokens(await r.json()); this.step = 'pwd'; this.code = ''; await this.load();
    },
    async loginPasskey() {
      this.err = '';
      try {
        const r = await fetch('/api/webauthn/login/begin', { method: 'POST', headers: this.hp() });
        if (!r.ok) { this.err = 'Errore avvio passkey'; return; }
        const opts = await r.json();
        opts.challenge = b64uToBuf(opts.challenge);
        if (opts.allowCredentials) opts.allowCredentials = opts.allowCredentials.map(c => ({ ...c, id: b64uToBuf(c.id) }));
        const cred = await navigator.credentials.get({ publicKey: opts });
        const payload = { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type, response: {
          authenticatorData: bufToB64u(cred.response.authenticatorData), clientDataJSON: bufToB64u(cred.response.clientDataJSON),
          signature: bufToB64u(cred.response.signature), userHandle: cred.response.userHandle ? bufToB64u(cred.response.userHandle) : null } };
        const r2 = await fetch('/api/webauthn/login/complete', { method: 'POST', headers: this.hp(), body: JSON.stringify({ credential: payload }) });
        if (!r2.ok) { this.err = r2.status === 429 ? 'Troppi tentativi.' : 'Passkey non valida'; return; }
        this._setTokens(await r2.json()); this.step = 'pwd'; await this.load();
      } catch (e) { this.err = 'Passkey annullata o non disponibile'; }
    },
    logout() { this.token = ''; this.imageToken = ''; this.pendingToken = ''; this.step = 'pwd'; localStorage.removeItem('tdp_token'); localStorage.removeItem('tdp_image_token'); },

    // ---------- router ----------
    _readHash() {
      const h = (location.hash || '#/').slice(1);
      const p = h.split('/').filter(Boolean);
      const r = { page: 'dashboard', folder: null, siteId: null, tab: 'overview' };
      if (p[0] === 'sites') r.page = 'sites';
      else if (p[0] === 'folder') { r.page = 'sites'; r.folder = decodeURIComponent(p[1] || ''); }
      else if (p[0] === 'site') { r.page = 'site'; r.siteId = parseInt(p[1]); r.tab = ['overview','ext','history'].includes(p[2]) ? p[2] : 'overview'; }
      else if (p[0] === 'expiries') r.page = 'domain-expiries'; // compatibilità bookmark vecchi
      else if (['security', 'history', 'settings', 'notifications', 'account', 'domain-expiries', 'component-expiries', 'reports', 'stats'].includes(p[0])) r.page = p[0];
      this.route = r; this.sideOpen = false;
    },
    go(path) { location.hash = '#/' + path; },
    async _onRoute() {
      if (!this.token) return;
      this.selMode = false; this.selIds = [];
      if (this.route.page === 'site' && this.route.siteId) await this.loadDetail(this.route.siteId);
      if (this.route.page === 'security') await this.loadSecurity();
      if (this.route.page === 'history') await this.loadHistory();
      if (this.route.page === 'dashboard') { await this.loadHistory(); }
      if (this.route.page === 'stats') { await this.loadStats(); }
      if (this.route.page === 'settings') { await this.loadConn(); await this.loadPrefs(); }
      if (this.route.page === 'account') { await this.load2fa(); }
      if (this.route.page === 'notifications') { await this.loadNotif(); }
      if (this.route.page === 'reports') { await this.loadReports(); }
      if (this.route.page === 'domain-expiries') { await this.loadPrefs(); await this.loadDomainExpiries(); }
      if (this.route.page === 'component-expiries') { await this.loadPrefs(); await this.loadComponentExpiries(); }
      window.scrollTo(0, 0);
    },

    // ---------- data ----------
    async load(silent = false) {
      if (!silent) this.loading = true;
      try {
        const r = await this.api('/api/sites');
        if (r.ok) this.sites = await r.json();
        if (!silent) await this._onRoute();
        else if (this.route.page === 'dashboard') this.loadHistory();
      } catch (e) { /* logout gestito in api() */ }
      this.loading = false;
    },
    async loadDetail(id) {
      const r = await this.api(`/api/sites/${id}`);
      if (r.ok) this.detail = await r.json();
      const h = await this.api(`/api/history?site_id=${id}&days=7`);
      if (h.ok) this.siteHistory = await h.json();
    },
    async loadHistory() {
      const [a, b] = await Promise.all([this.api('/api/history?days=7&limit=200'), this.api('/api/history/summary?days=7')]);
      if (a.ok) this.history = await a.json();
      if (b.ok) this.histSummary = await b.json();
    },
    async loadSecurity() {
      this.sec.loading = true;
      const [a, b] = await Promise.all([this.api('/api/security/summary'), this.api('/api/security/matches')]);
      if (a.ok) this.sec.summary = await a.json();
      if (b.ok) this.sec.items = (await b.json()).items || [];
      this.sec.loading = false;
    },
    async scanNow() { await this.api('/api/security/scan-now', { method: 'POST' }); this.say('Scansione avviata: risultati tra qualche minuto'); },

    // ---------- helpers ----------
    siteTags(s) { return (s.tags || '').split(',').map(t => t.trim()).filter(Boolean); },
    hasUpd(s) { return s.core_update || (s.updates_count || 0) > 0; },
    isOff(s) { return s.status && s.status !== 'ok'; },
    coreLabel(s) { return s.core_current || '—'; },
    fmtDate(d) { if (!d) return '—'; const x = new Date(d); return x.toLocaleString(I18n.locale, { dateStyle: 'short', timeStyle: 'short' }); },
    fmtDay(d) { if (!d) return '—'; const x = new Date(d); return Number.isNaN(x.getTime()) ? '—' : x.toLocaleDateString(I18n.locale); },
    ago(d) {
      if (!d) return 'mai'; const s = Math.floor((Date.now() - new Date(d)) / 1000);
      if (s < 60) return 'adesso'; if (s < 3600) return Math.floor(s / 60) + ' min fa'; if (s < 86400) return Math.floor(s / 3600) + ' h fa'; return Math.floor(s / 86400) + ' g fa';
    },
    initials(n) { return (n || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase(); },
    daysUntil(d) { if (!d) return null; const a = new Date(d); if (Number.isNaN(a.getTime())) return null; const today = new Date(); const u1 = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()); const u2 = Date.UTC(a.getUTCFullYear(), a.getUTCMonth(), a.getUTCDate()); return Math.round((u2-u1)/86400000); },
    expiryText(d) { const n = this.daysUntil(d); if (n === null) return '—'; if (n < 0) return `scaduto da ${Math.abs(n)} g`; if (n === 0) return 'scade oggi'; return `scade tra ${n} giorni`; },
    expiryClass(d) { return this.expiryDaysClass(this.daysUntil(d)); },
    expiryDaysClass(n) { if (n === null || n === undefined) return ''; if (n < 0 || n <= Number(this.prefs.expiry_critical_days||7)) return 'err'; if (n <= Number(this.prefs.expiry_warning_days||30)) return 'warn'; return 'ok'; },
    expiryDaysText(n) { if (n === null || n === undefined) return '—'; if (n < 0) return `scaduto da ${Math.abs(n)} giorni`; if (n === 0) return 'scade oggi'; return `tra ${n} giorni`; },
    platformLabel(p) { return p === 'wp' ? 'WordPress' : (p === 'joomla' ? 'Joomla' : 'WordPress + Joomla'); },
    dateOnly(d) { if (!d) return ''; const x = new Date(d); if (Number.isNaN(x.getTime())) return ''; return x.toISOString().slice(0, 10); },
    phpState(v) {
      const m = String(v || '').match(/(\d+)\.(\d+)/); if (!m) return { cls: '', label: '' };
      const ver = `${m[1]}.${m[2]}`;
      const dates = {
        '8.1': ['2023-11-25','2025-12-31'], '8.2': ['2024-12-31','2026-12-31'],
        '8.3': ['2025-12-31','2027-12-31'], '8.4': ['2026-12-31','2028-12-31'],
        '8.5': ['2027-12-31','2029-12-31']
      };
      if (+m[1] < 8 || (+m[1] === 8 && +m[2] <= 0)) return { cls: 'err', label: 'EOL' };
      const d = dates[ver]; if (!d) return { cls: '', label: '' };
      const now = new Date(); const active = new Date(d[0]+'T23:59:59Z'); const security = new Date(d[1]+'T23:59:59Z');
      if (now > security) return { cls: 'err', label: 'EOL' };
      if (now > active) return { cls: 'warn', label: 'solo sicurezza' };
      return { cls: 'ok', label: '' };
    },

    // ---------- folders (tag piatti; "Padre/Figlio" = albero a 2 livelli) ----------
    get folderTree() {
      const map = {};
      for (const s of this.sites) for (const t of this.siteTags(s)) {
        const [top, sub] = t.split('/').map(x => x.trim());
        map[top] = map[top] || { name: top, tag: t.includes('/') ? null : t, sites: [], subs: {} };
        if (sub) { map[top].subs[sub] = map[top].subs[sub] || { name: sub, tag: t, sites: [] }; map[top].subs[sub].sites.push(s); }
        else map[top].sites.push(s);
      }
      return Object.values(map).sort((a, b) => a.name.localeCompare(b.name)).map(f => {
        const subs = Object.values(f.subs).sort((a, b) => a.name.localeCompare(b.name));
        const all = [...f.sites, ...subs.flatMap(x => x.sites)];
        return { ...f, subs, all, stats: this._stats(all), subStats: Object.fromEntries(subs.map(x => [x.name, this._stats(x.sites)])) };
      });
    },
    get noFolder() { return this.sites.filter(s => this.siteTags(s).length === 0); },
    _stats(arr) { return { count: arr.length, upd: arr.filter(s => this.hasUpd(s)).length, off: arr.filter(s => this.isOff(s)).length }; },
    get totStats() { return { ...this._stats(this.sites), auto: this.sites.filter(s => s.auto_update).length, wp: this.sites.filter(s => s.cms === 'wp').length, joomla: this.sites.filter(s => s.cms === 'joomla').length }; },
    folderSites(tag) {
      if (tag === '__none') return this.noFolder;
      const tf = tag.toLowerCase();
      return this.sites.filter(s => this.siteTags(s).some(t => t.toLowerCase() === tf || t.toLowerCase().startsWith(tf + '/')));
    },
    get pageSites() { return this.route.folder ? this.folderSites(this.route.folder) : this.sites; },
    get pageTitle() { return this.route.folder ? (this.route.folder === '__none' ? 'Senza cartella' : this.route.folder) : 'Tutti i siti'; },
    get pageStats() { return this._stats(this.pageSites); },

    // ---------- list: filter + sort ----------
    get filtered() {
      let arr = this.pageSites;
      const q = this.q.trim().toLowerCase();
      if (q) arr = arr.filter(s => (s.name || '').toLowerCase().includes(q) || (s.url || '').toLowerCase().includes(q) || (s.tags || '').toLowerCase().includes(q));
      if (this.flt === 'upd') arr = arr.filter(s => this.hasUpd(s));
      else if (this.flt === 'off') arr = arr.filter(s => this.isOff(s));
      else if (this.flt === 'wp' || this.flt === 'joomla') arr = arr.filter(s => s.cms === this.flt);
      const k = this.sortKey, d = this.sortDir;
      return [...arr].sort((a, b) => {
        let va, vb;
        if (k === 'status') { va = this.isOff(a) ? 2 : (this.hasUpd(a) ? 1 : 0); vb = this.isOff(b) ? 2 : (this.hasUpd(b) ? 1 : 0); }
        else if (k === 'updates') { va = a.updates_count || 0; vb = b.updates_count || 0; }
        else if (k === 'checked') { va = a.last_checked || ''; vb = b.last_checked || ''; }
        else { va = (a[k] || '').toString().toLowerCase(); vb = (b[k] || '').toString().toLowerCase(); }
        return (va > vb ? 1 : va < vb ? -1 : 0) * d;
      });
    },
    sortBy(k) { if (this.sortKey === k) this.sortDir *= -1; else { this.sortKey = k; this.sortDir = 1; } },

    // ---------- selection ----------
    toggleSel(id) { this.selIds = this.selIds.includes(id) ? this.selIds.filter(x => x !== id) : [...this.selIds, id]; },
    selAll() { const f = this.filtered.map(s => s.id); this.selIds = f.every(id => this.selIds.includes(id)) ? [] : f; },
    exitSel() { this.selMode = false; this.selIds = []; },

    // ---------- site actions ----------
    async check(s) {
      this.busy[s.id] = true;
      try { const r = await this.api(`/api/sites/${s.id}/refresh`, { method: 'POST' }); if (r.ok) { const d = await r.json(); Object.assign(s, d); if (this.detail && this.detail.id === s.id) this.detail = d; this.say('Check completato: ' + s.name); } }
      finally { this.busy[s.id] = false; }
    },
    async checkAll() { this.say('Check di tutti i siti in corso…'); for (const s of this.sites) { try { const r = await this.api(`/api/sites/${s.id}/refresh`, { method: 'POST' }); if (r.ok) Object.assign(s, await r.json()); } catch (e) { } } this.say('Check completato'); },
    async updateNow(s) {
      if (!confirm(`Aggiornare tutto su "${s.name}" adesso?`)) return;
      const r = await this.api('/api/sites/bulk/update-selected', { method: 'POST', body: JSON.stringify({ ids: [s.id] }) });
      this.say(r.ok ? 'Aggiornamento accodato: ' + s.name : 'Errore nell\'accodare');
    },
    async updateSelected() {
      if (!this.selIds.length) return;
      if (!confirm(`Aggiornare ${this.selIds.length} siti adesso?`)) return;
      const r = await this.api('/api/sites/bulk/update-selected', { method: 'POST', body: JSON.stringify({ ids: this.selIds }) });
      this.say(r.ok ? `Aggiornamento accodato su ${this.selIds.length} siti` : 'Errore'); this.exitSel();
    },
    async updateAll() {
      if (!confirm('Aggiornare TUTTI i siti con update pendenti adesso?')) return;
      const r = await this.api('/api/sites/bulk/update-now', { method: 'POST' });
      this.say(r.ok ? 'Aggiornamento globale accodato' : 'Errore');
    },
    async setAutoSelected(on) {
      for (const id of this.selIds) { const s = this.sites.find(x => x.id === id); if (s && s.auto_update !== on) { await this.api(`/api/sites/${id}`, { method: 'PATCH', body: JSON.stringify({ auto_update: on }) }); s.auto_update = on; } }
      this.say(`Auto-update ${on ? 'attivato' : 'disattivato'} su ${this.selIds.length} siti`); this.exitSel();
    },
    async goAdmin(s) {
      const w = window.open('', '_blank'); const al = s.cms === 'wp';
      try {
        const r = await this.api(`/api/sites/${s.id}/admin?autologin=${al}`);
        if (r.ok) { const u = (await r.json()).url; if (u && w) { w.location.href = u; return; } }
        if (w) w.close(); this.say('URL admin non disponibile');
      } catch (e) { if (w) w.close(); }
    },
    async delSite(s) {
      if (!confirm(`Eliminare "${s.name}" da Sentinel? (il sito NON viene toccato)`)) return;
      await this.api(`/api/sites/${s.id}`, { method: 'DELETE' }); this.say('Sito rimosso'); this.go('sites'); await this.load(true);
    },
    async toggleSilence(s) {
      const on = !s.notifications_silenced;
      const r = await this.api(`/api/sites/${s.id}`, { method: 'PATCH', body: JSON.stringify({ notifications_silenced: on }) });
      if (!r.ok) { this.say('Errore nel modificare le notifiche'); return; }
      const d = await r.json(); Object.assign(s, d); if (this.detail && this.detail.id === s.id) Object.assign(this.detail, d);
      this.say(on ? `Notifiche silenziate per ${s.name}` : `Notifiche riattivate per ${s.name}`);
    },
    exportCsv() {
      const esc = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
      const rows = [['Nome','URL','CMS','Core','PHP','Cartelle','Stato','Update','Ultimo check','Scadenza dominio','Notifiche']];
      for (const x of this.filtered) rows.push([x.name,x.url,x.cms,x.core_current||'',x.php_version||'',this.siteTags(x).join(' | '),this.isOff(x)?'offline':'ok',x.updates_count||0,x.last_checked||'',x.domain_expires_at||'',x.notifications_silenced?'silenziate':'attive']);
      const csv = '\ufeff' + rows.map(r => r.map(esc).join(';')).join('\r\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `sentinel-siti-${new Date().toISOString().slice(0,10)}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    },

    // ---------- site form (drawer) ----------
    openNew() { this.form = { name: '', url: '', cms: 'wp', admin_url: '', token: '', group: '', tags: this.route.folder && this.route.folder !== '__none' ? this.route.folder : '', poll_interval_minutes: 180, auto_update: true, notifications_silenced: false, enabled: true }; this.formErr = ''; this.drawer = 'site'; },
    openEdit(s) { this.form = { id: s.id, name: s.name, url: s.url, cms: s.cms, admin_url: s.admin_url || '', token: s.token || '', group: s.group || '', tags: s.tags || '', poll_interval_minutes: s.poll_interval_minutes, auto_update: s.auto_update, notifications_silenced: !!s.notifications_silenced, enabled: s.enabled }; this.formErr = ''; this.drawer = 'site'; },
    async saveSite() {
      this.formErr = '';
      const f = this.form; const isNew = !f.id;
      const body = { ...f }; delete body.id;
      const r = await this.api(isNew ? '/api/sites' : `/api/sites/${f.id}`, { method: isNew ? 'POST' : 'PATCH', body: JSON.stringify(body) });
      if (!r.ok) { const d = await r.json().catch(() => ({})); this.formErr = d.detail || 'Errore nel salvataggio'; return; }
      this.drawer = null; this.say(isNew ? 'Sito aggiunto' : 'Sito salvato');
      await this.load(true); if (this.detail && this.detail.id === f.id) await this.loadDetail(f.id);
    },

    // ---------- tags (bulk) ----------
    openTags() { this.tagsForm = { add: '', remove: '' }; this.drawer = 'tags'; },
    async saveTags() {
      const add = this.tagsForm.add.split(',').map(x => x.trim()).filter(Boolean), remove = this.tagsForm.remove.split(',').map(x => x.trim()).filter(Boolean);
      const r = await this.api('/api/sites/bulk/tags', { method: 'POST', body: JSON.stringify({ site_ids: this.selIds, add, remove }) });
      if (r.ok) { this.say('Cartelle aggiornate'); this.drawer = null; this.exitSel(); await this.load(true); } else this.say('Errore');
    },
    get allTags() { const set = new Set(); this.sites.forEach(s => this.siteTags(s).forEach(t => set.add(t))); return [...set].sort(); },

    // ---------- install / remove ----------
    openInstall(mode = 'install') { this.inst = { ...this.inst, mode, file: null, q: '', results: [], searched: false, rmItems: [], sel: [], out: [], openKeys: [], siteFilter: '', drag: false }; this.drawer = 'install'; },
    instSites() {
      const base = this.sites.filter(s => s.cms === this.inst.cms && s.enabled);
      if (this.inst.mode !== 'remove') return base;
      // in RIMOZIONE mostra solo i siti che hanno davvero almeno una delle estensioni
      // scelte: selezionarne altri produceva solo errori "non trovata"
      const have = new Set(this.inst.rmItems.flatMap(i => i.site_ids || []));
      return base.filter(s => have.has(s.id));
    },
    // tiene la selezione dei siti coerente con le estensioni scelte
    _pruneInstSel() {
      const allowed = new Set(this.instSites().map(s => s.id));
      this.inst.sel = this.inst.sel.filter(id => allowed.has(id));
    },
    instToggleAll() { const ids = this.instSites().map(s => s.id); this.inst.sel = ids.every(i => this.inst.sel.includes(i)) ? [] : ids; },
    async instSearch(live = false) {
      const q = this.inst.q.trim();
      if (!q) { this.inst.results = []; this.inst.searched = false; return; }
      if (live && q.length < 2) return;
      const r = await this.api(`/api/install/search?cms=${this.inst.cms}&q=${encodeURIComponent(this.inst.q)}`);
      if (r.ok) { this.inst.results = (await r.json()).results || []; this.inst.searched = true; }
    },
    instSelected(r) { return this.inst.rmItems.some(i => i.type === r.type && i.slug === r.slug); },
    instToggle(r) {
      if (r.protected) return;
      if (this.instSelected(r)) {
        this.inst.rmItems = this.inst.rmItems.filter(i => !(i.type === r.type && i.slug === r.slug));
      } else {
        this.inst.rmItems.push({ type: r.type, slug: r.slug, name: r.name, site_ids: r.site_ids, site_details: r.site_details || [] });
        // selezione ESPLICITA: scegliendo l'estensione si spuntano i siti che la hanno,
        // poi si deseleziona chi non si vuole toccare (niente piu' "vuoto = tutti")
        this.inst.sel = [...new Set([...this.inst.sel, ...(r.site_ids || [])])];
      }
      this._pruneInstSel();
    },

    // ---- operazioni in blocco: comandi del flusso guidato ----
    instSetMode(m) {
      if (this.inst.mode === m) return;
      Object.assign(this.inst, { mode: m, sel: [], out: [], rmItems: [], results: [], searched: false, q: '', siteFilter: '', file: null, openKeys: [] });
    },
    instSetCms(c) {
      if (this.inst.cms === c) return;
      Object.assign(this.inst, { cms: c, sel: [], out: [], rmItems: [], results: [], searched: false, siteFilter: '', openKeys: [] });
      if (this.inst.mode === 'remove' && this.inst.q.trim()) this.instSearch();
    },
    instCmsCount(c) { return this.sites.filter(s => s.cms === c && s.enabled).length; },
    instVisibleSites() {
      const f = (this.inst.siteFilter || '').trim().toLowerCase();
      const list = this.instSites();
      return f ? list.filter(s => (s.name || '').toLowerCase().includes(f) || (s.url || '').toLowerCase().includes(f) || (s.tags || '').toLowerCase().includes(f)) : list;
    },
    instToggleSite(id) { this.inst.sel = this.inst.sel.includes(id) ? this.inst.sel.filter(x => x !== id) : [...this.inst.sel, id]; },
    instSelAll() { this.inst.sel = [...new Set([...this.inst.sel, ...this.instVisibleSites().map(s => s.id)])]; },
    instSelNone() {
      const vis = new Set(this.instVisibleSites().map(s => s.id));
      this.inst.sel = (this.inst.siteFilter || '').trim() ? this.inst.sel.filter(id => !vis.has(id)) : [];
    },
    instFolders() {
      const map = {};
      for (const s of this.instSites()) for (const t of this.siteTags(s)) map[t] = (map[t] || 0) + 1;
      return Object.entries(map).sort((a, b) => a[0].localeCompare(b[0])).map(([tag, count]) => ({ tag, count }));
    },
    _instFolderIds(tag) { return this.instSites().filter(s => this.siteTags(s).includes(tag)).map(s => s.id); },
    instFolderOn(tag) { const ids = this._instFolderIds(tag); return ids.length > 0 && ids.every(id => this.inst.sel.includes(id)); },
    instToggleFolder(tag) {
      const ids = this._instFolderIds(tag);
      this.inst.sel = this.instFolderOn(tag) ? this.inst.sel.filter(id => !ids.includes(id)) : [...new Set([...this.inst.sel, ...ids])];
    },
    instSiteVersion(s) {
      for (const it of this.inst.rmItems) {
        const d = (it.site_details || []).find(x => x.id === s.id);
        if (d) return d.version ? 'v' + d.version : '';
      }
      return '';
    },
    instDrop(e) {
      this.inst.drag = false;
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!f) return;
      if (!/\.zip$/i.test(f.name)) { this.say('Serve un file .zip'); return; }
      this.inst.file = f;
    },
    get instCanRun() {
      if (this.inst.busy || !this.inst.sel.length) return false;
      return this.inst.mode === 'install' ? !!this.inst.file : this.inst.rmItems.length > 0;
    },
    pl(n, one, many) { return n === 1 ? one : many; },   // singolare/plurale italiano
    get instSummary() {
      const n = this.inst.sel.length;
      if (this.inst.mode === 'install') {
        if (!this.inst.file) return 'Scegli lo zip da installare';
        if (!n) return 'Scegli almeno un sito';
        return `Installerai ${this.inst.file.name} su ${n} ${this.pl(n, 'sito', 'siti')}`;
      }
      const k = this.inst.rmItems.length;
      if (!k) return 'Scegli cosa rimuovere';
      if (!n) return 'Scegli almeno un sito';
      return `Rimuoverai ${k} ${this.pl(k, 'estensione', 'estensioni')} da ${n} ${this.pl(n, 'sito', 'siti')}`;
    },
    instOutCount(kind) {
      return this.inst.out.filter(o => kind === 'skip' ? o.skipped : (kind === 'ok' ? (o.ok && !o.skipped) : (!o.ok && !o.skipped))).length;
    },
    instIsOpen(r) { return this.inst.openKeys.includes(r.type + '|' + r.slug); },
    instToggleOpen(r) { const k = r.type + '|' + r.slug; this.inst.openKeys = this.inst.openKeys.includes(k) ? this.inst.openKeys.filter(x => x !== k) : [...this.inst.openKeys, k]; },
    async runInstall() {
      if (!this.inst.file || !this.inst.sel.length) { this.say('Serve un file e almeno un sito'); return; }
      this.inst.busy = true; this.inst.out = [];
      try {
        const fd = new FormData(); fd.append('package', this.inst.file); fd.append('cms', this.inst.cms); fd.append('kind', this.inst.kind); fd.append('activate', this.inst.activate ? 'true' : 'false'); fd.append('site_ids', this.inst.sel.join(','));
        const r = await fetch('/api/install', { method: 'POST', headers: { 'Authorization': 'Bearer ' + this.token }, body: fd });
        const d = await r.json().catch(() => ({})); this.inst.out = d.results || d.items || [{ ok: r.ok, message: d.detail || (r.ok ? 'ok' : 'errore') }];
        await this.load(true);
      } finally { this.inst.busy = false; }
    },
    async runRemove() {
      if (!this.inst.rmItems.length) { this.say('Seleziona cosa rimuovere'); return; }
      // solo siti che hanno l'estensione: la selezione manuale viene intersecata con
      // chi ce l'ha davvero, cosi' un clic sbagliato non genera errori
      const have = [...new Set(this.inst.rmItems.flatMap(i => i.site_ids || []))];
      const ids = this.inst.sel.length ? this.inst.sel.filter(id => have.includes(id)) : have;
      if (!ids.length) { this.say('Nessuno dei siti selezionati ha le estensioni scelte'); return; }
      if (!confirm(`Rimuovere ${this.inst.rmItems.length} estensioni da ${ids.length} siti?`)) return;
      this.inst.busy = true; this.inst.out = [];
      try {
        const r = await this.api('/api/install/remove', { method: 'POST', body: JSON.stringify({ cms: this.inst.cms, items: this.inst.rmItems.map(i => ({ type: i.type, slug: i.slug })), site_ids: ids }) });
        const d = await r.json().catch(() => ({})); this.inst.out = d.results || d.items || [{ ok: r.ok, message: d.detail || (r.ok ? 'ok' : 'errore') }];
        await this.load(true);
      } finally { this.inst.busy = false; }
    },

    // ---------- scadenze domini + registro globale plugin/temi ----------
    async loadDomainExpiries() {
      this.exp.loadingDomains = true;
      try { const r = await this.api('/api/domain-expiries'); if (r.ok) this.exp.domains = await r.json(); }
      finally { this.exp.loadingDomains = false; }
    },
    async loadComponentExpiries() {
      this.exp.loadingComponents = true;
      try { const r = await this.api('/api/component-expiries'); if (r.ok) this.exp.components = await r.json(); }
      finally { this.exp.loadingComponents = false; }
    },
    async scanDomains() {
      const r = await this.api('/api/domain-expiries/scan', { method: 'POST' });
      if (!r.ok) { this.say('Errore nell’avvio del controllo'); return; }
      this.say('Scansione domini avviata');
      [5000, 15000, 30000, 60000, 120000].forEach(ms => setTimeout(async () => {
        if (this.route.page === 'domain-expiries') await this.loadDomainExpiries();
        if (this.route.page === 'site' && this.detail) await this.loadDetail(this.detail.id);
      }, ms));
    },
    newExpiry() {
      this.expiryForm = { id: null, platform: 'both', name: '', provider: '', notes: '', date: '', recur: 12, recurCustom: 0 };
      this.expiryErr = ''; this.expiryEdit = true;
    },
    editExpiry(x) {
      const rm = x.recur_months || 0, std = [0, 1, 3, 6, 12, 24].includes(rm);
      this.expiryForm = { id: x.id, platform: x.platform || 'both', name: x.name, provider: x.provider || '', notes: x.notes || '', date: this.dateOnly(x.expires_at), recur: std ? rm : -1, recurCustom: std ? 0 : rm };
      this.expiryErr = ''; this.expiryEdit = true;
    },
    async saveExpiry() {
      this.expiryErr = ''; const f = this.expiryForm;
      if (!f.name.trim() || !f.date) { this.expiryErr = 'Nome e data sono obbligatori'; return; }
      const recur = f.recur === -1 ? Math.max(0, parseInt(f.recurCustom) || 0) : (parseInt(f.recur) || 0);
      const body = { category: 'Licenza', platform: f.platform || 'both', name: f.name.trim(), provider: f.provider.trim(), notes: f.notes.trim(), expires_at: f.date + 'T12:00:00Z', recur_months: recur };
      const r = await this.api(f.id ? `/api/component-expiries/${f.id}` : '/api/component-expiries', { method: f.id ? 'PATCH' : 'POST', body: JSON.stringify(body) });
      if (!r.ok) { this.expiryErr = (await r.json().catch(()=>({}))).detail || 'Errore nel salvataggio'; return; }
      this.expiryEdit = false; this.say(f.id ? 'Scadenza aggiornata' : 'Scadenza aggiunta');
      await this.loadComponentExpiries();
    },
    async renewExpiry(x) {
      if (!confirm(`Segnare "${x.name}" come rinnovata? La scadenza avanza di un periodo (${x.recur_label}).`)) return;
      const r = await this.api(`/api/component-expiries/${x.id}/renew`, { method: 'POST' });
      if (!r.ok) { this.say((await r.json().catch(()=>({}))).detail || 'Errore'); return; }
      const d = await r.json(); this.say(`${x.name}: nuova scadenza ${this.fmtDay(d.expires_at)}`);
      await this.loadComponentExpiries();
    },
    async deleteExpiry(x) {
      if (!confirm(`Eliminare la scadenza "${x.name}"?`)) return;
      await this.api(`/api/component-expiries/${x.id}`, { method: 'DELETE' }); this.say('Scadenza eliminata');
      await this.loadComponentExpiries();
    },

    // ---------- impostazioni operative (no segreti) ----------
    async loadPrefs() {
      const r = await this.api('/api/preferences');
      if (!r.ok) return;
      const d = await r.json();
      this.prefs = { ...this.prefs, ...d, domain_alert_text: (d.domain_alert_days||[30,14,7]).join(', '), component_alert_text: (d.component_alert_days||[30,14,7]).join(', '), msg: '', err: '', busy: false };
      this.prefsLoaded = true;
    },
    parseAlertDays(v) { return [...new Set(String(v||'').split(/[,;\s]+/).map(x=>parseInt(x,10)).filter(x=>Number.isFinite(x)&&x>0&&x<=3650))].sort((a,b)=>b-a); },
    async savePrefs() {
      this.prefs.msg = this.prefs.err = ''; this.prefs.busy = true;
      try {
        const da = this.parseAlertDays(this.prefs.domain_alert_text), ca = this.parseAlertDays(this.prefs.component_alert_text);
        if (!da.length || !ca.length) { this.prefs.err = 'Inserisci almeno una soglia valida per domini e plugin/temi'; return; }
        // NB: i campi numerici si costruiscono da una lista, non a mano: cosi' aggiungere
        // una nuova impostazione non richiede di ricordarsi di inserirla anche qui
        // (era successo con screenshot_every_hours: il valore non partiva e tornava al default).
        const NUM_KEYS = ['domain_scan_days', 'domain_parallel_lookups', 'expiry_warning_days',
                          'expiry_critical_days', 'screenshot_every_hours', 'history_retention_days'];
        const body = { domain_alert_days: da, component_alert_days: ca };
        for (const k of NUM_KEYS) {
          const v = parseInt(this.prefs[k], 10);
          if (Number.isFinite(v)) body[k] = v;
        }
        const r = await this.api('/api/preferences', { method: 'PUT', body: JSON.stringify(body) }); const d = await r.json().catch(()=>({}));
        if (!r.ok) { this.prefs.err = d.detail || 'Errore nel salvataggio'; return; }
        this.prefs = { ...this.prefs, ...d, domain_alert_text: d.domain_alert_days.join(', '), component_alert_text: d.component_alert_days.join(', '), msg: 'Salvato', err: '', busy: false };
      } finally { this.prefs.busy = false; }
    },

    // ---------- branding ----------
    async loadBrand() {
      try { const r = await fetch('/api/branding'); if (r.ok) { this.brand = { ...this.brand, ...(await r.json()) }; const f = document.getElementById('app-favicon'); if (f) f.href = this.brand.favicon_url; } } catch (e) { }
    },
    async uploadBrand(kind, ev) {
      const f = ev.target.files && ev.target.files[0]; if (!f) return; this.brand.busy = true;
      try { const fd = new FormData(); fd.append('file', f); const r = await fetch(`/api/branding/${kind}`, { method: 'POST', headers: { 'Authorization': 'Bearer ' + this.token }, body: fd }); if (!r.ok) { this.say((await r.json().catch(()=>({}))).detail || 'Upload fallito'); return; } await this.loadBrand(); this.say(kind === 'logo' ? 'Logo aggiornato' : 'Favicon aggiornata'); }
      finally { this.brand.busy = false; ev.target.value = ''; }
    },
    async resetBrand(kind) { const r = await this.api(`/api/branding/${kind}`, { method: 'DELETE' }); if (r.ok) { await this.loadBrand(); this.say(kind === 'logo' ? 'Logo predefinito ripristinato' : 'Favicon predefinita ripristinata'); } },

    // ---------- settings: connettori / account / telegram ----------
    async saveHubUrl() {
      this.conn.msg = this.conn.err = '';
      const r = await this.api('/api/connectors/hub-url', { method: 'PUT', body: JSON.stringify({ url: this.conn.hubUrl }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { this.conn.err = d.detail || 'Indirizzo non salvato'; return; }
      this.conn.hubUrl = this.conn.hubSaved = d.url || '';
      this.conn.msg = 'Indirizzo salvato: il pacchetto WordPress verrà generato con questo indirizzo';
    },
    useCurrentHubUrl() { this.conn.hubUrl = window.location.origin; },
    get hubDirty() { return (this.conn.hubUrl || '') !== (this.conn.hubSaved || ''); },

    async loadConn() {
      try {
        const r = await this.api('/api/connectors'); if (r.ok) this.conn.list = await r.json();
        const k = await this.api('/api/connectors/regkey'); if (k.ok) this.conn.regKey = (await k.json()).key || '';
        const h = await this.api('/api/connectors/hub-url');
        if (h.ok) { const d = await h.json(); this.conn.hubUrl = this.conn.hubSaved = d.url || ''; }
      } catch (e) { }
    },
    async resetConn(kind) {
      if (!confirm('Tornare al pacchetto incluso in Sentinel TD? Lo zip caricato verrà rimosso dall\'archivio.')) return;
      const r = await this.api(`/api/connectors/${kind}`, { method: 'DELETE' });
      if (!r.ok) { this.conn.err = 'Non riuscito'; return; }
      this.conn.msg = 'Ora viene usato il pacchetto incluso';
      await this.loadConn();
    },
    async uploadConn(kind, ev) {
      const f = ev.target.files && ev.target.files[0]; if (!f) return; this.conn.busy = true; this.conn.err = ''; this.conn.msg = '';
      try { const fd = new FormData(); fd.append('package', f); const r = await fetch('/api/connectors/' + kind, { method: 'POST', headers: { 'Authorization': 'Bearer ' + this.token }, body: fd }); if (r.ok) { this.conn.msg = 'Connettore caricato'; await this.loadConn(); } else this.conn.err = (await r.json().catch(() => ({}))).detail || 'Upload fallito'; }
      finally { this.conn.busy = false; ev.target.value = ''; }
    },
    async downloadConn(kind) {
      // niente token in query: lo zip WP contiene la chiave di registrazione, quindi
      // si scarica con l'header Authorization e si salva come blob
      try {
        const r = await this.api(`/api/connectors/${kind}/download`);
        if (!r.ok) { this.say('Download non riuscito'); return; }
        const blob = await r.blob();
        const cd = r.headers.get('content-disposition') || '';
        const m = cd.match(/filename="?([^";]+)"?/);
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (m && m[1]) || `${kind}.zip`;
        document.body.appendChild(a); a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
      } catch (e) { this.say('Download non riuscito'); }
    },
    async rotateKey() { if (!confirm('Rigenerare la chiave di registrazione? Quella nei connettori già distribuiti smette di valere per NUOVI collegamenti.')) return; const r = await this.api('/api/connectors/regkey/rotate', { method: 'POST' }); if (r.ok) { this.conn.regKey = (await r.json()).key; this.say('Chiave rigenerata'); } },
    copy(t) { navigator.clipboard?.writeText(t).then(() => this.say('Copiato')); },
    async telegramTest() { const r = await this.api('/api/sites/telegram/test', { method: 'POST' }); this.say(r.ok ? 'Messaggio Telegram inviato' : 'Invio fallito: controlla token/chat'); },
    async saveUsername() { this.account.msg = this.account.err = ''; const r = await this.api('/api/account/username', { method: 'POST', body: JSON.stringify({ username: this.account.username }) }); r.ok ? this.account.msg = 'Username aggiornato' : this.account.err = (await r.json().catch(() => ({}))).detail || 'Errore'; },
    async savePassword() { this.account.msg = this.account.err = ''; const r = await this.api('/api/account/password', { method: 'POST', body: JSON.stringify({ old_password: this.account.oldPw, new_password: this.account.newPw }) }); if (r.ok) { this.account.msg = 'Password aggiornata'; this.account.oldPw = this.account.newPw = ''; } else this.account.err = (await r.json().catch(() => ({}))).detail || 'Errore'; },

    // ---------- notifiche (editor visuale + template Jinja) ----------
    async loadNotif(forceCurrent = false) {
      const previous = this.notif.cur && this.notif.cur.event;
      const r = await this.api('/api/notifications'); if (r.ok) this.notif.events = await r.json();
      if (!this.notif.events.length) return;
      if (forceCurrent || !this.notif.cur) this.selNotif((previous && this.notif.events.some(x => x.event === previous)) ? previous : this.notif.events[0].event);
    },
    selNotif(ev) {
      const e = this.notif.events.find(x => x.event === ev); if (!e) return;
      this.notif.cur = e; this.notif.edit = { ...e.config }; this.notif.msg = this.notif.err = '';
      this.notif.preview = null; this.notif.source = false; this.notif.tab = 'email'; this.notif.previewTab = 'email';
      this.$nextTick(() => { this.loadNotifEditors(true); this.previewNotif(); });
    },
    async previewNotif() {
      if (!this.notif.cur || !this.notif.edit) return;
      const e = this.notif.edit;
      const r = await this.api(`/api/notifications/${this.notif.cur.event}/preview`, { method: 'POST', body: JSON.stringify({ subject: e.subject, body_email: e.body_email, body_telegram: e.body_telegram }) });
      if (r.ok) this.notif.preview = await r.json();
      else { const d = await r.json().catch(() => ({})); this.notif.preview = { subject: '', body_email: '', body_telegram: '', error: d.detail || 'Anteprima non disponibile' }; }
    },
    loadNotifEditors(force = false, attempt = 0) {
      if (!this.notif.edit) return;
      // Al primo ingresso nella pagina l'editor viene chiesto prima che Alpine abbia
      // creato il contenteditable (blocco annidato: un solo $nextTick non basta):
      // l'innerHTML finiva nel nulla e il riquadro restava bianco finche' non si
      // cambiava notifica. Se l'elemento della tab attiva non c'e' ancora, riprova
      // al frame successivo.
      const activeId = 'nf_visual_' + (this.notif.tab === 'telegram' ? 'telegram' : 'email');
      if (this.notif.tab !== 'subject' && !this.notif.source && !document.getElementById(activeId) && attempt < 20) {
        requestAnimationFrame(() => this.loadNotifEditors(force, attempt + 1));
        return;
      }
      for (const kind of ['email', 'telegram']) {
        const el = document.getElementById('nf_visual_' + kind); if (!el) continue;
        const field = kind === 'email' ? 'body_email' : 'body_telegram';
        const raw = this.notif.edit[field] || '';
        if (force || el.__sentinelRaw !== raw) {
          el.innerHTML = this.jinjaToEditable(raw);
          el.__sentinelRaw = raw;
        }
      }
    },
    jinjaToEditable(raw) {
      const box = document.createElement('div'); box.innerHTML = raw || '';
      const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
      const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
      const re = /(\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/g;
      for (const node of nodes) {
        if (!node.parentNode || ['SCRIPT', 'STYLE'].includes(node.parentNode.nodeName)) continue;
        const txt = node.nodeValue || ''; if (!re.test(txt)) { re.lastIndex = 0; continue; } re.lastIndex = 0;
        const frag = document.createDocumentFragment(); let at = 0; let m;
        while ((m = re.exec(txt))) {
          if (m.index > at) frag.appendChild(document.createTextNode(txt.slice(at, m.index)));
          const token = m[0]; const chip = document.createElement('span');
          chip.className = token.startsWith('{{') ? 'wys-token' : 'wys-control'; chip.contentEditable = 'false'; chip.dataset.jinja = token;
          chip.textContent = token.startsWith('{{') ? token : this.jinjaControlLabel(token);
          frag.appendChild(chip); at = m.index + token.length;
        }
        if (at < txt.length) frag.appendChild(document.createTextNode(txt.slice(at)));
        node.parentNode.replaceChild(frag, node);
      }
      return box.innerHTML;
    },
    jinjaControlLabel(token) {
      const x = token.replace(/^\{%\s*|\s*%\}$/g, '').trim();
      if (/^endif\b/.test(x)) return 'fine condizione';
      if (/^else\b/.test(x)) return 'altrimenti';
      if (/^if\s+/.test(x)) return 'se ' + x.replace(/^if\s+/, '');
      if (/^endfor\b/.test(x)) return 'fine elenco';
      if (/^for\s+/.test(x)) return 'elenco ' + x.replace(/^for\s+/, '');
      return x;
    },
    editableToJinja(kind) {
      const el = document.getElementById('nf_visual_' + kind); if (!el) return null;
      const clone = el.cloneNode(true);
      clone.querySelectorAll('[data-jinja]').forEach(n => n.replaceWith(document.createTextNode(n.dataset.jinja || '')));
      if (kind === 'telegram') return this.serializeTelegram(clone);
      return clone.innerHTML;
    },
    serializeTelegram(root) {
      const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const attr = t => esc(t).replace(/"/g, '&quot;');
      const walk = node => {
        if (node.nodeType === Node.TEXT_NODE) return esc(node.nodeValue || '');
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        const tag = node.tagName.toLowerCase();
        const inside = Array.from(node.childNodes).map(walk).join('');
        if (tag === 'br') return '\n';
        if (tag === 'b' || tag === 'strong') return `<b>${inside}</b>`;
        if (tag === 'i' || tag === 'em') return `<i>${inside}</i>`;
        if (tag === 'code') return `<code>${inside}</code>`;
        if (tag === 'pre') return `<pre>${inside}</pre>`;
        if (tag === 'a') return `<a href="${attr(node.getAttribute('href') || '')}">${inside}</a>`;
        if (tag === 'div' || tag === 'p') return inside + '\n';
        if (tag === 'li') return '• ' + inside + '\n';
        return inside;
      };
      return Array.from(root.childNodes).map(walk).join('').replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '');
    },
    notifEditorInput(kind) {
      if (!this.notif.edit) return;
      const field = kind === 'email' ? 'body_email' : 'body_telegram'; const raw = this.editableToJinja(kind);
      if (raw !== null) { this.notif.edit[field] = raw; const el = document.getElementById('nf_visual_' + kind); if (el) el.__sentinelRaw = raw; }
      this.notifRememberSelection(kind); this.previewNotif();
    },
    notifRememberSelection(kind) {
      const el = document.getElementById('nf_visual_' + kind); const sel = window.getSelection();
      if (!el || !sel || !sel.rangeCount) return; const r = sel.getRangeAt(0);
      if (el.contains(r.commonAncestorContainer)) { window.__sentinelNotifRanges = window.__sentinelNotifRanges || {}; window.__sentinelNotifRanges[kind] = r.cloneRange(); }
    },
    restoreNotifSelection(kind) {
      const el = document.getElementById('nf_visual_' + kind); if (!el) return null; el.focus();
      const sel = window.getSelection(); const saved = window.__sentinelNotifRanges && window.__sentinelNotifRanges[kind];
      if (sel && saved && el.contains(saved.commonAncestorContainer)) { sel.removeAllRanges(); sel.addRange(saved); return saved; }
      const r = document.createRange(); r.selectNodeContents(el); r.collapse(false); if (sel) { sel.removeAllRanges(); sel.addRange(r); } return r;
    },
    formatNotif(cmd, value = null) {
      const kind = this.notif.tab; if (!['email', 'telegram'].includes(kind)) return;
      this.restoreNotifSelection(kind); document.execCommand(cmd, false, value); this.notifEditorInput(kind); this.notifRememberSelection(kind);
    },
    notifLink() {
      const kind = this.notif.tab; if (!['email', 'telegram'].includes(kind)) return;
      this.restoreNotifSelection(kind); const url = prompt('Indirizzo del link:', 'https://'); if (!url) return;
      document.execCommand('createLink', false, url); this.notifEditorInput(kind); this.notifRememberSelection(kind);
    },
    notifColor(color) { if (this.notif.tab !== 'email') return; this.restoreNotifSelection('email'); document.execCommand('foreColor', false, color); this.notifEditorInput('email'); this.notifRememberSelection('email'); },
    captureNotifEditors() {
      if (!this.notif.edit || this.notif.source) return;
      for (const kind of ['email', 'telegram']) {
        const field = kind === 'email' ? 'body_email' : 'body_telegram'; const raw = this.editableToJinja(kind); if (raw !== null) this.notif.edit[field] = raw;
      }
    },
    async saveNotif() {
      this.notif.msg = this.notif.err = ''; this.notif.busy = true; this.captureNotifEditors();
      try {
        const r = await this.api(`/api/notifications/${this.notif.cur.event}`, { method: 'PUT', body: JSON.stringify(this.notif.edit) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { this.notif.err = d.detail || 'Errore'; return; }
        this.notif.cur.config = d.config; this.notif.edit = { ...d.config }; this.notif.msg = 'Salvato'; this.$nextTick(() => this.loadNotifEditors(true));
      } finally { this.notif.busy = false; }
    },
    async resetNotif() {
      if (!confirm('Ripristinare il template predefinito per questo evento?')) return;
      const r = await this.api(`/api/notifications/${this.notif.cur.event}/reset`, { method: 'POST' });
      if (r.ok) { const d = await r.json(); this.notif.cur.config = d.config; this.notif.edit = { ...d.config }; this.notif.msg = 'Ripristinato'; this.notif.source = false; this.$nextTick(() => { this.loadNotifEditors(true); this.previewNotif(); }); }
    },
    async testNotif() {
      this.notif.msg = this.notif.err = ''; this.notif.busy = true; this.captureNotifEditors();
      try {
        const e = this.notif.edit;
        const r = await this.api(`/api/notifications/${this.notif.cur.event}/test`, { method: 'POST', body: JSON.stringify({ subject: e.subject, body_email: e.body_email, body_telegram: e.body_telegram, email: e.email, telegram: e.telegram }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { this.notif.err = d.detail || 'Errore'; return; }
        const sent = [d.email ? 'email ✓' : '', d.telegram ? 'Telegram ✓' : ''].filter(Boolean).join(', ');
        this.notif.msg = sent ? ('Inviato: ' + sent + (d.error ? ' — ' + d.error : '')) : (d.error || 'Nessun canale attivo');
      } finally { this.notif.busy = false; }
    },
    insertVar(name) {
      const token = '{{ ' + name + ' }}';
      if (this.notif.tab === 'subject') {
        const el = document.getElementById('nf_subject'); const v = this.notif.edit.subject || '';
        if (el && typeof el.selectionStart === 'number') { const a = el.selectionStart, b = el.selectionEnd; this.notif.edit.subject = v.slice(0, a) + token + v.slice(b); this.$nextTick(() => { el.focus(); el.selectionStart = el.selectionEnd = a + token.length; }); }
        else this.notif.edit.subject = v + token;
        this.previewNotif(); return;
      }
      const kind = this.notif.tab; if (!['email', 'telegram'].includes(kind)) return;
      const el = document.getElementById('nf_visual_' + kind); if (!el) return;
      this.restoreNotifSelection(kind); const sel = window.getSelection(); if (!sel || !sel.rangeCount) return; const r = sel.getRangeAt(0);
      r.deleteContents(); const chip = document.createElement('span'); chip.className = 'wys-token'; chip.contentEditable = 'false'; chip.dataset.jinja = token; chip.textContent = token;
      r.insertNode(chip); r.setStartAfter(chip); r.collapse(true); sel.removeAllRanges(); sel.addRange(r); this.notifEditorInput(kind);
    },
    emailPreviewDoc() {
      const body = (this.notif.preview && this.notif.preview.body_email) || '';
      // il corpo email va mostrato come lo vedrebbe un client: foglio bianco con
      // margini, non incollato ai bordi dell'iframe
      return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        + '<style>html{background:#fff}body{margin:0;padding:26px 24px;background:#fff;color:#172033;font-family:Arial,Helvetica,sans-serif}'
        + '.tdp-mail{max-width:640px;margin:0 auto}img{max-width:100%;height:auto}a{word-break:break-word}</style></head>'
        + '<body><div class="tdp-mail">' + body + '</div></body></html>';
    },
    get notifDirty() { const c = this.notif.cur && this.notif.cur.config, e = this.notif.edit; return c && e && JSON.stringify(c) !== JSON.stringify(e); },

    // ---------- anteprima visiva del sito (screenshot) ----------
    shotUrl(site) {
      if (!site || !site.shot_path) return '';
      // il timestamp evita che il browser mostri la versione in cache dopo un refresh
      const v = site.shot_at ? new Date(site.shot_at).getTime() : 0;
      return `/api/sites/${site.id}/image?k=${encodeURIComponent(this.imageToken)}&v=${v}&n=${this.imgNonce}`;
    },
    thumbUrl(site) {
      if (!site || !site.shot_path) return '';
      const v = site.shot_at ? new Date(site.shot_at).getTime() : 0;
      return `/api/sites/${site.id}/image?k=${encodeURIComponent(this.imageToken)}&thumb=1&v=${v}&n=${this.imgNonce}`;
    },
    async shootBulk(onlySelected = false) {
      const ids = onlySelected ? [...this.selIds] : [];
      const quanti = onlySelected ? ids.length : this.sites.filter(s => s.enabled !== false).length;
      if (onlySelected && !ids.length) { this.say('Nessun sito selezionato'); return; }
      if (!confirm(`Rigenerare l'anteprima di ${quanti} siti? Gli scatti sono scaglionati, ci vorranno alcuni minuti.`)) return;
      const r = await this.api('/api/sites/bulk/shots', { method: 'POST', body: JSON.stringify({ ids }) });
      if (!r.ok) { this.say('Rigenerazione non avviata: coda non raggiungibile'); return; }
      const d = await r.json();
      this.say(`Anteprime accodate per ${d.queued} siti — circa ${d.eta_min} min`, 6000);
      if (onlySelected) this.exitSel();
      // ricarica dopo qualche minuto: le miniature si aggiornano man mano
      setTimeout(() => this.load(true), 90000);
    },
    async shootNow(site) {
      this.busy['shot' + site.id] = true;
      try {
        const r = await this.api(`/api/sites/${site.id}/shot`, { method: 'POST' });
        if (!r.ok) { this.say('Anteprima non richiesta: coda non raggiungibile'); return; }
        this.say('Anteprima in aggiornamento…');
        // lo scatto richiede qualche secondo: ricarico il sito finche' cambia il timestamp
        const before = site.shot_at || '';
        for (let i = 0; i < 12; i++) {
          await new Promise(res => setTimeout(res, 2500));
          const d = await this.api(`/api/sites/${site.id}`);
          if (!d.ok) break;
          const fresh = await d.json();
          if ((fresh.shot_at || '') !== before) {
            if (this.detail && this.detail.id === site.id) this.detail = fresh;
            const inList = this.sites.find(x => x.id === site.id);
            if (inList) Object.assign(inList, fresh);
            this.say('Anteprima aggiornata');
            return;
          }
        }
        this.say('Anteprima non ancora pronta: ricarica tra poco');
      } finally { this.busy['shot' + site.id] = false; }
    },

    // ---------- anteprime ----------
    fitFrame(el, min = 200, max = 3000) {
      // adatta l'altezza dell'iframe al contenuto: niente barre interne, niente tagli
      try {
        const d = el.contentDocument;
        if (!d || !d.body) return;
        const h = Math.max(d.body.scrollHeight, d.documentElement.scrollHeight);
        el.style.height = Math.min(max, Math.max(min, h + 24)) + 'px';
      } catch (e) { /* documento non leggibile: resta l'altezza di default */ }
    },
    previewFull: '',
    openPreviewFull(html) { this.previewFull = html || ''; },

    // ---------- statistiche ----------
    async loadStats(first = true) {
      this.stats.busy = true;
      try {
        if (first && !this.stats.periods.length) {
          const [p, sc] = await Promise.all([this.api('/api/reports/periods'), this.api('/api/reports/scopes')]);
          if (p.ok) this.stats.periods = await p.json();
          if (sc.ok) this.stats.scopes = await sc.json();
          if (!this.stats.period) this.stats.period = this.stats.periods.length ? this.stats.periods[0].period : '';
          if (!this.stats.cmpB) this.stats.cmpB = this.stats.period;
          if (!this.stats.cmpA && this.stats.periods.length > 1) this.stats.cmpA = this.stats.periods[1].period;
        }
        const qs = `scope=${encodeURIComponent(this.stats.scope)}`;
        if (this.stats.mode === 'days') {
          // vista giornaliera: stessa forma di dati, stesso grafico e stesse classifiche
          const r = await this.api(`/api/stats/daily?days=${this.stats.days}&${qs}`);
          if (r.ok) { const d = await r.json(); this.stats.trend = d.trend; this.stats.data = d.data; }
          return;
        }
        // NB: il trend NON passa il periodo selezionato: la finestra e' sempre
        // "ultimi N mesi fino a oggi". Altrimenti ogni clic su una barra spostava
        // la finestra indietro e si finiva a navigare anni passati vuoti.
        const [o, t] = await Promise.all([
          this.api(`/api/stats/overview?period=${encodeURIComponent(this.stats.period)}&${qs}`),
          this.api(`/api/reports/trend?months=${this.stats.months}&${qs}`),
        ]);
        if (o.ok) this.stats.data = await o.json();
        if (t.ok) this.stats.trend = await t.json();
        if (this.stats.mode === 'compare') await this.loadCompare();
      } finally { this.stats.busy = false; }
    },
    // ---- navigazione mese per mese (frecce e tastiera) ----
    curPeriod() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; },
    shiftPeriod(p, delta) {
      const t = (+p.slice(0, 4)) * 12 + (+p.slice(5, 7) - 1) + delta;
      return `${String(Math.floor(t / 12)).padStart(4, '0')}-${String(t % 12 + 1).padStart(2, '0')}`;
    },
    canShiftStat(delta) {
      if (!this.stats.period) return false;
      const p = this.shiftPeriod(this.stats.period, delta);
      if (delta > 0) return p <= this.curPeriod();          // nessun mese nel futuro
      return this.stats.months < 24 || this.statMonths.some(m => m.period === p);
    },
    async shiftStatMonth(delta) {
      if (!this.canShiftStat(delta)) return;
      const p = this.shiftPeriod(this.stats.period, delta);
      // se il mese esce dalla finestra del grafico, allarga la finestra (6 -> 12 -> 24)
      if (!this.statMonths.some(m => m.period === p)) {
        const next = [6, 12, 24].find(t => t > this.stats.months);
        if (!next) { this.say('Oltre 24 mesi indietro non è disponibile'); return; }
        this.stats.months = next; this.stats.period = p;
        await this.loadStats(false);
        return;
      }
      await this.pickMonth(p);
    },

    async pickMonth(p) {
      if (this.stats.mode === 'days') return;   // in vista giornaliera non c'e' un mese da aprire
      if (!p || p === this.stats.period) return;
      this.stats.period = p; this.stats.mode = 'month';
      this.stats.busy = true;
      try {
        const r = await this.api(`/api/stats/overview?period=${encodeURIComponent(p)}&scope=${encodeURIComponent(this.stats.scope)}`);
        if (r.ok) this.stats.data = await r.json();
      } finally { this.stats.busy = false; }
    },
    // i mesi selezionabili sono quelli della finestra mostrata nel grafico
    get statMonths() {
      if (this.stats.trend && this.stats.trend.months) return [...this.stats.trend.months].reverse();
      return this.stats.periods.map(p => ({ period: p.period, label: p.label, updates: p.updates }));
    },
    get statEmptyWindow() { return !!(this.stats.trend && this.stats.trend.months && this.stats.trend.total === 0); },

    async loadCompare() {
      if (!this.stats.cmpA || !this.stats.cmpB) return;
      const r = await this.api(`/api/stats/compare?a=${encodeURIComponent(this.stats.cmpA)}&b=${encodeURIComponent(this.stats.cmpB)}&scope=${encodeURIComponent(this.stats.scope)}`);
      if (r.ok) this.stats.cmp = await r.json();
    },
    // colore stabile e distinto per nome: lo stesso sito/plugin ha sempre la sua tinta,
    // in ogni schermata e a ogni ricarica (hash del nome -> tonalita').
    // Colori delle classifiche: assegnati per POSIZIONE con l'angolo aureo (137.5°),
    // la tecnica dei grafici veri: due voci vicine in lista non possono mai avere
    // tinte simili, e la palette resta coerente qualunque sia il numero di righe.
    colorIdx(i) { const c = this._palette(i); return `hsl(${c.h} ${c.s}% ${c.l}%)`; },
    colorGradIdx(i) {
      const c = this._palette(i);
      return `linear-gradient(90deg, hsl(${c.h} ${c.s}% ${c.l}%), hsl(${(c.h + 22) % 360} ${c.s}% ${Math.min(72, c.l + 9)}%))`;
    },
    _palette(i) {
      const n = Number(i) || 0;
      return { h: Math.round((n * 137.508 + 16) % 360), s: 62 + (n % 3) * 7, l: 52 + (n % 2) * 6 };
    },

    statsPdfUrl() {
      const base = `/api/stats/pdf?k=${encodeURIComponent(this.imageToken)}&scope=${encodeURIComponent(this.stats.scope)}`;
      return this.stats.mode === 'compare'
        ? `${base}&mode=compare&a=${encodeURIComponent(this.stats.cmpA)}&b=${encodeURIComponent(this.stats.cmpB)}`
        : `${base}&mode=month&period=${encodeURIComponent(this.stats.period)}`;
    },
    statBar(v, max) { return Math.max(2, Math.round((v || 0) * 100 / Math.max(1, max))) + '%'; },
    statTrendH(m) { const max = (this.stats.trend && this.stats.trend.max) || 1; return m.updates ? Math.max(4, Math.round(m.updates * 130 / max)) + 'px' : '0px'; },
    statDelta(cur, prev) { const d = (cur || 0) - (prev || 0); const p = prev ? Math.round(d * 100 / prev) : null; return { d, p, cls: d > 0 ? 'ok' : (d < 0 ? 'err' : 'mut'), txt: (d > 0 ? '+' : '') + d + (p === null ? '' : ` (${d > 0 ? '+' : ''}${p}%)`) }; },
    statDeltaFail(cur, prev) { const x = this.statDelta(cur, prev); x.cls = x.d > 0 ? 'err' : (x.d < 0 ? 'ok' : 'mut'); return x; },
    // ---- grafico andamento ----
    // Scala "gentile": passo 1/2/5 x 10^n con al massimo 5 righe guida, cosi' le tacche
    // dell'asse sono numeri tondi (50, 100, 150…) invece di valori come 62,5.
    get trendScale() {
      const ms = (this.stats.trend && this.stats.trend.months) || [];
      const max = Math.max(1, ...ms.map(m => (m.updates || 0) + (m.failed || 0)));
      const pow = Math.pow(10, Math.floor(Math.log10(max)));
      let step = pow;
      for (const c of [0.1, 0.2, 0.5, 1, 2, 5, 10]) {
        const s = c * pow;
        if (s >= 1 && Math.ceil(max / s) <= 5) { step = s; break; }
      }
      step = Math.max(1, Math.round(step));
      const top = Math.ceil(max / step) * step;
      const ticks = [];
      for (let v = step; v <= top; v += step) ticks.push(v);
      return { top, ticks };
    },
    trendY(v) { return Math.round(Math.max(0, v || 0) / (this.trendScale.top || 1) * 200); },   // 200 = altezza area grafico in CSS
    get trendBest() {
      const ms = (this.stats.trend && this.stats.trend.months) || [];
      let best = null;
      for (const m of ms) if (!best || (m.updates || 0) > (best.updates || 0)) best = m;
      return best && best.updates ? { value: this.fmtNum(best.updates), label: this.barLabel(best), period: best.period }
                                   : { value: '—', label: '', period: '' };
    },
    // Card "rispetto a prima": nei mesi e' l'ultimo mese vs il precedente; nei giorni
    // e' il periodo intero vs il periodo precedente (altrimenti confronterebbe ieri e oggi).
    get trendPeriodDelta() {
      if (this.stats.mode === 'days' && this.stats.data) {
        const cur = this.stats.data.totals.updates || 0, prev = this.stats.data.prev_totals.updates || 0;
        return { delta: cur - prev, delta_pct: prev ? Math.round((cur - prev) * 100 / prev) : null };
      }
      return this.trendLast;
    },
    // Etichette dell'asse: con molti punti se ne mostra una ogni N per non accavallarle
    axisLabel(m, i, total) {
      const step = Math.max(1, Math.ceil(total / 12));
      const show = i % step === 0 || i === total - 1 || m.period === this.stats.period;
      return show ? this.barLabel(m) : '';
    },
    get trendLast() {
      const ms = (this.stats.trend && this.stats.trend.months) || [];
      return ms.length ? ms[ms.length - 1] : { delta: null, label: '' };
    },
    fmtNum(v, d = 0) {
      const n = Number(v || 0);
      try { return n.toLocaleString((window.I18n && I18n.locale) || 'it', { maximumFractionDigits: d }); }
      catch (e) { return String(n); }
    },
    trendShowVal(m) {
      if (!((m.updates || 0) + (m.failed || 0))) return false;
      // con 24 mesi i numeri si accavallano: solo selezionato, sotto il mouse e migliore
      return this.stats.months <= 12 || m.period === this.stats.period || m.period === this.stats.hover || m.period === this.trendBest.period;
    },
    // etichetta dell'asse: i punti giornalieri hanno il proprio testo breve
    barLabel(m) { return m.short || this.shortMonth(m.label); },
    // riepilogo per giorno della settimana (solo in vista giornaliera)
    get statWeekdays() {
      if (this.stats.mode !== 'days' || !this.stats.trend) return [];
      const names = ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom'];
      const acc = names.map((n, i) => ({ name: n, updates: 0, days: 0, idx: i }));
      for (const d of this.stats.trend.months || []) {
        const w = acc[d.weekday ?? 0];
        w.updates += d.updates || 0; w.days += 1;
      }
      const max = Math.max(1, ...acc.map(a => a.updates));
      return acc.map(a => ({ ...a, avg: a.days ? a.updates / a.days : 0, pct: Math.round(a.updates * 100 / max) }));
    },
    shortMonth(label) { const p = (label || '').split(' '); return p.length > 1 ? p[0].slice(0, 3) + ' ' + p[1].slice(2) : label; },

    // ---------- report dettagliato su richiesta ----------
    async openDetailReport(ids = []) {
      if (!this.drep.oldest) {
        try {
          const r = await this.api('/api/reports/periods');
          if (r.ok) { const ps = (await r.json()).map(x => x.period).filter(Boolean).sort(); this.drep.oldest = ps[0] || this.curPeriod(); }
        } catch (e) { }
      }
      Object.assign(this.drep, { filter: '', busy: false });
      if (ids && ids.length) { this.drep.mode = 'pick'; this.drep.ids = [...new Set(ids)]; }
      else if (this.route.page === 'sites' && this.route.folder && this.route.folder !== '__none') { this.drep.mode = 'folder'; this.drep.folder = this.route.folder; }
      else { this.drep.mode = 'all'; }
      if (!this.drep.from) this.drepRange('q3');
      this.drawer = 'detailrep';
    },
    drepRange(key) {
      const cur = this.curPeriod();
      const map = { cur: [cur, cur], prev: [this.shiftPeriod(cur, -1), this.shiftPeriod(cur, -1)],
                    q3: [this.shiftPeriod(cur, -2), cur], y1: [this.shiftPeriod(cur, -11), cur],
                    all: [this.drep.oldest || this.shiftPeriod(cur, -11), cur] };
      const [a, b] = map[key] || map.q3;
      Object.assign(this.drep, { from: a, to: b, range: key });
    },
    // mesi selezionabili: dal primo mese con dati (o 24 mesi fa) a oggi, dal piu' recente
    get drepMonths() {
      const cur = this.curPeriod();
      let start = this.drep.oldest && this.drep.oldest < this.shiftPeriod(cur, -23) ? this.drep.oldest : this.shiftPeriod(cur, -23);
      const out = []; let p = cur, guard = 0;
      while (p >= start && guard++ < 240) { out.push(p); p = this.shiftPeriod(p, -1); }
      return out;
    },
    drepMonthLabel(p) {
      // nome del mese nella lingua attiva dell'interfaccia (Intl, niente catalogo)
      if (!p) return '';
      try {
        const lang = (window.I18n && I18n.locale) || 'it';
        const s = new Date(+p.slice(0, 4), +p.slice(5, 7) - 1, 1).toLocaleDateString(lang, { month: 'long', year: 'numeric' });
        return s.charAt(0).toUpperCase() + s.slice(1);
      } catch (e) { return p; }
    },
    get drepFolders() { return this.allTags || []; },
    drepSites() {
      const f = (this.drep.filter || '').trim().toLowerCase();
      const list = this.sites.filter(s => s.enabled !== false);
      return f ? list.filter(s => (s.name || '').toLowerCase().includes(f) || (s.url || '').toLowerCase().includes(f) || (s.tags || '').toLowerCase().includes(f)) : list;
    },
    drepToggleSite(id) { this.drep.ids = this.drep.ids.includes(id) ? this.drep.ids.filter(x => x !== id) : [...this.drep.ids, id]; },
    drepSelAll() { this.drep.ids = [...new Set([...this.drep.ids, ...this.drepSites().map(s => s.id)])]; },
    drepSelNone() {
      const vis = new Set(this.drepSites().map(s => s.id));
      this.drep.ids = (this.drep.filter || '').trim() ? this.drep.ids.filter(id => !vis.has(id)) : [];
    },
    get drepTargetCount() {
      if (this.drep.mode === 'pick') return this.drep.ids.length;
      if (this.drep.mode === 'folder') return this.drep.folder ? this.folderSites(this.drep.folder).length : 0;
      return this.sites.filter(s => s.enabled !== false).length;
    },
    get drepCanRun() {
      if (this.drep.busy || !this.drep.from || !this.drep.to) return false;
      if (this.drep.mode === 'pick') return this.drep.ids.length > 0;
      if (this.drep.mode === 'folder') return !!this.drep.folder;
      return true;
    },
    get drepSummary() {
      const n = this.drepTargetCount;
      const per = this.drep.from === this.drep.to ? this.drepMonthLabel(this.drep.from || this.curPeriod())
                : `${this.drepMonthLabel(this.drep.from)} – ${this.drepMonthLabel(this.drep.to)}`;
      if (!n) return 'Scegli almeno un sito';
      return `${this.drep.format === 'csv' ? 'CSV' : 'PDF'} · ${n} ${this.pl(n, 'sito', 'siti')} · ${per}`;
    },
    async downloadDetailReport() {
      if (!this.drepCanRun) return;
      this.drep.busy = true;
      try {
        let a = this.drep.from, b = this.drep.to; if (a > b) [a, b] = [b, a];
        const body = { from: a, to: b, history: this.drep.history, failed: this.drep.failed, format: this.drep.format,
                       site_ids: this.drep.mode === 'pick' ? this.drep.ids : [],
                       folder: this.drep.mode === 'folder' ? this.drep.folder : '' };
        const r = await this.api('/api/reports/detail', { method: 'POST', body: JSON.stringify(body) });
        if (!r.ok) { const d = await r.json().catch(() => ({})); this.say(d.detail || 'Report non generato'); return; }
        const blob = await r.blob();
        const cd = r.headers.get('content-disposition') || '';
        const m = cd.match(/filename="?([^";]+)"?/);
        const el = document.createElement('a');
        el.href = URL.createObjectURL(blob);
        el.download = (m && m[1]) || `report-dettagliato.${this.drep.format}`;
        document.body.appendChild(el); el.click();
        setTimeout(() => { URL.revokeObjectURL(el.href); el.remove(); }, 2000);
        this.say('Report scaricato');
      } catch (e) { this.say('Report non generato'); }
      finally { this.drep.busy = false; }
    },

    // ---------- report mensile ----------
    async loadReports() {
      const [c, p, sc] = await Promise.all([this.api('/api/reports/config'), this.api('/api/reports/periods'), this.api('/api/reports/scopes')]);
      if (c.ok) { const d = await c.json(); this.rep.cfg = d.config; this.rep.template = d.template; this.rep.defaultTemplate = d.default_template; if (!this.rep.period) this.rep.period = d.suggested_period; }
      if (p.ok) { this.rep.periods = await p.json(); if (!this.rep.periods.some(x => x.period === this.rep.period) && this.rep.periods.length) this.rep.period = this.rep.periods[0].period; }
      if (sc.ok) { this.rep.scopes = await sc.json(); if (!this.rep.scopes.some(x => x.key === this.rep.scope) && this.rep.scopes.length) this.rep.scope = this.rep.scopes[0].key; }
      this.rep.dirty = false;
      await Promise.all([this.previewReport(), this.loadTrend()]);
    },
    async loadTrend() {
      // finestra ancorata a oggi (come nelle statistiche): il mese scelto evidenzia
      // una barra, non sposta l'intervallo mostrato
      const r = await this.api(`/api/reports/trend?scope=${encodeURIComponent(this.rep.scope)}&months=${this.rep.trendMonths}`);
      if (r.ok) this.rep.trend = await r.json();
    },
    trendBarH(m) { const max = (this.rep.trend && this.rep.trend.max) || 1; return m.updates ? Math.max(4, Math.round(m.updates * 120 / max)) + 'px' : '0px'; },
    deltaTxt(m) { if (m.delta === null || m.delta === undefined) return '—'; const s = m.delta > 0 ? '+' : ''; return s + m.delta + (m.delta_pct === null || m.delta_pct === undefined ? '' : ` (${s}${m.delta_pct}%)`); },
    deltaCls(m) { if (!m.delta) return 'mut'; return m.delta > 0 ? 'ok' : 'err'; },
    // la lista scopes della UI e' la fonte: la config salvata viene ricostruita da qui
    syncScopes() {
      this.rep.cfg.scopes = this.rep.scopes.filter(x => x.enabled).map(x => ({ key: x.key, enabled: true }));
      if (!this.rep.cfg.scopes.length) this.rep.cfg.scopes = [{ key: '__all__', enabled: false }];
      this.rep.dirty = true;
    },
    get repEnabledCount() { return this.rep.scopes.filter(x => x.enabled).length; },
    async previewReport() {
      if (!this.rep.cfg) return;
      this.rep.busy = true; this.rep.err = '';
      try {
        const r = await this.api('/api/reports/preview', { method: 'POST', body: JSON.stringify({ period: this.rep.period, scope: this.rep.scope, config: this.rep.cfg, template: this.rep.advanced ? this.rep.template : null }) });
        if (r.ok) this.rep.html = await r.text();
        else { const d = await r.json().catch(() => ({})); this.rep.err = d.detail || 'Errore anteprima'; }
      } finally { this.rep.busy = false; }
    },
    async saveReportCfg() {
      this.rep.busy = true; this.rep.msg = this.rep.err = '';
      try {
        const body = { config: this.rep.cfg }; if (this.rep.advanced) body.template = this.rep.template;
        const r = await this.api('/api/reports/config', { method: 'PUT', body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { this.rep.err = d.detail || 'Errore'; return; }
        this.rep.cfg = d.config; this.rep.msg = 'Impostazioni salvate'; this.rep.dirty = false;
      } finally { this.rep.busy = false; }
    },
    resetReportTemplate() { if (!confirm('Ripristinare il layout PDF predefinito?')) return; this.rep.template = this.rep.defaultTemplate; this.rep.dirty = true; this.previewReport(); },
    reportPdfUrl() { return `/api/reports/pdf?period=${encodeURIComponent(this.rep.period)}&scope=${encodeURIComponent(this.rep.scope)}&k=${encodeURIComponent(this.imageToken)}`; },
    async sendReportNow(all = false) {
      const what = all ? `tutti i report attivi (${this.repEnabledCount})` : `il report "${this.repScopeLabel}"`;
      if (!confirm(`Inviare ora ${what} di ${this.repPeriodLabel} per email?`)) return;
      this.rep.busy = true; this.rep.msg = this.rep.err = '';
      try {
        const body = { period: this.rep.period }; if (!all) body.scope = this.rep.scope;
        const r = await this.api('/api/reports/send', { method: 'POST', body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { this.rep.err = d.detail || 'Invio non riuscito'; return; }
        this.rep.msg = d.message ? d.message : (`Report inviato (${d.filename})` + (d.pdf ? '' : ' — PDF non disponibile, allegato HTML'));
      } finally { this.rep.busy = false; }
    },
    async repScopeChanged() { await Promise.all([this.previewReport(), this.loadTrend()]); },
    get repScopeLabel() { const s = this.rep.scopes.find(x => x.key === this.rep.scope); return s ? s.label : 'Tutti i siti'; },
    get repPeriodLabel() { const p = this.rep.periods.find(x => x.period === this.rep.period); return p ? p.label : this.rep.period; },
    repTouch() { this.rep.dirty = true; this.previewReport(); },

    // ---------- 2FA ----------
    async load2fa() { const r = await this.api('/api/2fa/status'); if (r.ok) { const d = await r.json(); this.account.username = d.username || this.account.username; this.sec2fa.totp = !!d.totp_enabled; this.sec2fa.passkeys = d.passkeys || []; } },
    async totpSetup() { this.sec2fa.err = ''; const r = await this.api('/api/2fa/totp/setup'); if (r.ok) this.sec2fa.setup = await r.json(); else this.sec2fa.err = (await r.json().catch(() => ({}))).detail || 'Errore'; },
    async totpEnable() { this.sec2fa.err = ''; const r = await this.api('/api/2fa/totp/enable', { method: 'POST', body: JSON.stringify({ code: this.sec2fa.code }) }); if (!r.ok) { this.sec2fa.err = 'Codice non valido'; return; } this.sec2fa.setup = null; this.sec2fa.code = ''; this.needs2faSetup = false; this.sec2fa.msg = 'Authenticator attivato'; await this.load2fa(); },
    async totpDisable() { if (!confirm('Disattivare l\'authenticator?')) return; await this.api('/api/2fa/totp/disable', { method: 'POST' }); await this.load2fa(); },
    async passkeyRegister() {
      this.sec2fa.err = '';
      try {
        const r = await this.api('/api/webauthn/register/begin', { method: 'POST' });
        if (!r.ok) { this.sec2fa.err = (await r.json()).detail || 'Errore'; return; }
        const opts = await r.json(); opts.challenge = b64uToBuf(opts.challenge); opts.user.id = b64uToBuf(opts.user.id);
        if (opts.excludeCredentials) opts.excludeCredentials = opts.excludeCredentials.map(c => ({ ...c, id: b64uToBuf(c.id) }));
        const cred = await navigator.credentials.create({ publicKey: opts });
        const payload = { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type, response: { attestationObject: bufToB64u(cred.response.attestationObject), clientDataJSON: bufToB64u(cred.response.clientDataJSON) } };
        const label = prompt('Nome per questa passkey (es. iPhone, MacBook):', 'passkey') || 'passkey';
        const r2 = await this.api('/api/webauthn/register/complete', { method: 'POST', body: JSON.stringify({ credential: payload, label }) });
        if (!r2.ok) { this.sec2fa.err = (await r2.json()).detail || 'Registrazione fallita'; return; }
        this.needs2faSetup = false; this.sec2fa.msg = 'Passkey registrata'; await this.load2fa();
      } catch (e) { this.sec2fa.err = 'Registrazione annullata o non disponibile'; }
    },
    async passkeyDelete(id) { if (!confirm('Rimuovere questa passkey?')) return; await this.api('/api/webauthn/register/delete', { method: 'POST', body: JSON.stringify({ label: id }) }); await this.load2fa(); },

    // ---------- dashboard ----------
    get attention() {
      const out = [];
      for (const s of this.sites) if (this.isOff(s)) out.push({ kind: 'err', site: s, text: 'Offline: ' + (s.error || s.status) });
      for (const h of this.history.filter(x => !x.ok).slice(0, 8)) out.push({ kind: 'warn', site: this.sites.find(s => s.id === h.site_id), text: `Update fallito: ${h.name} (${h.error || 'errore'})`, at: h.at });
      if (this.sec.summary && (this.sec.summary.critical || this.sec.summary.exploited)) out.push({ kind: 'err', text: `${this.sec.summary.critical} vulnerabilità critiche, ${this.sec.summary.exploited} sfruttate attivamente`, link: 'security' });
      return out.slice(0, 12);
    },
    // ---- grafico "ultimi 7 giorni" in dashboard: stessa resa delle statistiche ----
    get dashScale() {
      const ds = (this.histSummary && this.histSummary.days) || [];
      const max = Math.max(1, ...ds.map(d => (d.ok || 0) + (d.failed || 0)));
      const pow = Math.pow(10, Math.floor(Math.log10(max)));
      let step = pow;
      for (const c of [0.1, 0.2, 0.5, 1, 2, 5, 10]) {
        const s = c * pow;
        if (s >= 1 && Math.ceil(max / s) <= 4) { step = s; break; }
      }
      step = Math.max(1, Math.round(step));
      const top = Math.ceil(max / step) * step;
      const ticks = [];
      for (let v = step; v <= top; v += step) ticks.push(v);
      return { top, ticks };
    },
    dashY(v) { return Math.round(Math.max(0, v || 0) / (this.dashScale.top || 1) * 130); },   // 130 = altezza in CSS
    dashDay(d) {
      const x = new Date(d.day + 'T12:00:00');
      const n = ['dom', 'lun', 'mar', 'mer', 'gio', 'ven', 'sab'][x.getDay()];
      return `${n} ${x.getDate()}`;
    },
    barH(day, key) {
      // altezza in PIXEL: le percentuali non si risolvono dentro un flex-item senza altezza esplicita
      // scala con un MINIMO di 10: un singolo update resta una barra bassa, la barra
      // piena compare solo con volumi reali (>=10 in un giorno). Oltre 10 scala sul massimo.
      const m = Math.max(10, ...(this.histSummary?.days || []).map(d => d.ok + d.failed));
      const v = day[key] || 0;
      return v ? Math.max(4, Math.round((v / m) * 100)) + 'px' : '0px';
    },
    dayLbl(d) { return new Date(d).toLocaleDateString(I18n.locale, { weekday: 'short' }); },
  };
}
