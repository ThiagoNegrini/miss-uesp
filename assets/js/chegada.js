
const $=id=>document.getElementById(id);
const categoryAliases={rainha:'Rainha do Samba',passista:'Passista de Ouro',diva:'Diva do Samba (Plus Size)'};
function normCat(v){let s=String(v||'').trim().toLocaleLowerCase('pt-BR');if(s.includes('rainha'))return 'Rainha do Samba';if(s.includes('passista'))return 'Passista de Ouro';if(s.includes('diva')||s.includes('plus size')||s.includes('miss plus'))return 'Diva do Samba (Plus Size)';return String(v||'').trim()}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
let candidates=[], arrivals={};
const params=new URLSearchParams(location.search);const requested=(params.get('categoria')||'').toLowerCase();
if(categoryAliases[requested]){$('category').value=categoryAliases[requested];$('category').disabled=true}
const api=(path,options={})=>requestJSON(path,options)
async function load(){
 try{
  const [c,a]=await Promise.all([api('/api/arrival-candidates'),api('/api/arrivals')]);
  candidates=c.candidates||[];arrivals=a.arrivals||{};
  $('status').textContent='Conectado à central. Exibindo somente os 25 classificados na audição.';
  render();
 }catch(e){$('status').textContent='Sem comunicação com a central: '+e.message+'. Confira se o servidor está aberto e se este tablet está no mesmo Wi-Fi.'}
}
function render(){
 // Preserve tudo o que estiver digitado antes de reconstruir a lista (inclusive no auto-refresh).
 const drafts={};
 document.querySelectorAll('input[data-cid][data-field]').forEach(el=>{
  const id=String(el.dataset.cid); if(!drafts[id])drafts[id]={}; drafts[id][el.dataset.field]=el.value;
 });
 const active=document.activeElement;
 const activeKey=active&&active.matches('input[data-cid][data-field]')?`${active.dataset.cid}:${active.dataset.field}`:null;
 const caret=activeKey&&typeof active.selectionStart==='number'?active.selectionStart:null;
 const cat=$('category').value;const q=$('search').value.trim().toLocaleLowerCase('pt-BR');
 const all=candidates.filter(x=>normCat(x.category)===cat);
 const filtered=all.filter(x=>x.name.toLocaleLowerCase('pt-BR').includes(q));
 $('total').textContent=all.length;
 $('present').textContent=all.filter(x=>arrivals[x.id]?.status!=='AUSENTE' && !!arrivals[x.id]).length;
 $('pending').textContent=all.filter(x=>!arrivals[x.id]).length;
 $('absent').textContent=all.filter(x=>arrivals[x.id]?.status==='AUSENTE').length;
 $('list').innerHTML=filtered.length?filtered.map(x=>{
  const a=arrivals[x.id];const isAbsent=a?.status==='AUSENTE';const isPresent=!!a&&!isAbsent;return `<div class="candidate ${isPresent?'is-present':isAbsent?'is-absent':'is-pending'}"><div class="candidate-main"><div class="photo-panel">${x.photo?`<img class="candidate-photo" src="${x.photo}" alt="Foto de ${esc(x.name)}" loading="lazy">`:`<div class="photo-placeholder">Foto não cadastrada</div>`}<button type="button" class="photo-button" data-id="${x.id}" data-action="photo">📷 Tirar foto</button></div><div class="candidate-content"><div class="candidate-head"><div class="info"><div class="name">${esc(x.name)}</div><div class="meta">${isPresent?'Chegada registrada: '+esc(a.arrived_at||''):isAbsent?'Ausência registrada: '+esc(a.arrived_at||''):'Aguardando chegada'}</div></div><span class="badge ${isPresent?'yes':'no'}">${isPresent?'✓ PRESENTE':isAbsent?'⚠ AUSENTE':'◷ PENDENTE'}</span></div><div class="candidate-fields"><label>Nome artístico<input data-field="artistic_name" data-cid="${x.id}" value="${esc(x.artistic_name||'')}" placeholder="Nome artístico"></label><label>Manequim<input data-field="mannequin" data-cid="${x.id}" value="${esc(x.mannequin||'')}" placeholder="Manequim"></label><label>Calçado<input data-field="shoe_size" data-cid="${x.id}" value="${esc(x.shoe_size||'')}" placeholder="Calçado"></label><label>Tamanho Body<input data-field="body_size" data-cid="${x.id}" value="${esc(x.body_size||'')}" placeholder="Tamanho Body"></label></div><div class="candidate-actions"><button class="present" data-id="${x.id}" data-action="save">Salvar dados</button><button class="${isPresent?'undo':'present'}" data-id="${x.id}" data-action="${isPresent?'undo':'present'}">${isPresent?'Desfazer chegada':'Marcar presente'}</button><button class="undo" data-id="${x.id}" data-action="absent">Marcar ausente</button></div></div></div></div>`
 }).join(''):'<p class="muted">Nenhum candidato encontrado nesta categoria/busca.</p>';
 // Reaplica rascunhos locais, que têm prioridade sobre os valores recebidos do servidor.
 document.querySelectorAll('input[data-cid][data-field]').forEach(el=>{
  const value=drafts[String(el.dataset.cid)]?.[el.dataset.field]; if(value!==undefined)el.value=value;
 });
 document.querySelectorAll('button[data-id]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.action==='photo'){ $('photoInput').dataset.cid=b.dataset.id; $('photoInput').click(); } else mark(b.dataset.id,b.dataset.action)}));
 if(activeKey){const [id,field]=activeKey.split(':');const next=document.querySelector(`input[data-cid="${CSS.escape(id)}"][data-field="${CSS.escape(field)}"]`);if(next){next.focus({preventScroll:true});if(caret!==null)try{next.setSelectionRange(caret,caret)}catch(_){}}}
}
function fieldsFor(id){const get=k=>document.querySelector(`[data-cid=\"${CSS.escape(String(id))}\"][data-field=\"${k}\"]`)?.value||'';return {artistic_name:get('artistic_name'),mannequin:get('mannequin'),shoe_size:get('shoe_size'),body_size:get('body_size')}}
async function mark(id,action){
 const b=[...document.querySelectorAll(`button[data-id=\"${CSS.escape(String(id))}\"]`)].find(x=>x.dataset.action===action);if(b)b.disabled=true;
 try{
  const r=await api('/api/arrival',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:Number(id),present:action==='present',absent:action==='absent',save_only:action==='save',...fieldsFor(id)})});
  arrivals[id]=r.arrival||null;if(!arrivals[id])delete arrivals[id];
  $('status').textContent=action==='save'?'Dados de chegada salvos no banco de dados.':(action==='present'?'Presença e dados salvos e sincronizados.':action==='absent'?'Ausência registrada; a candidata ficará fora da classificação.':'Registro de presença desfeito.');
  render();
 }catch(e){$('status').textContent='Não foi possível salvar a presença: '+e.message}
}

