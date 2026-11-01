
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm=v=>String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR').trim();
let currentData={released:false,participants:[],winners:[]};
let resultsOpen=false;
let renderedWinnersSignature='';
async function refreshAgulha(){
 try{
  const j=await requestJSON('/api/agulha/presenter-data');
  $('agulhaButton').disabled=!j.released;
  $('agulhaStatus').textContent=j.released?'Vencedora do Agulha de Ouro liberada independentemente do Concurso. Clique para revelar.':'Resultado geral do Agulha de Ouro ainda não liberado.';
  $('agulhaButton').onclick=()=>{if(!j.released)return;$('agulhaWinners').innerHTML=(j.winners||[]).map(w=>'<article class="winner"><div>'+(w.photo?'<img src="'+esc(w.photo)+'" alt="">':'')+'</div><div><div class="position">AGULHA DE OURO</div><h3>'+esc(w.name)+'</h3><div>'+esc(w.category)+'</div></div></article>').join('')||'<div class="empty">Não há resultado publicado.</div>';};
 }catch(e){$('agulhaStatus').textContent='Não foi possível carregar o Agulha de Ouro: '+e.message}
}
async function refresh(){
 try{
  const j=await requestJSON('/api/presenter-data');
  // Depois de abrir o resultado, congela a interface para a sincronização periódica não fechar os detalhes.
  if(resultsOpen)return;
  currentData=j;renderParticipants();renderRelease();
  $('status').textContent=(j.participants||[]).length+' participante(s) carregado(s) · sincronizado às '+new Date().toLocaleTimeString('pt-BR');
 }catch(e){$('status').textContent='Não foi possível carregar: '+e.message}
}
function renderParticipants(){
 const groups={passista:[],diva:[],rainha:[]};
 for(const p of currentData.participants||[]){const c=norm(p.category);if(c.includes('passista'))groups.passista.push(p);else if(c.includes('diva'))groups.diva.push(p);else if(c.includes('rainha'))groups.rainha.push(p)}
 for(const [key,list] of Object.entries(groups)){
  list.sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),'pt-BR'));
  $(key).innerHTML=list.length?list.map(p=>'<div class="person"><div class="name">'+esc(p.name)+'</div><div class="art">'+(p.artistic_name?'Nome artístico: '+esc(p.artistic_name):'Nome artístico não informado')+'</div></div>').join(''):'<div class="empty">Nenhum participante</div>';
 }
}
function renderRelease(){
 const released=!!currentData.released;
 $('resultButton').disabled=!released;
 $('resultButton').textContent=released?'RESULTADO':'RESULTADO — AGUARDANDO LIBERAÇÃO';
 if(!released){
  resultsOpen=false;
  document.body.classList.remove('results-mode');
  $('results').style.display='none';
  $('locked').style.display='block';
  $('locked').textContent='O resultado final ainda não foi liberado pela organização.';
  $('winnerGrid').innerHTML='';
 }else if(resultsOpen){
  renderWinners();
 }else{
  $('results').style.display='none';
  $('locked').style.display='block';
  $('locked').textContent='Resultado liberado. Clique em RESULTADO para exibir os seis vencedores.';
 }
}
function showResults(){
 if(!currentData.released)return;
 resultsOpen=true;
 document.body.classList.add('results-mode');
 renderWinners();
}
function winnerIcon(w){const p=norm(w.position||'');const c=norm(w.category||'');return (p.includes('passista')||c.includes('passista'))?'🎩':'👑';}
function renderWinners(){
 const winners=[...(currentData.winners||[])];
 const order=['Passista de prata','Passista de ouro','Diva do Samba (Plus Size)','2ª Princesa','1ª Princesa','Rainha'];
 winners.sort((a,b)=>order.indexOf(a.position)-order.indexOf(b.position));
 const signature=JSON.stringify(winners);
 if(signature!==renderedWinnersSignature){
  const opened=new Set([...$('winnerGrid').querySelectorAll('.winner-detail.open')].map(el=>el.id));
  $('winnerGrid').innerHTML=winners.map((w,i)=>'<div class="winner-item"><button class="winner-tile" type="button" aria-expanded="false" aria-controls="winner-detail-'+i+'" onclick="toggleWinner('+i+')"><span class="tile-icon">'+winnerIcon(w)+'</span><span>'+esc(w.position)+'</span></button><article class="winner-detail" id="winner-detail-'+i+'">'+(w.photo?'<img src="'+esc(w.photo)+'" alt="Foto de '+esc(w.name)+'">':'<div class="no-photo">Foto não cadastrada</div>')+'<div><div class="position">'+esc(w.position)+'</div><h3>'+esc(w.name)+'</h3><div class="art">'+esc(w.artistic_name||'')+'</div><div class="art">'+esc(w.category)+'</div></div></article></div>').join('')||'<p class="empty">Os seis resultados ainda não estão completos na apuração oficial.</p>';
  winners.forEach((w,i)=>{if(opened.has('winner-detail-'+i)){const detail=$('winner-detail-'+i),button=detail?.previousElementSibling;if(detail){detail.classList.add('open');button?.setAttribute('aria-expanded','true');}}});
  renderedWinnersSignature=signature;
 }
 $('locked').style.display='none';$('results').style.display='block';
}
function toggleWinner(index){
 const detail=$('winner-detail-'+index);
 const button=detail?.previousElementSibling;
 if(!detail||!button)return;
 const opening=!detail.classList.contains('open');
 detail.classList.toggle('open',opening);
 button.setAttribute('aria-expanded',String(opening));
}
$('resultButton').addEventListener('click',showResults);
refresh();refreshAgulha();setInterval(refresh,5000);setInterval(refreshAgulha,3000);
