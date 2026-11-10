import type { ReactNode } from 'react';
import { fireEvent, render as renderTree, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@/i18n';
import type { ExpressionCaps, Profile } from '@/lib/api/types';
import { EmotionControls } from './emotion-controls';
import {
  cloneSettingsStore,
  DEFAULT_CLONE_SETTINGS,
  patchCloneSettings,
} from '@/lib/store/clone-settings';

const engine = vi.hoisted(() => ({
  id: 'indextts2',
  supportsEmotion: true,
  name: 'IndexTTS 2.5 (multilingual emotion-controlled cloning)',
  caps: undefined as ExpressionCaps | undefined,
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    activeTts: {
      id: engine.id,
      display_name: engine.name,
      supports_emotion: engine.supportsEmotion,
      expression_caps: engine.caps,
    },
  }),
}));
const voices = vi.hoisted(() => ({ data: [] as Profile[] }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => voices }));
function render(children: ReactNode) {
  return renderTree(
    <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>,
  );
}
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#models">{children}</a>,
}));

beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  cloneSettingsStore.setState(() => ({ ...DEFAULT_CLONE_SETTINGS }));
  engine.supportsEmotion = true;
  engine.name = 'IndexTTS 2.5 (multilingual emotion-controlled cloning)';
  engine.caps = undefined;
  engine.id = 'indextts2';
  voices.data = [
    { id: 'speaker', name: 'Speaker', ref_audio_path: 'speaker.wav' },
    { id: 'emotion', name: 'Emotional sample', locked_audio_path: 'emotion.wav' },
    { id: 'empty', name: 'Unrendered design' },
  ] as Profile[];
});

