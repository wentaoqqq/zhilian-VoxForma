import {
  VideoIcon,
  AudioLinesIcon,
  BrainCircuitIcon,
  KeyboardIcon,
  LanguagesIcon,
  MicIcon,
  UsersRoundIcon,
  type LucideIcon,
} from 'lucide-react';

export const modelFamilies = [
  'video',
  'tts',
  'asr',
  'dictation',
  'diarisation',
  'translation',
  'llm',
] as const;

export type ModelFamily = (typeof modelFamilies)[number];

export const familyIcons: Record<ModelFamily, LucideIcon> = {
  video: VideoIcon,
  tts: AudioLinesIcon,
  asr: MicIcon,
  dictation: KeyboardIcon,
  diarisation: UsersRoundIcon,
  translation: LanguagesIcon,
  llm: BrainCircuitIcon,
};
