import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PlayCircleIcon } from 'lucide-react';
import { apiPath, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { Dialog,DialogContent,DialogHeader,DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useCharacters, type Character } from './characters-api';

export function characterDuration(seconds:number) {return Math.floor(seconds/60)+':'+String(Math.floor(seconds%60)).padStart(2,'0');}

export function CharacterVideo({character,compact=false}:{character:Character;compact?:boolean}) {
  const {t}=useTranslation();const [compatible,setCompatible]=useState(false);const [failed,setFailed]=useState(false);
  return <div className="grid gap-2">
    <video key={character.revision+String(compatible)} data-testid="character-preview" controls playsInline preload="metadata" poster={apiPath(character.poster_url)} src={apiPath(character.preview_url+(compatible?'&preview=1':''))}
      onError={()=>{if(compatible)setFailed(true);else setCompatible(true);}} className={'aspect-[9/16] w-auto max-w-full justify-self-center rounded-lg bg-black object-contain '+(compact?'h-[min(38vh,360px)]':'h-[min(65vh,640px)]')}/>
    {compatible&&<p className="text-xs text-muted-foreground">{t('characters.compatiblePreview')}</p>}
    {failed&&<p role="alert" className="text-sm text-destructive">{t('modelSettings.unavailable')}</p>}
  </div>;
}

export function CharacterCover({character}:{character:Character}) {
  return <div className="relative aspect-[9/16] overflow-hidden rounded-lg bg-muted">
    <img src={apiPath(character.poster_url)} alt={character.name} loading="lazy" className="h-full w-full object-contain"/>
    <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 text-xs text-white">{characterDuration(character.duration)}</span>
    <PlayCircleIcon aria-hidden="true" className="absolute left-1/2 top-1/2 size-9 -translate-x-1/2 -translate-y-1/2 text-white drop-shadow"/>
  </div>;
}

export function CharacterPicker({value,onChange,disabled=false}:{value:string;onChange:(id:string)=>void;disabled?:boolean}) {
  const {t}=useTranslation();const query=useCharacters();const [open,setOpen]=useState(false);const [search,setSearch]=useState('');
  const selected=query.data?.find(c=>c.id===value);const rows=(query.data??[]).filter(c=>(c.name+' '+c.notes).toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <div className="grid gap-3" data-testid="character-picker">
    <span className="font-medium">{t('characters.character')}</span>
    <Button type="button" variant="outline" disabled={disabled} onClick={()=>setOpen(true)}>{selected?selected.name:t('characters.pick')}</Button>
    {query.isError&&<p role="alert" className="text-sm text-destructive">{describeError(query.error)}</p>}
    {selected&&<><CharacterVideo key={selected.revision} character={selected}/><div className="text-xs text-muted-foreground">{characterDuration(selected.duration)} · {selected.width} × {selected.height}{selected.notes&&<p className="mt-1 whitespace-pre-wrap">{selected.notes}</p>}</div>{!selected.available&&<p role="alert">{t('modelSettings.unavailable')}</p>}</>}
    {!selected&&!query.isLoading&&<p className="text-sm text-muted-foreground">{t(query.data?.length?'characters.pick':'characters.empty')}</p>}
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-3xl"><DialogHeader><DialogTitle>{t('characters.pick')}</DialogTitle></DialogHeader>
        <Input aria-label={t('common.search')} placeholder={t('common.search')} value={search} onChange={e=>setSearch(e.target.value)}/>
        <div className="grid max-h-[60vh] grid-cols-2 gap-3 overflow-y-auto sm:grid-cols-3">
          {rows.map(c=><button key={c.id} type="button" aria-label={c.name} disabled={!c.available} onClick={()=>{onChange(c.id);setOpen(false);}} className="grid content-start gap-2 rounded-xl border border-border p-2 text-left outline-none hover:border-primary focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
            <CharacterCover character={c}/><span className="truncate font-medium">{c.name}</span>{c.notes&&<span className="line-clamp-2 text-xs text-muted-foreground">{c.notes}</span>}
          </button>)}
        </div>
        {!rows.length&&<p className="text-sm text-muted-foreground">{query.isLoading?t('common.loading'):query.data?.length?t('common.no_matches'):t('characters.empty')}</p>}
      </DialogContent>
    </Dialog>
  </div>;
}
