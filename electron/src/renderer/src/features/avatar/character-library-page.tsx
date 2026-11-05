import { useEffect,useRef,useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ContactRoundIcon,PencilIcon,PlusIcon,Trash2Icon,Undo2Icon,UploadIcon } from 'lucide-react';
import { apiJson,describeError } from '@/lib/api/client';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Dialog,DialogContent,DialogHeader,DialogTitle,DialogFooter } from '@/components/ui/dialog';
import { CharacterCover,CharacterVideo,characterDuration } from './character-picker';
import { characterApi,useCharacters,useRefreshCharacters,type Character } from './characters-api';

function CharacterEditor({character,onSaved,onClose}:{character:Character|null;onSaved:()=>void;onClose:()=>void}) {
  const {t}=useTranslation();const [name,setName]=useState(character?.name??'');const [notes,setNotes]=useState(character?.notes??'');
  const [file,setFile]=useState<File|null>(null);const [url,setUrl]=useState('');
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{if(!file){setUrl('');return;}const next=URL.createObjectURL(file);setUrl(next);return()=>URL.revokeObjectURL(next);},[file]);
  async function save(){
    setBusy(true);setError('');
    try{
      if(file&&(!file.size||file.size>2*1024**3))throw new Error(t('common.error')+' · 0–2 GiB');
      if(character&&!file)await apiJson(characterApi+'/'+character.id,{method:'PATCH',body:JSON.stringify({name,notes})});
      else{
        if(!file)throw new Error(t('characters.uploadVideo'));
        const params=new URLSearchParams({name,notes,filename:file.name});
        await apiJson(characterApi+(character?'/'+character.id+'/video':'')+'?'+params.toString(),{method:character?'PUT':'POST',body:file,headers:{'Content-Type':'application/octet-stream'}});
      }
      onSaved();
    }catch(e){setError(describeError(e));}finally{setBusy(false);}
  }
  return <Dialog open onOpenChange={open=>{if(!open&&!busy)onClose();}}><DialogContent showCloseButton={!busy} className="max-h-[90vh] overflow-y-auto sm:max-w-xl" data-testid="character-editor">
    <DialogHeader><DialogTitle>{t(character?'characters.edit':'characters.create')}</DialogTitle></DialogHeader>
    <label className="grid gap-2 text-sm">{t('characters.name')}<Input id="character-name" maxLength={80} value={name} disabled={busy} onChange={e=>setName(e.target.value)} placeholder={t('characters.namePlaceholder')}/></label>
    <label className="grid gap-2 text-sm">{t('characters.notes')}<Textarea id="character-notes" rows={2} maxLength={2000} value={notes} disabled={busy} onChange={e=>setNotes(e.target.value)}/></label>
    <div className="grid gap-2">
      <input ref={input} id="character-video-upload" aria-hidden="true" tabIndex={-1} type="file" accept=".mp4,.mov,.mkv" className="sr-only" disabled={busy} onChange={e=>{const f=e.currentTarget.files?.[0];e.currentTarget.value='';if(f){setFile(f);if(!name)setName(f.name.replace(/\.[^.]+$/,''));}}}/>
      <Button variant="outline" disabled={busy} onClick={()=>input.current?.click()}><UploadIcon/>{t(character?'characters.replaceVideo':'characters.uploadVideo')}</Button>
      <p className="break-all text-xs text-muted-foreground">{file?file.name+' · '+(file.size/1024**2).toFixed(1)+' MiB':'MP4 / MOV / MKV · ≤ 2 GiB'}</p>
    </div>
    {url?<video key={url} src={url} controls preload="metadata" className="aspect-[9/16] h-[min(38vh,360px)] w-auto max-w-full justify-self-center rounded-lg bg-black object-contain"/>:character?<CharacterVideo character={character} compact/>:null}
    {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
    {busy&&<p role="status" className="text-sm">{file?t('characters.uploading'):t('common.saving')}</p>}
    <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button><Button id="character-save" disabled={busy||!name.trim()||(!character&&!file)} onClick={()=>void save()}>{t('common.save')}</Button></DialogFooter>
  </DialogContent></Dialog>;
}