describe('engine-aware emotion entry (#3422)', () => {
  it('stays visible and explains why for engines without emotion', async () => {
    engine.supportsEmotion = false;
    engine.name = 'VoiceStudio (k2-fsa/OmniVoice, 600+ languages)';
    engine.caps = { emotion: 'none', tags: ['[laughter]', '[sigh]'] };
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(
      await screen.findByText(/Emotion isn't available with OmniVoice\. IndexTTS2/),
    ).toBeInTheDocument();
    expect(screen.getByText(/reactions such as \[laughter\] from Insert/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sliders' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument();
  });

  it('takes a written delivery direction where instruct steers the clone', async () => {
    engine.supportsEmotion = false;
    engine.name = 'CosyVoice 3 (9 langs, zero-shot, instruct, Apache-2.0)';
    engine.caps = { emotion: 'direction', tags: ['[breath]', '[laughter]'] };
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    const field = await screen.findByRole('textbox', { name: 'Delivery direction' });
    fireEvent.change(field, { target: { value: 'scared, then excited' } });
    // Its own draft, not the Style instruct another engine would also read.
    expect(cloneSettingsStore.state.direction).toBe('scared, then excited');
    expect(cloneSettingsStore.state.instruct).toBe('');
    expect(screen.getByText(/guidance for CosyVoice 3\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(cloneSettingsStore.state.direction).toBe('');
  });

  it('keeps the legacy gate for a backend without expression_caps', async () => {
    engine.supportsEmotion = false;
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByText(/Emotion isn't available/)).toBeInTheDocument();
  });
});

describe('IndexTTS2 clone emotion controls', () => {
  it.each(['indextts20', 'indextts2'])(
    'selects a saved audio reference in the portaled picker for %s without changing the speaker',
    async (id) => {
      engine.id = id;
      patchCloneSettings({ selectedProfileId: 'speaker' });
      render(<EmotionControls />);
      fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Audio reference' }));
      // The second Audio reference button is the real shared VoiceSelector trigger.
      fireEvent.click(screen.getAllByRole('button', { name: 'Audio reference' })[1]);
      const sample = await screen.findByRole('option', { name: 'Emotional sample' });
      expect(screen.queryByRole('option', { name: 'Unrendered design' })).toBeNull();
      expect(screen.getAllByRole('option')).toHaveLength(2);
      // Exercise the pointer/mouse path in the body portal, outside the Popover DOM.
      fireEvent.pointerDown(sample, { pointerType: 'mouse', button: 0 });
      fireEvent.mouseDown(sample, { button: 0 });
      fireEvent.mouseUp(sample, { button: 0 });
      fireEvent.click(sample);
      expect(cloneSettingsStore.state).toMatchObject({
        emotionMode: 'audio',
        emotionProfileId: 'emotion',
        selectedProfileId: 'speaker',
      });
      expect(document.querySelector('audio')).toHaveAttribute(
        'src',
        expect.stringContaining('/profiles/emotion/audio'),
      );
      expect(screen.getByRole('button', { name: 'Reset' })).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Off' }));
      expect(cloneSettingsStore.state.emotionMode).toBe('none');
      expect(document.querySelector('audio')).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Audio reference' }));
      expect(cloneSettingsStore.state.emotionProfileId).toBe('emotion');
      fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
      expect(cloneSettingsStore.state).toMatchObject({
        emotionMode: 'none',
        emotionProfileId: null,
        emotionAlpha: 0.6,
        selectedProfileId: 'speaker',
      });
    },
  );

  it('limits strength to 0.6 when switching from audio to text', async () => {
    patchCloneSettings({ emotionMode: 'audio', emotionAlpha: 1, emotionProfileId: 'emotion' });
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByText('1.00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Description' }));
    expect(cloneSettingsStore.state.emotionAlpha).toBe(0.6);
  });

  it('disables an empty audio picker and editing during generation', async () => {
    voices.data = [];
    patchCloneSettings({ emotionMode: 'audio' });
    render(<EmotionControls disabled />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    const buttons = await screen.findAllByRole('button', { name: 'Audio reference' });
    for (const button of buttons) expect(button).toBeDisabled();
  });

  it('does not offer saved audio to other engines even if they advertise emotion', async () => {
    engine.id = 'other';
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByRole('button', { name: 'Description' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Audio reference' })).toBeNull();
  });
  it('keeps the default take neutral and accepts a written emotion prompt', async () => {
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(cloneSettingsStore.state.emotionMode).toBe('none');

    fireEvent.click(await screen.findByRole('button', { name: 'Description' }));
    const textarea = screen.getByRole('textbox', { name: 'Emotion description' });
    fireEvent.change(textarea, { target: { value: 'whispering with sadness' } });
    expect(cloneSettingsStore.state.emotionMode).toBe('text');
    expect(cloneSettingsStore.state.emotionText).toBe('whispering with sadness');
    expect(screen.getByText('0.60')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Off' }));
    expect(cloneSettingsStore.state.emotionMode).toBe('none');
    // The text is a draft and is restored if Description is selected again.
    expect(cloneSettingsStore.state.emotionText).toBe('whispering with sadness');
  });

  it('offers eight correctly ordered vector sliders and an isolated reset', async () => {
    patchCloneSettings({
      emotionMode: 'vector',
      emotionVector: [0.4, 0, 0, 0, 0, 0.2, 0, 0],
      speed: 1.5,
    });
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByRole('button', { name: 'Sliders' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    // Base UI mounts slider groups before their measured thumbs in jsdom.
    // Assert the eight real controls plus labels; browser rendering supplies thumbs.
    expect(document.querySelectorAll('[data-slot="slider"]')).toHaveLength(8);
    for (const label of [
      'Happy',
      'Angry',
      'Sad',
      'Afraid',
      'Disgusted',
      'Melancholic',
      'Surprised',
      'Calm',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(cloneSettingsStore.state.emotionMode).toBe('none');
    expect(cloneSettingsStore.state.emotionVector).toEqual([0, 0, 0, 0, 0, 0, 0, 0]);
    expect(cloneSettingsStore.state.speed).toBe(1.5);
  });

  it('disables editing while a take is being synthesized', async () => {
    render(<EmotionControls disabled />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByRole('button', { name: 'Description' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sliders' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset' })).toBeDisabled();
  });
});
