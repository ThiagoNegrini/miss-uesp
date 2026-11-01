
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=(path,body)=>requestJSON(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})
let state={evaluators:[],results:[],released:false};
async function load(){try{
 state=await api('/api/agulha/data');
 $('roster').value=(state.evaluators||[]).join(String.fromCharCode(10));
 $('head').innerHTML='<tr><th>Categoria</th><th>Candidata</th>'+state.evaluators.map(x=>'<th>'+esc(x)+'</th>').join('')+'<th>Soma</th><th>Posição geral</th><th>Situação</th></tr>';
 const list=state.results||[];
 const ranked=list.filter(x=>x.total!=null).sort((x,y)=>Number(y.total)-Number(x.total)||String(x.name).localeCompare(String(y.name),'pt-BR')||String(x.category).localeCompare(String(y.category),'pt-BR'));
 const topScore=ranked.length?Number(ranked[0].total):null;
 const topCount=ranked.filter(x=>Number(x.total)===topScore).length;
 let previous=null,position=0;
 ranked.forEach((x,index)=>{if(previous===null||Number(x.total)!==previous)position=index+1;x.position=position;x.tied_for_first=Number(x.total)===topScore&&topCount>1;previous=Number(x.total)});
 list.forEach(x=>{if(x.total==null){delete x.position;x.tied_for_first=false}});
 list.sort((x,y)=>(x.position||999)-(y.position||999)||String(x.category).localeCompare(String(y.category),'pt-BR')||String(x.name).localeCompare(String(y.name),'pt-BR'));
 const leaders=ranked.filter(x=>x.position===1);
 $('winnerSummary').textContent=leaders.length>1
   ?'Empate geral entre '+leaders.length+' participantes ('+leaders.map(x=>x.name+' — '+x.category).join('; ')+'). Reavalie uma nota para definir uma única vencedora.'
   :leaders.length===1
     ?(leaders[0].complete?'Vencedora geral: ':'Líder geral parcial: ')+leaders[0].name+' — '+leaders[0].category
     :'Ainda não há notas para formar a classificação geral.';
 $('rows').innerHTML=list.map(x=>'<tr><td>'+esc(x.category)+'</td><td>'+esc(x.name)+'</td>'+state.evaluators.map(ev=>'<td>'+(x.scores[ev]==null?'—':Number(x.scores[ev]).toLocaleString('pt-BR',{minimumFractionDigits:1}))+'</td>').join('')+'<td><b>'+(x.total==null?'—':Number(x.total).toLocaleString('pt-BR',{minimumFractionDigits:1}))+'</b></td><td>'+(x.tied_for_first?'Empate geral':(x.position||'—')+(x.position?'º':''))+'</td><td>'+esc(x.status)+'</td></tr>').join('')||'<tr><td colspan="7">Ainda não há candidatas elegíveis.</td></tr>';
 $('release').checked=!!state.released;
 $('resultStatus').textContent=(state.released?'Liberado ao apresentador. ':'Não liberado. ')+(leaders.length>1?'Empate geral: a liberação permanece bloqueada. ':'')+list.filter(x=>x.complete).length+'/'+list.length+' avaliações completas.'
 }catch(e){$('resultStatus').textContent='Falha ao carregar: '+e.message}}
$('saveRoster').onclick=async()=>{try{const evaluators=$('roster').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);const j=await api('/api/agulha/evaluators',{evaluators});$('rosterStatus').textContent='Lista salva: '+j.evaluators.length+' avaliadores.';await load()}catch(e){$('rosterStatus').textContent=e.message}};
$('clearData').onclick=async()=>{if(!confirm('Apagar todas as notas do Agulha de Ouro e os avaliadores cadastrados? As participantes e os dados do Concurso serão preservados.'))return;const b=$('clearData');b.disabled=true;try{const j=await api('/api/agulha/clear',{confirm:true});$('rosterStatus').textContent='Limpeza concluída: '+j.deleted_scores+' nota(s) e '+j.deleted_evaluators+' avaliador(es) removidos.';await load()}catch(e){$('rosterStatus').textContent='Falha ao limpar: '+e.message}finally{b.disabled=false}};
$('release').onchange=async()=>{const control=$('release');try{const j=await api('/api/agulha/publication',{released:control.checked});$('resultStatus').textContent=j.released?'Resultado liberado separadamente ao apresentador.':'Resultado ocultado ao apresentador.';await load()}catch(e){control.checked=!control.checked;$('resultStatus').textContent=e.message}};
$('refresh').onclick=load;load();
