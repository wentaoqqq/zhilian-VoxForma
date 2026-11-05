import { describe, expect, it } from 'vitest';
import { withProductTerminology } from './product-terminology';

describe('Chinese product terminology', () => {
  it('applies without mutating the original language bundle or unrelated labels', () => {
    const source = { clone: { title: '语音克隆', untouched: '保留' }, nav: { clone_short: '克隆' } };
    const first = withProductTerminology('zh-CN', source);
    expect(first).toMatchObject({ clone: { title: '文字转语音', untouched: '保留' }, nav: { clone_short: '文字转语音' } });
    expect(source.clone.title).toBe('语音克隆');
    expect(withProductTerminology('zh-CN', first)).toEqual(first);
  });
  it('keeps other languages intact and does not recreate a removed upstream key', () => {
    const source = { nav: { voice: 'Voice' } };
    expect(withProductTerminology('en', source)).toBe(source);
    expect(withProductTerminology('fr', source)).toBe(source);
    expect(withProductTerminology('zh-CN', source)).not.toHaveProperty('clone');
  });
  it('retains interpolation variables and handles a fresh Traditional Chinese bundle', () => {
    const result = withProductTerminology('zh-TW', { clone: { using_profile: '使用 {{name}}', output_meta: '{{duration}} / {{gen}}' } });
    expect(result).toEqual({ clone: { using_profile: '目前音色：{{name}}', output_meta: '音訊 {{duration}} 秒 · 產生耗時 {{gen}} 秒' } });
  });
});
