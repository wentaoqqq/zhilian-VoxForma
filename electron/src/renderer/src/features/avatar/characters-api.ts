import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiJson } from '@/lib/api/client';

export interface Character {
  id:string; name:string; notes:string; revision:string; created:number; updated:number; deleted:number|null;
  filename:string; duration:number; width:number; height:number; fps:number; bytes:number; available:boolean;
  poster_url:string; preview_url:string;
}
export const characterApi='/api/avatar/characters';
const selectionKey='voicestudio.avatar.character.v1';
export function rememberCharacter(id:string) {try{localStorage.setItem(selectionKey,id);}catch{/* Selection still works in memory. */}}
export function useCharacterSelection() {
  const [id,setId]=useState(()=>{try{return localStorage.getItem(selectionKey)||'';}catch{return '';}});
  return [id,(next:string)=>{rememberCharacter(next);setId(next);}] as const;
}
export function useCharacters(deleted=false) {
  return useQuery({queryKey:['avatar-characters',deleted],queryFn:()=>apiJson<Character[]>(characterApi+(deleted?'?deleted=true':'')),staleTime:15000,refetchInterval:30000});
}
export function useRefreshCharacters() {
  const client=useQueryClient();return ()=>client.invalidateQueries({queryKey:['avatar-characters']});
}