$('photoInput').addEventListener('change',async()=>{
 const input=$('photoInput');const file=input.files&&input.files[0];const id=input.dataset.cid;input.value='';if(!file||!id)return;
 if(!file.type.startsWith('image/')){ $('status').textContent='Selecione uma imagem válida.';return; }
 try{
  // Redimensiona a imagem antes de enviá-la para manter o banco leve.
  const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>{const img=new Image();img.onload=()=>{const max=1200,scale=Math.min(1,max/Math.max(img.width,img.height));const canvas=document.createElement('canvas');canvas.width=Math.round(img.width*scale);canvas.height=Math.round(img.height*scale);canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);resolve(canvas.toDataURL('image/jpeg',0.82));};img.onerror=reject;img.src=reader.result;};reader.onerror=reject;reader.readAsDataURL(file);});
  const r=await api('/api/arrival',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:Number(id),save_only:true,photo:dataUrl,...fieldsFor(id)})});
  const c=candidates.find(x=>String(x.id)===String(id));if(c)c.photo=dataUrl; $('status').textContent='Foto salva no cadastro da candidata.';render();
 }catch(e){$('status').textContent='Não foi possível salvar a foto: '+e.message;}
});

$('category').addEventListener('change',render);$('search').addEventListener('input',render);
$('exportXlsx').addEventListener('click',()=>{window.location.href='/api/export-xlsx'});
load();setInterval(load,5000);