export function CharacterLibraryPage(){
  const {t}=useTranslation();const [deleted,setDeleted]=useState(false);const query=useCharacters(deleted);const refresh=useRefreshCharacters();
  const [search,setSearch]=useState('');const [editor,setEditor]=useState<Character|null|undefined>(undefined);const [preview,setPreview]=useState<Character|null>(null);const [removing,setRemoving]=useState<Character|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const rows=(query.data??[]).filter(c=>(c.name+' '+c.notes).toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  async function remove(c:Character,restore=false){setBusy(true);setError('');try{await apiJson(characterApi+'/'+c.id+(restore?'/restore':''),{method:restore?'POST':'DELETE'});setRemoving(null);await refresh();}catch(e){setError(describeError(e));}finally{setBusy(false);}}
  return <div className="flex h-full min-h-0 flex-col">
    <WorkspaceHeader><h1 className="flex items-center gap-2"><ContactRoundIcon className="size-4"/>{t('characters.library')}</h1><Button variant="ghost" size="sm" onClick={()=>void refresh()}>{t('common.refresh')}</Button></WorkspaceHeader>
    <main data-testid="character-library" className="flex-1 overflow-y-auto p-5"><div className="mx-auto grid max-w-6xl gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{t('characters.hint')}</p><Button id="character-create" onClick={()=>setEditor(null)}><PlusIcon/>{t('characters.create')}</Button></div>
      <div className="flex flex-wrap items-center justify-between gap-3"><Input aria-label={t('common.search')} placeholder={t('common.search')} value={search} onChange={e=>setSearch(e.target.value)} className="max-w-sm"/><label className="flex items-center gap-2 text-sm"><input id="character-show-deleted" type="checkbox" checked={deleted} onChange={e=>setDeleted(e.target.checked)}/>{t('characters.deleted')}</label></div>
      {(error||query.isError)&&<p role="alert" className="text-sm text-destructive">{error||describeError(query.error)}</p>}
      {query.isLoading?<p role="status">{t('common.loading')}</p>:!rows.length?<div className="grid justify-items-center gap-3 rounded-xl border border-dashed border-border p-12 text-center text-muted-foreground"><ContactRoundIcon className="size-10"/><p>{query.data?.length?t('common.no_matches'):t('characters.empty')}</p>{!deleted&&<Button variant="outline" onClick={()=>setEditor(null)}>{t('characters.create')}</Button>}</div>:<div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {rows.map(c=><article key={c.id} data-character-id={c.id} className="grid content-start gap-3 rounded-xl border border-border p-3">
          <button type="button" aria-label={t('characters.preview')+' · '+c.name} onClick={()=>setPreview(c)} className="rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"><CharacterCover character={c}/></button>
          <div><h2 className="truncate font-semibold" title={c.name}>{c.name}</h2><p className="mt-1 text-xs text-muted-foreground">{characterDuration(c.duration)} · {c.width} × {c.height}</p>{c.notes&&<p className="mt-2 line-clamp-2 whitespace-pre-wrap text-sm text-muted-foreground">{c.notes}</p>}{!c.available&&<p className="mt-2 text-xs text-destructive">{t('modelSettings.unavailable')}</p>}</div>
          <div className="flex flex-wrap gap-1">{deleted?<Button variant="outline" size="sm" disabled={busy} onClick={()=>void remove(c,true)}><Undo2Icon/>{t('common.resume')}</Button>:<><Button variant="ghost" size="sm" disabled={busy} onClick={()=>setEditor(c)}><PencilIcon className="size-3.5"/>{t('characters.edit')}</Button><Button variant="ghost" size="sm" aria-label={t('common.delete')+' · '+c.name} disabled={busy} onClick={()=>setRemoving(c)}><Trash2Icon className="size-3.5"/></Button></>}</div>
        </article>)}
      </div>}
    </div></main>
    {editor!==undefined&&<CharacterEditor character={editor} onClose={()=>setEditor(undefined)} onSaved={()=>{setEditor(undefined);setDeleted(false);void refresh();}}/>}
    <Dialog open={!!preview} onOpenChange={open=>{if(!open)setPreview(null);}}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md"><DialogHeader><DialogTitle>{preview?.name}</DialogTitle></DialogHeader>{preview&&<><CharacterVideo key={preview.revision} character={preview}/><p className="whitespace-pre-wrap text-sm text-muted-foreground">{preview.notes}</p></>}</DialogContent></Dialog>
    <Dialog open={!!removing} onOpenChange={open=>{if(!open&&!busy)setRemoving(null);}}><DialogContent showCloseButton={!busy}><DialogHeader><DialogTitle>{t('common.delete')} · {removing?.name}</DialogTitle></DialogHeader><p className="text-sm text-muted-foreground">{t('characters.deleteNote')}</p><DialogFooter><Button variant="outline" disabled={busy} onClick={()=>setRemoving(null)}>{t('common.cancel')}</Button><Button id="character-confirm-delete" disabled={busy} onClick={()=>removing&&void remove(removing)}>{t('common.delete')}</Button></DialogFooter></DialogContent></Dialog>
  </div>;
}
