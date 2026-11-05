/** Chinese product vocabulary; original translation keys and resources stay intact. */
import terms from './product-terminology.json';

type Messages = Record<string, unknown>;

export function withProductTerminology(locale: string, resources: Messages): Messages {
  if (locale !== 'zh-CN' && locale !== 'zh-TW') return resources;
  const result = { ...resources };
  for (const entry of terms.entries) {
    const parts = entry.key.split('.');
    let original: unknown = resources;
    for (const part of parts) {
      if (!original || typeof original !== 'object') { original = undefined; break; }
      original = (original as Messages)[part];
    }
    // A removed upstream key is not recreated. The update checker reports drift.
    if (typeof original !== 'string') continue;
    let target = result;
    for (const part of parts.slice(0, -1)) {
      target[part] = { ...(target[part] as Messages) };
      target = target[part] as Messages;
    }
    target[parts[parts.length - 1]] = entry[locale];
  }
  return result;
}
