const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const folder = path.resolve(__dirname, '..', 'api/static/i18n');
const dictionaries = Object.fromEntries(['it','en','fr','de'].map(lang => [lang, JSON.parse(fs.readFileSync(path.join(folder, lang + '.json'), 'utf8'))]));
const catalog = Object.fromEntries(Object.keys(dictionaries.it).map(key => [key, Object.fromEntries(['en','fr','de'].map(lang => [lang,dictionaries[lang][key]]))]));
const engine = require(path.join(folder,'i18n.js'))(catalog);
test('catalogs are complete and preserve every placeholder', () => {
  const slots = value => [...value.matchAll(/\{\d+\}/g)].map(match => match[0]).sort();
  for (const [lang, entries] of Object.entries(dictionaries)) {
    assert.deepEqual(Object.keys(entries).sort(), Object.keys(dictionaries.it).sort());
    for (const [key,value] of Object.entries(entries)) {
      assert.equal(typeof value,'string'); assert(value.trim(), key);
      assert.deepEqual(slots(key),slots(value),lang + ': ' + key);
    }
  }
});
test('browser bundle matches editable catalogs', () => {
  const context = {window:{}};
  vm.runInNewContext(fs.readFileSync(path.join(folder,'catalog.js'),'utf8'),context);
  assert.equal(JSON.stringify(context.window.APP_TRANSLATIONS),JSON.stringify(catalog));
});
test('detects supported regional languages in preference order', () => {
  assert.equal(engine.detect(['es-ES','fr-CA','de']), 'fr');
  assert.equal(engine.detect(['de-AT']), 'de');
  assert.equal(engine.detect(['IT_it']), 'it');
  assert.equal(engine.detect(['ja-JP']), 'en');
  assert.equal(engine.detect([]), 'en');
});
test('translates labels, whitespace and dynamic placeholders', () => {
  assert.equal(engine.t('Salva','de'), 'Speichern');
  assert.equal(engine.t(' Salva ','fr'), ' Enregistrer ');
  assert.equal(engine.t('Microfono 42','fr'), 'Microphone 42');
  assert.equal(engine.t('Salva','it'), 'Salva');
  assert.equal(engine.t('Salva','xx'), 'Salva');
});
test('preserves URLs and unknown content', () => {
  assert.equal(engine.t('https://example.com/Salva','de'), 'https://example.com/Salva');
  assert.equal(engine.t('person@example.com','fr'), 'person@example.com');
  assert.equal(engine.t('A user supplied arbitrary sentence XYZ','de'), 'A user supplied arbitrary sentence XYZ');
});
