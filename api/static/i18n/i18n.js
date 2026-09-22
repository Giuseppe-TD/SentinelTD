/* Local UI translation. No network translation service is used. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.I18n = factory(root.APP_TRANSLATIONS || {}, root);
})(typeof window !== 'undefined' ? window : globalThis, function (catalog, win) {
  'use strict';
  const languages = ['en', 'it', 'fr', 'de'];
  const normalize = value => String(value).replace(/\s+/g, ' ').trim();
  const escapeRE = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const detect = values => {
    for (const value of values || []) {
      const lang = String(value).toLowerCase().split(/[-_]/)[0];
      if (languages.includes(lang)) return lang;
    }
    return 'en';
  };
  let preference = 'auto';
  const storageKey = 'ui-language:' + (win?.document.documentElement.dataset.project || 'app');
  try { preference = win?.localStorage.getItem(storageKey) || 'auto'; } catch (_) {}
  if (!['auto', ...languages].includes(preference)) preference = 'auto';
  const browserLanguage = () => detect(win?.navigator.languages || [win?.navigator.language]);
  let locale = preference === 'auto' ? browserLanguage() : preference;
  const reverse = new Map();
  for (const [source, translations] of Object.entries(catalog)) {
    for (const text of Object.values(translations)) if (!reverse.has(text)) reverse.set(text,source);
  }
  const templates = Object.keys(catalog).filter(key => /\{\d+\}/.test(key)).map(key => {
    const ids = [...key.matchAll(/\{(\d+)\}/g)].map(m => m[1]);
    const expression = escapeRE(key).replace(/\\\{\d+\\\}/g, '(.+?)');
    return {key, ids, re: new RegExp('^' + expression + '$')};
  }).sort((a,b) => b.key.length-a.key.length);
  // Concatenated labels (e.g. counts followed by "sites") are translated at
  // boundaries only. URLs, input values and user-content regions are untouched.
  const boundaries = Object.keys(catalog).filter(key => !/[{}<>]/.test(key) && key.length > 2)
    .sort((a,b) => b.length-a.length);
  function translate(value, language=locale, depth=0) {
    if (typeof value !== 'string' || !languages.includes(language)) return value;
    const rawKey = normalize(value);
    const key = catalog[rawKey] ? rawKey : (reverse.get(rawKey) || rawKey);
    if (!key || /^(?:https?:\/\/|\w+@\S+\.)/.test(key)) return value;
    if (language === 'it') return key===rawKey ? value : value.replace(rawKey,key);
    let result = catalog[key]?.[language];
    if (result === undefined) {
      for (const item of templates) {
        const match = item.re.exec(key);
        if (!match) continue;
        const translation = catalog[item.key]?.[language];
        if (!translation) continue;
        result = translation.replace(/\{(\d+)\}/g, (all, id) => match[item.ids.indexOf(id)+1] ?? all);
        break;
      }
    }
    if (result === undefined && depth < 3) {
      // Numbers and punctuation around a standalone label.
      const decorated = key.match(/^([^\p{L}]*)(.+?)([^\p{L}]*)$/u);
      if (decorated && catalog[decorated[2]]?.[language])
        result = decorated[1] + catalog[decorated[2]][language] + decorated[3];
      if (result === undefined) for (const part of boundaries) {
        if (key.startsWith(part + ' ')) {
          result = catalog[part][language] + ' ' + translate(key.slice(part.length+1), language, depth+1); break;
        }
        if (key.endsWith(' ' + part)) {
          result = translate(key.slice(0,-part.length-1),language,depth+1) + ' ' + catalog[part][language]; break;
        }
      }
    }
    if (result === undefined) return value;
    return (value.match(/^\s*/)?.[0] || '') + result + (value.match(/\s*$/)?.[0] || '');
  }
  const api = {t:translate, detect, get locale(){return locale;}, get preference(){return preference;}, setLanguage};
  if (!win) return api;
  const document = win.document;
  const textState = new WeakMap(), attrState = new WeakMap();
  const attrs = ['title','placeholder','aria-label','alt','data-tip'];
  const skip = 'script,style,code,pre,textarea,[contenteditable], [translate="no"],[data-i18n-skip],#language-switcher,.chat-msg-text,.chat-msg-name,.chat-message-text,.chat-body,.msg-text,.msg-bubble,.msg-author,.avatar-name,.peer-name,.tile-name,.meeting-title,[data-user-name],#cdTitle,#roomIdDisplay,#pjRoomCode';
  function ignored(element) {
    if (!element || element.closest(skip)) return true;
    const binding = element.closest('[x-text]')?.getAttribute('x-text') || '';
    return !/['"]/.test(binding) && /(?:\.(?:name|title|url|email|username|full_name|gtin|description|notes|brand|site_name|ext_name|from|to|value)|^user_email$)/.test(binding);
  }
  function renderNode(node) {
    if (node.nodeType === 3) {
      if (ignored(node.parentElement)) return;
      const before = node.nodeValue;
      const state = textState.get(node);
      const source = state && before === state.rendered ? state.source : before;
      const login = source.trim() === 'Entra' && node.parentElement?.closest('.login,.login-shell,.auth-wrap');
      const rendered = login ? source.replace('Entra', {it:'Entra',en:'Sign in',fr:'Se connecter',de:'Anmelden'}[locale]) : translate(source);
      textState.set(node,{source,rendered});
      if (before !== rendered) node.nodeValue = rendered;
    } else if (node.nodeType === 1 && !ignored(node)) {
      const state = attrState.get(node) || {};
      for (const attr of attrs) if (node.hasAttribute(attr)) {
        const before = node.getAttribute(attr), old = state[attr];
        const source = old && before === old.rendered ? old.source : before;
        const rendered = translate(source);
        state[attr] = {source,rendered};
        if (rendered !== before) node.setAttribute(attr,rendered);
      }
      attrState.set(node,state);
    }
  }
  function render(tree=document.documentElement) {
    renderNode(tree);
    const walker = document.createTreeWalker(tree, 1|4, {acceptNode(node) {
      if (node.nodeType === 1 && ignored(node)) return 2;
      return 1;
    }});
    while (walker.nextNode()) renderNode(walker.currentNode);
  }
  let observer;
  function observe() { observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attrs}); }
  function setLanguage(value) {
    if (!['auto', ...languages].includes(value)) return;
    preference = value;
    locale = value === 'auto' ? browserLanguage() : value;
    if (!win) return;
    try { win.localStorage.setItem(storageKey,value); } catch (_) {}
    document.documentElement.lang = locale;
    if (observer) observer.disconnect();
    render();
    const select = document.getElementById('language-select');
    if (select) { select.value=value; select.setAttribute('aria-label', {en:'Language',it:'Lingua',fr:'Langue',de:'Sprache'}[locale]); }
    if (observer) observe();
    win.dispatchEvent(new CustomEvent('languagechange',{detail:{language:locale,preference}}));
  }
  // Native dialogs do not create DOM nodes, so translate their message explicitly.
  for (const name of ['alert','confirm','prompt']) {
    const original=win[name].bind(win);
    win[name]=function(message,...args){ return original(translate(message),...args); };
  }
  document.documentElement.lang=locale;
  function boot() {
    const bar=document.createElement('div'); bar.id='language-switcher'; bar.setAttribute('translate','no');
    const label=document.createElement('label'); label.htmlFor='language-select'; label.className='language-switcher__icon'; label.textContent='🌐';
    const select=document.createElement('select'); select.id='language-select';
    for(const [value,text] of [['auto','Auto · Browser'],['it','Italiano'],['en','English'],['fr','Français'],['de','Deutsch']]) {
      const option=document.createElement('option'); option.value=value; option.textContent=text; select.append(option);
    }
    select.addEventListener('change',()=>setLanguage(select.value));
    bar.append(label,select);
    // Sentinel has a real application topbar: keep the language control inside it
    // instead of floating it over the interface buttons.
    // NB: the topbar does not exist yet at DOMContentLoaded (the whole app lives inside
    // an Alpine template rendered later), so placement is re-evaluated on every DOM
    // change: on login the control moves into the topbar, on logout it floats again.
    const project=document.documentElement.dataset.project || '';
    function place() {
      const host=project==='panopticon-lite' ? document.querySelector('.topbar') : null;
      if(host){ if(bar.parentElement!==host){ bar.classList.add('language-switcher--inline'); host.append(bar); } }
      else if(bar.parentElement!==document.body){ bar.classList.remove('language-switcher--inline'); document.body.append(bar); }
    }
    place();
    observer=new MutationObserver(records=>{
      observer.disconnect();
      for(const record of records) {
        if(record.type==='childList') for(const node of record.addedNodes) render(node);
        else renderNode(record.target);
      }
      place();
      observe();
    });
    setLanguage(preference);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true}); else boot();
  win.addEventListener('languagechange',event=>{ if(!event.detail && preference==='auto')setLanguage('auto'); });
  return api;
});
