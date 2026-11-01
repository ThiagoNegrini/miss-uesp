
let previewRows=[];
let auditionCandidates=[];
let arrivalData={};
let auditionScores={};
let contestScores={};
let evaluatorRoster=[];
let contestEvaluatorRoster=[];
const $=id=>document.getElementById(id);
const api=(path,options={})=>requestJSON(path,options)
async function loadRoster(){try{const stage=$('rosterStage').value;const j=await api('/api/evaluators?stage='+stage);$('rosterNames').value=(j.evaluators||[]).join('\n');$('rosterStatus').textContent='Lista '+stage+' carregada ('+(j.evaluators||[]).length+' avaliador(es)).'}catch(e){$('rosterStatus').textContent='Falha ao carregar lista: '+e.message}}
$('loadRoster').addEventListener('click',loadRoster);$('rosterStage').addEventListener('change',loadRoster);$('saveRoster').addEventListener('click',async()=>{try{const stage=$('rosterStage').value,evaluators=$('rosterNames').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);const j=await api('/api/evaluators',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage,evaluators})});$('rosterNames').value=j.evaluators.join('\n');$('rosterStatus').textContent='Lista '+stage+' salva: '+j.evaluators.length+' avaliador(es).'}catch(e){$('rosterStatus').textContent='Falha ao salvar: '+e.message}});loadRoster();
async function refresh(){
 try{
   const stage=$('globalStage')?.value==='concurso'?'CONCURSO':'AUDICAO';
   const [d,c,a,s,e,contest,contestEvaluators]=await Promise.all([api('/api/arrival-candidates'),api('/api/candidates'),api('/api/arrivals'),api('/api/scores'),api('/api/evaluators?stage='+stage),api('/api/contest-candidates'),api('/api/evaluators?stage=CONCURSO')]);
   previewRows=d.candidates||[]; auditionCandidates=c.candidates||[]; arrivalData=a.arrivals||{}; auditionScores=s.scores||{}; contestScores=contest.scores||{}; evaluatorRoster=e.evaluators||[]; contestEvaluatorRoster=contestEvaluators.evaluators||[]; render();
   const presentes=previewRows.filter(x=>arrivalFor(x)).length;
   $('dbStatus').textContent='Banco local conectado. '+previewRows.length+' participante(s) cadastrado(s); '+((contest.candidates||[]).length)+' classificado(s) para o Concurso; '+presentes+' chegada(s) confirmada(s). Sincronização automática ativa.';
 }catch(e){$('dbStatus').textContent='Não foi possível atualizar o painel: '+e.message+'. Confirme que a Central está aberta pelo endereço do servidor local.'}
}
function normCategory(value){
 const s=String(value||'').trim().toLocaleLowerCase('pt-BR').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,' ');
 if(s.includes('rainha')) return 'Rainha do Samba';
 if(s.includes('passista')) return 'Passista de Ouro';
 if(s.includes('diva') || s.includes('miss plus') || s.includes('plus size')) return 'Diva do Samba (Plus Size)';
 return String(value||'').trim();
}
function candidateCategory(candidate){return normCategory(candidate?.category||candidate?.categoria||candidate?.modalidade||candidate?.category_name||'')}
function arrivalFor(candidate){return arrivalData[String(candidate.id)]||null}
function evaluatorProgress(candidate){
 const stage=$('globalStage')?.value==='concurso'?'CONCURSO':'AUDICAO';
 const saved=(stage==='concurso'?contestScores:auditionScores)[String(candidate.id)]||{};
 const roster=stage==='concurso'?contestEvaluatorRoster:evaluatorRoster;
 const hasScore=(v,criterion)=>v&&v[criterion]!==null&&v[criterion]!==undefined&&String(v[criterion]).trim()!=='';
 // Cada avaliador cadastrado conta no máximo uma vez. Normalizar nomes evita
 // diferenças de acento/espaços; limitar ao total impede estados inválidos como 5/2.
 const evaluatorKey=value=>String(value??'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,' ').trim().toLocaleLowerCase('pt-BR');
 const savedByEvaluator=new Map(Object.entries(saved).map(([name,values])=>[evaluatorKey(name),values]));
 const total=roster.length;
 const count=criterion=>{
   // A prévia só conta notas vinculadas aos avaliadores atualmente cadastrados.
   // Não usar fallback por quantidade de registros: isso marca todos os participantes
   // como avaliados quando existem notas antigas de outros avaliadores.
   return roster.filter(name=>hasScore(savedByEvaluator.get(evaluatorKey(name)),criterion)).length;
 };
 const status=criterion=>{const n=count(criterion);return `<span class="presence ${total>0&&n>=total?'yes':'no'}">${n}/${total}</span>`};
 if(stage==='CONCURSO'){const category=candidateCategory(candidate);const eligible=category==='Rainha do Samba'||category==='Diva do Samba (Plus Size)';return {aesthetics:eligible?status('aesthetics_sympathy'):'—',communication:status('communication_elegance'),samba:status('samba')}}
 return {aesthetics:'—',communication:status('communication'),samba:status('samba')};
}
function formatTime(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?String(value):d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}
function render(){
 const stage=$('globalStage')?.value||'audicao';
 const baseRows=stage==='concurso'?previewRows:auditionCandidates;
 const f=$('filter').value, pf=$('presenceFilter') ? $('presenceFilter').value : '';
 const q=($('candidateSearch')?.value||'').trim().toLocaleLowerCase('pt-BR');
 $('arrivalTitle').textContent=stage==='concurso'?'3. Classificados na Audição — controle de chegada':'3. Chegada da Audição — controle de chegada';
 $('arrivalHelp').textContent=stage==='concurso'?'Exibe somente candidatas classificadas e publicadas na Audição. Confira as chegadas registradas.':'Exibe a lista geral de candidatas da Audição. Confira as chegadas registradas.';
 const categoryOrder=['Passista de Ouro','Diva do Samba (Plus Size)','Rainha do Samba'];
 let rows=baseRows.filter(x=>(!q||String(x.name||'').toLocaleLowerCase('pt-BR').includes(q))&&(!f||normCategory(x.category)===f));
 rows=rows.filter(x=>!pf||(pf==='present'?!!arrivalFor(x):!arrivalFor(x)));
 rows.sort((a,b)=>{const ca=normCategory(a.category),cb=normCategory(b.category);const ia=categoryOrder.indexOf(ca),ib=categoryOrder.indexOf(cb);return (ia<0?categoryOrder.length:ia)-(ib<0?categoryOrder.length:ib)||String(a.name||'').localeCompare(String(b.name||''),'pt-BR')});
 $('total').textContent=baseRows.length;
 const cats={'Rainha do Samba':'rainha','Passista de Ouro':'passista','Diva do Samba (Plus Size)':'diva'};
 for(const [c,id] of Object.entries(cats))$(id).textContent=baseRows.filter(x=>normCategory(x.category)===c).length;
 const presentes=baseRows.filter(x=>!!arrivalFor(x)).length;
 $('presentes').textContent=presentes; $('aguardando').textContent=Math.max(0,baseRows.length-presentes);
 $('categories').innerHTML=Object.keys(cats).map(c=>{const list=baseRows.filter(x=>normCategory(x.category)===c);const count=list.filter(x=>!!arrivalFor(x)).length;return `<div class="category"><div><b>${c}</b><br><span>${count} presente(s) de ${list.length}</span></div><b>${count}/${list.length}</b></div>`}).join('');
 if(!rows.length){$('rows').innerHTML='<tr><td colspan="6">Nenhum candidato para este filtro.</td></tr>'}
 else{
   let rowNumber=0;
   const grouped=categoryOrder.map(category=>({category,items:rows.filter(x=>normCategory(x.category)===category)})).filter(group=>group.items.length);
   const other=rows.filter(x=>!categoryOrder.includes(normCategory(x.category)));
   if(other.length)grouped.push({category:'Outras categorias',items:other});
   $('rows').innerHTML=grouped.map(group=>`<tr class="category-group"><td colspan="6" style="background:#fff5f5;color:#a50d25;font-weight:800;border-top:2px solid #e5b7be">${esc(group.category)} — ${group.items.length} participante(s)</td></tr>`+group.items.map(x=>{const a=arrivalFor(x);rowNumber++;return `<tr><td>${rowNumber}</td><td>${esc(normCategory(x.category))}</td><td>${esc(x.name)}</td><td><span class="presence ${a?'yes':'no'}">${a?'Presente':'Aguardando'}</span></td><td>${a?esc(formatTime(a.arrived_at)):'—'}</td><td>${a?esc(a.device||'—'):'—'}</td></tr>`}).join('')).join('');
 }
}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
$('file').addEventListener('change',()=>{$('importBtn').disabled=!$('file').files.length;$('importStatus').textContent=$('file').files.length?'Arquivo selecionado: '+$('file').files[0].name:'Aguardando arquivo Excel.'});
$('importBtn').addEventListener('click',async()=>{
 const file=$('file').files[0];if(!file)return;$('importBtn').disabled=true;$('importStatus').textContent='Importando e validando candidatos…';
 try{const fd=new FormData();fd.append('file',file);const r=await fetch('/api/import',{method:'POST',body:fd});const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'Erro ao importar');$('importStatus').textContent=`Importação concluída: ${j.imported} candidato(s). Categorias: ${JSON.stringify(j.counts)}.`;await refresh()}
 catch(e){$('importStatus').textContent='Falha na importação: '+e.message}finally{$('importBtn').disabled=false}
});
$('refreshBtn').addEventListener('click',refresh);$('filter').addEventListener('change',render);if($('candidateSearch'))$('candidateSearch').addEventListener('input',render);
async function applyGlobalStage(stage,persist=true){$('globalStage').value=stage;const rosterStage=$('rosterStage');if(rosterStage){rosterStage.value=stage.toUpperCase();loadRoster()}const link=$('evaluatorLink');link.href='avaliador.html?stage='+encodeURIComponent(stage);link.textContent='Abrir avaliador — '+(stage==='concurso'?'Concurso':'Audição');document.querySelector('header small').textContent=(stage==='concurso'?'CONCURSO':'AUDIÇÃO')+' MISS UESP • CARNAVAL 2027';try{localStorage.setItem('uesp_active_stage',stage)}catch(e){};render();if(persist){try{const response=await api('/api/active-stage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage:stage==='concurso'?'CONCURSO':'AUDICAO'})});if(response.stage!==(stage==='concurso'?'CONCURSO':'AUDICAO'))throw new Error('O servidor retornou uma etapa diferente.');$('dbStatus').textContent='Etapa '+(stage==='concurso'?'Concurso':'Audição')+' sincronizada com o servidor.'}catch(e){$('dbStatus').textContent='ERRO ao sincronizar etapa: '+e.message+' — confira se a Central está aberta pelo mesmo endereço do servidor.'}}}
$('globalStage').addEventListener('change',()=>applyGlobalStage($('globalStage').value,true));
(async()=>{try{const current=await api('/api/active-stage');await applyGlobalStage(String(current.stage||'AUDICAO').toLowerCase()==='concurso'?'concurso':'audicao',false)}catch(e){console.warn('Não foi possível carregar a etapa do servidor:',e)}})();if($('presenceFilter')) $('presenceFilter').addEventListener('change',render);

$('clearContestBtn').addEventListener('click',async()=>{
 if(!confirm('Confirma apagar TODAS as notas do Concurso? A lista de avaliadores do Concurso, as notas e os avaliadores da Audição, candidatos, fotos, categorias, ordem e publicação da Audição serão preservados.'))return;
 const b=$('clearContestBtn');b.disabled=true;$('clearContestStatus').textContent='Limpando somente as notas do Concurso…';
 try{const j=await api('/api/clear-contest-scores',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});$('clearContestStatus').textContent=`Concluído: ${j.deleted} nota(s) do Concurso removida(s). Lista de avaliadores e dados da Audição preservados.`;}catch(e){$('clearContestStatus').textContent='Falha: '+e.message}finally{b.disabled=false}
});
$('clearScoresBtn').addEventListener('click',async()=>{
 const confirmed=confirm('ATENÇÃO: esta ação reiniciará os dados operacionais como numa nova importação. Serão apagados TODAS as notas e nomes de avaliadores, avaliações do Concurso, chegadas, fotos, nome artístico, manequim, calçado, tamanho de body e resultados publicados. A lista oficial de candidatos e a planilha importada serão preservadas. Esta ação NÃO pode ser desfeita. Deseja continuar?');
 if(!confirmed)return;
 const btn=$('clearScoresBtn');btn.disabled=true;$('clearScoresStatus').textContent='Limpando notas, chegadas, fotos e dados de cadastro…';
 try{
  const j=await api('/api/clear-scores',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
  $('clearScoresStatus').textContent=`Concluído: ${j.deleted} nota(s) de Audição, ${j.deleted_contest||0} avaliação(ões) do Concurso, ${j.deleted_arrivals||0} chegada(s) e ${j.deleted_photos||0} foto(s) apagadas. A lista oficial de candidatos e a planilha importada foram preservadas.`;
 }catch(e){$('clearScoresStatus').textContent='Falha na exclusão: '+e.message}
 finally{btn.disabled=false}
});
$('exportBtn').addEventListener('click',()=>{
 const f=$('filter').value,pf=$('presenceFilter') ? $('presenceFilter').value : '';let rows=previewRows.filter(x=>!f||normCategory(x.category)===f);rows=rows.filter(x=>!pf||(pf==='present'?!!arrivalFor(x):!arrivalFor(x)));
 const csv=['Categoria;Nome do candidato;Chegada;Horário;Dispositivo',...rows.map(x=>{const a=arrivalFor(x);return `"${normCategory(x.category).replaceAll('"','""')}";"${String(x.name||'').replaceAll('"','""')}";"${a?'Presente':'Aguardando'}";"${a?formatTime(a.arrived_at):''}";"${a?String(a.device||'').replaceAll('"','""'):''}"`})].join('\r\n');
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));a.download='candidatos_uesp_2027.csv';a.click();URL.revokeObjectURL(a.href)
});
refresh();setInterval(refresh,5000);
