import { useEffect, useRef, useState } from 'react';
import { CharacterPicker } from './character-picker';
import { useCharacters, useCharacterSelection } from './characters-api';
import { DigitalHumanIcon } from '@/components/icons/digital-human-icon';
import { useTranslation } from 'react-i18next';
// @ts-expect-error Shared legacy JSX control has no declaration file.
import SearchableSelect from '@shared/components/SearchableSelect';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { toGenerateForm } from '@/lib/api/generate';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { EngineNotice } from '@/components/engine-notice';
import { useEngines } from '@/hooks/use-engines';
import { useProfiles } from '@/hooks/use-profiles';
import { useCloneReadiness } from '@/hooks/use-clone-readiness';
import { useReferenceTranscript } from '@/hooks/use-reference-transcript';
import { isPresetOnly } from '@/hooks/use-tts-readiness';
import { setCloneSetting, useCloneSettings } from '@/lib/store/clone-settings';
import { useReference } from '@/lib/store/reference';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';
import { effectiveSamplingSteps } from '@/lib/audio/quality';
import { engineTrimsReference } from '@/lib/reference-usage';
import { expressionCaps } from '@/lib/expression';
import { manualProfileTranscript } from '@/hooks/use-generate';
import { VoiceSetup } from '@/features/clone/voice-setup';
import { ReferencePanel } from '@/features/clone/reference-panel';
import { EditProfile } from '@/features/clone/edit-profile';
import { ScriptPanel } from '@/features/clone/script-panel';
import { VoiceControls } from '@/features/clone/action-bar';
import { QualityControls } from '@/features/clone/quality-controls';
import { EmotionControls } from '@/features/clone/emotion-controls';
import { EngineLanguagePicker } from '@/features/clone/engine-language-picker';
import { PresetVoicePicker } from '@/features/clone/preset-voice-picker';

type Media = { id: string; name: string };
type Job = { id: string; status: string; stage?: string; message?: string; error?: string; audio_output?: string; output?: string; video?: string; reference?: string; text?: string; fps?: number; mode?: string; character_name?: string };
type Jobs = { jobs: Job[]; queue: { running: number; waiting: number; capacity: number; full: boolean } };
const prefix = '/api/avatar/';
const requestId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

function Upload({id, kind, busy, onFile}: {id: string; kind: string; busy: boolean; onFile: (file: File) => void}) {
  const {t}=useTranslation();
  const input=useRef<HTMLInputElement>(null);
  return <div className="flex flex-wrap items-center gap-2">
    <input ref={input} id={id} className="sr-only" aria-hidden="true" tabIndex={-1} type="file" accept={kind==='Video'?'.mp4,.mov,.mkv':'.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac,.webm'} disabled={busy}
      onChange={e=>{const f=e.currentTarget.files?.[0];e.currentTarget.value='';if(f)onFile(f);}} />
    <Button type="button" variant="outline" disabled={busy} onClick={()=>input.current?.click()}>{t('gallery.upload')+' · '+t(kind==='Video'?'avatar.video':'avatar.audio')}</Button>
    <span className="text-xs text-muted-foreground">{'≤ 2 GiB'}</span>
  </div>;
}

