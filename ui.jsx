import React,{useState} from 'react'
export function Button({children,primary=false,...p}){return <button className={`cp-btn ${primary?'primary':''}`} {...p}>{children}</button>}
export function Empty({title,children,action}){return <div className="cp-empty"><h3>{title}</h3><p>{children}</p>{action}</div>}
export function Tabs({items,value,onChange}){return <nav className="cp-tabs" role="tablist" aria-label="Views">{items.map(([id,label])=><button key={id} role="tab" aria-selected={value===id} className="cp-tab" onClick={()=>onChange(id)}>{label}</button>)}</nav>}
export function Field({label,hint,children}){return <label className="cp-label">{label}{children}{hint&&<span className="cp-hint">{hint}</span>}</label>}
export function Upload({label,value,onChange}){
 const [error,setError]=useState(''),[busy,setBusy]=useState(false)
 async function choose(file){setError('');if(!file){onChange(null);return}if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>2000000){setError('Choose a PNG, JPEG, or WebP image under 2 MB.');onChange(null);return}setBusy(true);try{const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file)});onChange({name:file.name,data})}catch{setError('Could not read that image. Choose it again.');onChange(null)}finally{setBusy(false)}}
 return <Field label={label} hint="PNG, JPEG, or WebP · up to 2 MB"><input className="cp-upload" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={e=>choose(e.target.files?.[0])}/>{value&&<img className="cp-image" src={value.data} alt={value.name}/>} {error&&<span role="alert" className="cp-error">{error}</span>}</Field>
}
export const validURL=v=>{try{const u=new URL(v);return ['http:','https:'].includes(u.protocol)&&!!u.hostname&&!u.username&&!u.password}catch{return false}}
export const validScore=v=>/^(10(?:\.0)?|[0-9](?:\.[0-9])?)$/.test(v)
