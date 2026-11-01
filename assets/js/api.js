/* Shared local API client for the offline MISS UESP application. */
async function requestJSON(path, options={}) {
 const config={cache:'no-store',...options};
 const response=await fetch(path,config);
 const contentType=response.headers.get('content-type')||'';
 if(!contentType.includes('application/json')) {
  const detail=(await response.text()).slice(0,160).replace(/\s+/g,' ');
  throw new Error('A rota '+path+' não retornou JSON (HTTP '+response.status+'). '+detail);
 }
 let payload;
 try { payload=await response.json(); }
 catch(e) { throw new Error('Resposta inválida da rota '+path+'.'); }
 if(!response.ok||payload.ok===false) throw new Error(payload.error||'Falha na comunicação com a central (HTTP '+response.status+').');
 return payload;
}
