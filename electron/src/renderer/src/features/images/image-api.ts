import { apiJson } from '@/lib/api/client';

export const imageModelsKey = ['image-models'] as const;
export const imageJobsKey = ['image-jobs'] as const;
export const imageInstallKey = ['image-install'] as const;

export type ImageModels = {
  supported: boolean;
  installed: boolean;
  models: Array<{ id: string; label: string }>;
  weight_bytes: number;
  max_references?: number;
};

export type ImageInstall = ImageModels & {
  job: null | {
    state: 'running' | 'done' | 'failed';
    step: 'runtime' | 'weights' | null;
    filename: string | null;
    received_bytes?: number;
    file_bytes?: number;
    error: string | null;
  };
};

export type ImageMode = 'text-to-image' | 'image-to-image' | 'edit';

export type ImageJob = {
  id: string;
  prompt: string;
  model: string;
  size: string;
  mode?: ImageMode;
  created_at: number;
  state: 'queued' | 'waiting' | 'starting' | 'generating' | 'done' | 'failed' | 'interrupted';
  error: string | null;
};

export const loadImageModels = () => apiJson<ImageModels>('/image-generation/models');
export const loadImageJobs = () => apiJson<{ jobs: ImageJob[] }>('/image-generation/jobs');
export const loadImageInstall = () => apiJson<ImageInstall>('/image-generation/install');
export const currentImageJob = (job: ImageJob) =>
  ['queued', 'waiting', 'starting', 'generating'].includes(job.state);
