import { afterEach, describe, expect, it, vi } from 'vitest';
import { randomUUID } from './random-uuid';

afterEach(() => vi.unstubAllGlobals());

describe('browser UUID compatibility', () => {
  it('uses native randomUUID with the correct receiver when available', () => {
    const native = { randomUUID() { expect(this).toBe(native); return 'native-uuid'; } };
    vi.stubGlobal('crypto', native);
    expect(randomUUID()).toBe('native-uuid');
  });
  it('works with the HTTP subset and sets UUID version and variant bits', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => { bytes.fill(255); return bytes; });
    vi.stubGlobal('crypto', { getRandomValues });
    expect(randomUUID()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff');
    expect(getRandomValues).toHaveBeenCalledOnce();
  });
  it('does not reuse entropy between calls', () => {
    let call = 0;
    vi.stubGlobal('crypto', { getRandomValues(bytes: Uint8Array) { bytes.fill(++call); return bytes; } });
    expect(randomUUID()).not.toBe(randomUUID());
  });
  it('never substitutes Math.random for cryptographic entropy', () => {
    vi.stubGlobal('crypto', undefined);
    expect(() => randomUUID()).toThrow('Secure random number generation is unavailable');
  });
});
