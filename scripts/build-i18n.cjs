const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const folder = path.resolve(__dirname, '..', 'api/static/i18n');
const langs = ['it', 'en', 'fr', 'de'];
const dictionaries = Object.fromEntries(langs.map(lang => [lang, JSON.parse(fs.readFileSync(path.join(folder, lang + '.json'), 'utf8'))]));
const keys = Object.keys(dictionaries.it);
const slots = value => [...value.matchAll(/\{\d+\}/g)].map(match => match[0]).sort();
for (const lang of langs) {
  assert.deepEqual(Object.keys(dictionaries[lang]).sort(), [...keys].sort(), lang + ': missing or extra keys');
  for (const key of keys) {
    const value = dictionaries[lang][key];
    assert.equal(typeof value, 'string', key);
    assert(value.trim(), lang + ': empty value for ' + key);
    assert.deepEqual(slots(value), slots(key), lang + ': placeholders for ' + key);
  }
}
const catalog = Object.fromEntries(keys.map(key => [key, Object.fromEntries(langs.filter(lang => lang !== 'it').map(lang => [lang, dictionaries[lang][key]]))]));
fs.writeFileSync(path.join(folder, 'catalog.js'), 'window.APP_TRANSLATIONS = ' + JSON.stringify(catalog) + ';\n');
console.log('Built ' + keys.length + ' translation entries in four languages.');