export function AvatarPage() {
  const {t}=useTranslation();
  const settings=useCloneSettings();
  const reference=useReference();
  const profiles=useProfiles();
  const {activeTts}=useEngines();
  const blocker=useCloneReadiness();
  const {editingProfileId}=useWorkspace();
  const selectedVoice=profiles.data?.find(p=>p.id===settings.selectedProfileId);
  const editingVoice=profiles.data?.find(p=>p.id===editingProfileId);
  const presetOnly=isPresetOnly(activeTts);
  const transcription=useReferenceTranscript(settings.selectedProfileId?null:reference.file,{skip:engineTrimsReference(activeTts,reference.durationSeconds)});
  const [choosingVoice,setChoosingVoice]=useState(false);
  const [files,setFiles]=useState<{videos:Media[];audios:Media[]}>({videos:[],audios:[]});
  const [jobs,setJobs]=useState<Jobs>({jobs:[],queue:{running:0,waiting:0,capacity:16,full:false}});
  const [characterId,setCharacterId]=useCharacterSelection();
  const characters=useCharacters();
  const selectedCharacter=characters.data?.find(c=>c.id===characterId);
  const video=selectedCharacter?.available?selectedCharacter.id:'';
  const [audio,setAudio]=useState('');
  const [mode,setMode]=useState('tts');
  const [emotion,setEmotion]=useState('');
  const [emotionEnabled,setEmotionEnabled]=useState(false);
  const [strength,setStrength]=useState(1);
  const [fps,setFps]=useState(25);
  const [busy,setBusy]=useState(false);
  const [uploading,setUploading]=useState('');
  const [error,setError]=useState('');
  const [networkError,setNetworkError]=useState('');
  const [lastId,setLastId]=useState('');
  const [visibleJobs,setVisibleJobs]=useState(8);
  const [retryRequest,setRetryRequest]=useState<{id:string;body:object}|null>(null);
  async function refreshFiles(){setFiles(await apiJson<typeof files>(prefix+'files'));}
  async function refreshJobs(){setJobs(await apiJson<Jobs>(prefix+'jobs'));setNetworkError('');}
  useEffect(()=>{let alive=true;void refreshFiles().catch(e=>alive&&setNetworkError(describeError(e)));const poll=()=>{if(alive)void refreshJobs().catch(e=>alive&&setNetworkError(describeError(e)));};poll();const timer=setInterval(poll,2500);return()=>{alive=false;clearInterval(timer);};},[]);
  useEffect(()=>{if(activeTts?.id!=='indextts2')setEmotionEnabled(false);},[activeTts?.id]);
  useEffect(()=>{if(selectedVoice||reference.file)setChoosingVoice(false);},[selectedVoice?.id,reference.file]);
  const media=(id:string)=>apiPath(prefix+'media?id='+encodeURIComponent(id)+'&preview=1');
  const options=(items:Media[])=>items.map(f=>({value:f.id,label:f.name}));
  async function upload(file:File,kind:string,choose?:(id:string)=>void){
    if(!file.size||file.size>2*1024**3)throw new Error(t('common.error')+' · 0–2 GiB');
    setUploading(file.name+' · '+(file.size/1024**2).toFixed(1)+' MiB');
    try{
      const result=await apiJson<Media>(prefix+'upload?kind='+kind+'&name='+encodeURIComponent(file.name),{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}});
      choose?.(result.id);await refreshFiles();return result.id;
    }finally{setUploading('');}
  }
  async function uploadAndChoose(file:File,kind:string,choose:(id:string)=>void){setBusy(true);setError('');try{await upload(file,kind,choose);}catch(e){setError(describeError(e));}finally{setBusy(false);}}
  async function send(body:object,id:string){
    setBusy(true);setError('');
    try{const result=await apiJson<{id:string}>(prefix+'jobs',{method:'POST',body:JSON.stringify({...body,request_id:id})});setLastId(result.id);setRetryRequest(null);await refreshJobs();}
    catch(e){setError(describeError(e));setRetryRequest({id,body});}finally{setBusy(false);}
  }
  async function submit(audioOnly:boolean){
    // Freeze the same native controls and serialize with VoiceStudio's own
    // multipart builder. The server queues this snapshot, not mutable prefs.
    if(mode==='audio'){void send({mode,character_id:characterId,audio,fps},requestId());return;}
    if(blocker||!activeTts)return;
    const engine=activeTts;
    const caps=expressionCaps(engine);
    const rawRef=presetOnly||settings.selectedProfileId?null:reference.file;
    const useEmotion=emotionEnabled&&engine.id==='indextts2';
    const form=toGenerateForm({
      ...settings,steps:effectiveSamplingSteps(settings.steps,engine.id),
      profileId:presetOnly?null:settings.selectedProfileId,
      refAudio:rawRef,
      refText:presetOnly?undefined:settings.selectedProfileId?manualProfileTranscript(settings.refText,selectedVoice?.ref_text):settings.refText,
      instruct:[presetOnly&&engine.supports_voice_design===false?'':settings.instruct,caps.emotion==='direction'?settings.direction.trim():''].filter(Boolean).join(', '),
      instructVocabulary:engine.instruct_vocabulary??'tags',
      presetVoice:engine.preset_voices?.includes(settings.presetVoice)?settings.presetVoice:undefined,
      ...(!useEmotion&&engine.supports_emotion?(settings.emotionMode==='vector'?{emoVector:[...settings.emotionVector]}:settings.emotionMode==='text'&&settings.emotionText.trim()?{emoText:settings.emotionText.trim(),emoAlpha:settings.emotionAlpha}:{}):{}),
    });
    const nativeForm:Record<string,string>={};for(const [key,value] of form.entries())if(typeof value==='string'&&key!=='text'&&key!=='profile_id')nativeForm[key]=value;
    const snapshot={mode,character_id:characterId,fps,text:settings.text,voice_profile:presetOnly?undefined:settings.selectedProfileId||undefined,tts_engine:engine.id,native_form:nativeForm,audio_only:audioOnly,
      emotion_enabled:useEmotion,emotion:useEmotion?emotion:'',emotion_weight:strength};
    setBusy(true);setError('');
    try{const referenceId=rawRef?await upload(rawRef,'audio'):undefined;await send({...snapshot,reference:referenceId},requestId());}
    catch(e){setError(describeError(e));}finally{setBusy(false);}
  }
  const pickerClass='w-full rounded-md border border-border bg-background px-3 py-2 text-left text-sm';
  const fieldClass='grid content-start gap-2';
  const previewClass='w-full rounded-lg border border-border bg-black';
  const disabled=busy||jobs.queue.full;
  return <div className="flex h-full min-h-0 flex-col">
    <WorkspaceHeader><h1 className="flex items-center gap-2"><DigitalHumanIcon className="size-4"/>{t('avatar.title')}</h1><Button variant="ghost" size="sm" onClick={()=>void refreshFiles().catch(e=>setError(describeError(e)))}>{t('common.refresh')}</Button></WorkspaceHeader>
    <main className="flex-1 overflow-auto p-5" data-testid="avatar-page">
      <div className="mx-auto grid max-w-6xl gap-6 xl:grid-cols-2">
        <section className="grid content-start gap-5 rounded-xl border border-border p-5">
          <CharacterPicker value={characterId} onChange={setCharacterId} disabled={busy}/>
          <div className={fieldClass}><span>{t('avatar.source')}</span><SearchableSelect value={mode} onChange={setMode} options={[{value:'tts',label:t('avatar.ttsSource')},{value:'audio',label:t('avatar.recordedAudio')}]} ariaLabel={t('avatar.source')} menuPortal buttonClassName={pickerClass}/></div>
          {mode==='audio'?<div className={fieldClass}>
            <Upload id="avatar-audio-upload" kind="Audio" busy={busy} onFile={f=>void uploadAndChoose(f,'audio',setAudio)}/>
            <SearchableSelect value={audio} onChange={setAudio} options={options(files.audios)} placeholder={t('avatar.serverAudio')} ariaLabel={t('avatar.audio')} menuPortal buttonClassName={pickerClass}/>
            {audio&&<audio key={audio} src={media(audio)} controls className="w-full"/>}
          </div>:<div className="grid gap-4" data-testid="avatar-native-tts">
            <div className="text-sm">{t('avatar.engine')} · {activeTts?.display_name??activeTts?.id??'…'}</div>
            <EngineNotice operation="clone"/>
            {editingVoice?<EditProfile key={editingVoice.id} profile={editingVoice} onDone={()=>setWorkspace({editingProfileId:null})}/>:!presetOnly&&(choosingVoice||(!selectedVoice&&!reference.file))?<VoiceSetup onChosen={()=>setChoosingVoice(false)} onBack={()=>setChoosingVoice(false)}/>:!presetOnly?<>
              <Button variant="outline" onClick={()=>setChoosingVoice(true)}>{t('cloneFlow.change_voice')} · {selectedVoice?.name??reference.file?.name}</Button>
              <ReferencePanel transcription={transcription}/>
            </>:<PresetVoicePicker disabled={busy}/>}
            <ScriptPanel voiceName={selectedVoice?.name}/>
            <div className="flex flex-wrap items-center gap-2"><EngineLanguagePicker value={settings.language} onValueChange={v=>setCloneSetting('language',v)}/><QualityControls disabled={busy}/><VoiceControls/><EmotionControls disabled={busy||emotionEnabled}/></div>
            {activeTts?.id==='indextts2'&&<details className="grid gap-2"><summary className="cursor-pointer text-sm text-muted-foreground">{t('avatar.emotion')} · {t('avatar.audio')}</summary>
              <label className="my-3 flex items-center gap-2"><input id="avatar-emotion-enabled" type="checkbox" checked={emotionEnabled} onChange={e=>setEmotionEnabled(e.target.checked)}/>{t('avatar.emotionEnabled')}</label>
              {emotionEnabled&&<fieldset className={fieldClass}>
                <p className="text-xs text-muted-foreground">{t('avatar.emotionOverride')}</p>
                <Upload id="avatar-emotion-upload" kind="Audio" busy={busy} onFile={f=>void uploadAndChoose(f,'emotion',setEmotion)}/>
                <SearchableSelect value={emotion} onChange={setEmotion} options={options(files.audios)} ariaLabel={t('avatar.emotion')} menuPortal buttonClassName={pickerClass}/>
                {emotion&&<audio key={emotion} src={media(emotion)} controls className="w-full"/>}
                <label>{t('avatar.strength')}<input id="avatar-emotion-strength" type="number" min={0} max={1} step={0.1} value={strength} onChange={e=>setStrength(Number(e.target.value))} className={pickerClass}/></label>
              </fieldset>}
            </details>}
          </div>}
          <div className={fieldClass}><span>{t('avatar.fps')}</span><SearchableSelect value={String(fps)} onChange={(v:string)=>setFps(Number(v))} options={['25','60']} ariaLabel={t('avatar.fps')} menuPortal buttonClassName={pickerClass}/></div>
          {uploading&&<p role="status" className="break-all text-sm">{t('common.loading')} · {uploading}</p>}
          <div className="flex flex-wrap gap-3">
            <Button id="avatar-generate" disabled={disabled||!video||(mode==='audio'?!audio:!!blocker)||(mode==='tts'&&emotionEnabled&&!emotion)} onClick={()=>void submit(false)}>{t('avatar.generateVideo')}</Button>
            {mode==='tts'&&<Button id="avatar-audio-only" variant="outline" disabled={disabled||!!blocker||(emotionEnabled&&!emotion)} onClick={()=>void submit(true)}>{t('avatar.audioOnly')}</Button>}
          </div>
          {(error||networkError)&&<div role="alert" className="break-words text-sm text-destructive">{error||networkError}{error&&retryRequest&&<Button variant="outline" onClick={()=>void send(retryRequest.body,retryRequest.id)} disabled={busy}>{t('common.retry')}</Button>}</div>}
          {lastId&&<p className="break-all text-xs text-muted-foreground">{lastId}</p>}
        </section>
        <section className="grid content-start gap-4">
          <div className="rounded-xl border border-border p-4"><h2 className="font-medium">{t('avatar.queue')}</h2><p className="text-sm text-muted-foreground">{jobs.queue.running} / {jobs.queue.waiting} / {jobs.queue.capacity}</p></div>
          {jobs.jobs.slice(0, visibleJobs).map(job => <article key={job.id} data-job-id={job.id} className="grid gap-3 rounded-xl border border-border p-4">
            <div className="flex justify-between gap-3"><code className="break-all text-xs">{job.id}</code><span className="text-xs">{t('batch.status_' + (job.status === 'succeeded' ? 'done' : job.status))}</span></div>
            {(job.character_name || job.video) && <p className="break-all text-sm">{job.character_name || job.video}</p>}
            {job.reference && <p className="break-all text-xs text-muted-foreground">{job.reference}</p>}
            {job.text && <p className="line-clamp-2 text-sm text-muted-foreground">{job.text}</p>}
            <p className="text-sm">{job.message}</p>
            {job.audio_output && <><audio controls preload="none" src={apiPath(prefix + 'jobs/' + job.id + '/audio')} className="w-full" /><a href={apiPath(prefix + 'jobs/' + job.id + '/audio-download')} className="text-sm underline">{t('avatar.download')} · {t('avatar.audio')}</a></>}
            {job.status === 'succeeded' && job.output && <><video controls preload="none" src={apiPath(prefix + 'jobs/' + job.id + '/video')} className={previewClass + ' max-h-96'} /><a href={apiPath(prefix + 'jobs/' + job.id + '/download')} className="text-sm underline">{t('avatar.download')} · {t('avatar.video')}</a></>}
            {job.audio_output && <Button variant="outline" size="sm" disabled={busy || jobs.queue.full || !video} onClick={() => void send({mode: 'audio', character_id: characterId, fps, reuse_audio_job: job.id}, requestId())}>{t('avatar.retryVideo')}</Button>}
            {job.error && <details className="text-xs text-destructive"><summary>{t('common.error')}</summary><pre className="whitespace-pre-wrap break-all">{job.error}</pre></details>}
          </article>)}
          {jobs.jobs.length > visibleJobs && <Button variant="outline" onClick={() => setVisibleJobs(n => n + 8)}>{t('gallery.load_more')}</Button>}
        </section>
      </div>
    </main>
  </div>;
}
