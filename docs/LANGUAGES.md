# Languages

The interface supports **English, Italian, French and German**. The 🌐 selector is available on all included application pages. `Auto · Browser` checks `navigator.languages` in order, accepting regional forms such as `fr-CA` and `de-AT`; unsupported preferences fall back to English. An explicit selection is saved per application in local storage. The selector still works when local storage is unavailable, but the choice cannot persist.

Translations are local files. No translation API, cloud account or external request is needed for the interface. Language changes update visible labels, tooltips, placeholders, native dialogs and application messages, including content inserted after page load. The HTML `lang` attribute is updated for accessibility. Dates rendered afterward use the selected locale; existing dates update when their view refreshes.

Application-owned source text is used as the catalog key. User-editable fields, code, user chat messages and marked user-content areas are excluded. Product descriptions, meeting notes, third-party errors, vulnerability advisories and user-created templates retain their original content; UI translation is not a translation service for those records. Built-in outgoing templates use `DEFAULT_UI_LANGUAGE` where documented in the application README, independently of a visitor's browser. Custom saved templates keep the language chosen by their author.

## Maintain translations

Edit matching entries in `it.json`, `en.json`, `fr.json` and `de.json` in the application's `i18n` directory. Preserve placeholders such as `{0}` exactly. Run:

```sh
node scripts/build-i18n.cjs
node --test tests/i18n.test.cjs
```

Commit the JSON files and regenerated `catalog.js` together. Rebuild the container and hard-refresh the browser after changing assets. Text added to the application needs a matching catalog entry; unknown text remains unchanged. Add `translate="no"` or `data-i18n-skip` to new user-content containers. The translation layer uses text nodes and attributes; it does not replace HTML markup.
