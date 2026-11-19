/** UUIDs for browser features, including LAN deployments over plain HTTP. */
export function randomUUID(): string {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === 'function') return webCrypto.randomUUID();
  if (typeof webCrypto?.getRandomValues !== 'function') {
    throw new Error('Secure random number generation is unavailable in this browser.');
  }
  return secureUUID(webCrypto);
}

function secureUUID(webCrypto: Crypto): ReturnType<Crypto['randomUUID']> {
  const bytes = webCrypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Install the fallback for callers of the native API on plain HTTP. */
export function installRandomUUID() {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === 'function' || typeof webCrypto?.getRandomValues !== 'function')
    return;
  Object.defineProperty(webCrypto, 'randomUUID', {
    configurable: true,
    writable: true,
    value: () => secureUUID(webCrypto),
  });
}

installRandomUUID();
