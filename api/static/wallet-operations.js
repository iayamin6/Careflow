// Operator controls use the same authenticated APIs as workflow automation.
const originalShow=show;
show=async function(tab){
  const extra=['executive','incidents','servicing','preferences','complaints','batch','experiments','governance','deliveries'];
  if(!extra.includes(tab)) return originalShow(tab);
  current=tab;
  try{
    const routes={executive:'report',incidents:'incidents',servicing:'servicing',preferences:null,complaints:'complaints',batch:'batch',experiments:'experiments',governance:'models/monitoring',deliveries:'deliveries'};
    const data=routes[tab]?await api(routes[tab]):{};
    let html='<section><h2>'+esc(({batch:'Spark analytics',servicing:'Settlements & refunds',governance:'Model governance',deliveries:'Delivery outbox',executive:'Executive overview'}[tab]||tab.charAt(0).toUpperCase()+tab.slice(1)))+'</h2>';
    if(tab==='executive'){
      html+='<div class="grid">'+[['Customers',data.customers],['Active customers',data.active_customers],['Merchants',data.merchants],['Elevated churn',data.elevated_churn],['Successful value / 30d (BDT)',(data.successful_volume_minor_30d/100).toFixed(2)],['Merchants needing attention',data.merchants_requiring_attention.length],['Open actions',data.open_actions]].map(([k,v])=>'<div class="card"><h3>'+esc(k)+'</h3><div class="metric">'+esc(v)+'</div></div>').join('')+'</div>';
      html+='<h3>Recommended interventions</h3>'+table(['Action','Count'],Object.entries(data.recommended_interventions).map(([k,v])=>[esc(k),v]));
    }
    if(tab==='incidents'){
      html+=table(['ID','Entity','Priority','Owner','Status','Manage'],data.data.map(r=>[r.id,esc(r.entity_type+' '+r.entity_id),esc(r.priority),esc(r.owner),esc(r.status),'<button onclick="incidentForm('+r.id+')">Manage</button>']));
      html+='<p>Closing an incident requires resolved payment evidence. Notes and ownership changes are audited.</p><div id="opform"></div>';
    }
    if(tab==='preferences')html+=formHtml('Save preferences',[['customer_id','Customer ID'],['channel','Channel','select','in_app,email,webhook'],['support','Support messages','select','true,false'],['marketing','Retention messages','select','false,true'],['destination','Destination (optional)']], 'preferences');
    if(tab==='complaints')html+=formHtml('Record complaint',[['customer_id','Customer ID'],['text','Complaint text']], 'complaint')+table(['ID','Customer','Category','Sentiment','Status',''],data.data.map(r=>[r.id,esc(r.customer_id),esc(r.category),esc(r.sentiment),esc(r.status),r.status==='open'?'<button onclick="simplePost(\'complaints/'+r.id+'/resolve\')">Resolve</button>':'']));
    if(tab==='servicing')html+='<p>Overdue settlements: '+data.overdue_settlements+' · Open refunds: '+data.open_refunds+'</p>'+formHtml('Record settlement',[['id','Settlement ID'],['transaction_id','Original transaction ID'],['amount_minor','Amount (paisa)','number'],['due_at','Due at (ISO timestamp)'],['settled_at','Settled at (optional)']], 'settlement')+formHtml('Update refund',[['id','Refund ID'],['transaction_id','Original transaction ID'],['amount_minor','Amount (paisa)','number'],['status','Status','select','requested,approved,completed,rejected']], 'refund');
    if(tab==='batch')html+='<p>Scala + Apache Spark processes event exports into engagement, reliability and partitioned Parquet datasets. Replicated benchmark data never replaces the live demo metrics.</p><button onclick="simplePost(\'batch/run\')">Run Spark analytics</button><p>Latest status: '+esc(data.status)+(data.stale?' · New events arrived; refresh required':'')+'</p>'+table(['Run','Status','Input rows','Finished'],data.runs.map(r=>[esc(r.id),esc(r.status),r.input_rows,esc(r.finished_at)]));
    if(tab==='batch' && data.metrics){
      html+='<h3>Customer engagement · latest published batch</h3>'+table(['Customer','Payments / 30d','Previous 30d','Value (BDT)','Active days','Segments'],data.metrics.filter(x=>x.entity_type==='customer').map(x=>{const r=x.metrics;return [esc(x.entity_id),r.frequency_30d,r.frequency_previous_30d,(r.monetary_minor_30d/100).toFixed(2),r.active_days_30d,esc(r.segment)]}));
    }
    if(tab==='experiments')html+='<p>Control customers are held out of retention outreach. Results require matured follow-up and do not establish real uplift for simulated messages.</p>'+formHtml('Create holdout experiment',[['id','Experiment ID'],['name','Name'],['customer_ids','Customer IDs (comma separated)'],['horizon_days','Follow-up days','number']], 'experiment');
    if(tab==='governance'){
      html+='<p>'+esc(data.evaluation_status)+'</p><button onclick="simplePost(\'models/retrain\')">Train candidate models</button>'+formHtml('Promote reviewed candidate',[['id','Candidate ID']], 'promote');
      const candidates=await api('models/candidates');html+=table(['Candidate','Status'],candidates.data.map(x=>[esc(x.id),esc(x.status)]));
    }
    if(tab==='deliveries')html+='<p>Queued messages are rechecked for eligibility. The default transport simulates delivery. External delivery requires configured adapters and verified allowlisted destinations.</p><button onclick="simplePost(\'deliveries/dispatch\')">Process outbox</button>'+table(['ID','Channel','Status','Attempts','Error'],data.data.map(r=>[r.id,esc(r.channel),esc(r.status),r.attempts,esc(r.last_error)]));
    html+='<details><summary>Evidence and complete records</summary><pre>'+esc(JSON.stringify(data,null,2))+'</pre></details></section>';
    $('content').innerHTML=html;
  }catch(e){$('content').innerHTML='<p class="error">'+esc(e.message)+'</p>'}
};
function formHtml(title,fields,kind){return '<form onsubmit="submitOperation(event,\''+kind+'\')"><h3>'+esc(title)+'</h3>'+fields.map(([name,label,type,choices])=>'<label style="display:inline-block;margin:6px">'+esc(label)+'<br>'+(type==='select'?'<select name="'+name+'">'+choices.split(',').map(x=>'<option>'+esc(x)+'</option>').join('')+'</select>':'<input name="'+name+'" type="'+(type||'text')+'">')+'</label>').join('')+'<button type="submit">'+esc(title)+'</button></form>'}
function incidentForm(id){$('opform').innerHTML=formHtml('Update incident '+id,[['status','Status','select','investigating,escalated,resolved,open'],['owner','Owner'],['note','Reason / investigation note']], 'incident-'+id)}
async function submitOperation(e,kind){e.preventDefault();const d=Object.fromEntries(new FormData(e.target));let path,body=d;
 if(kind==='preferences'){path='customers/'+encodeURIComponent(d.customer_id)+'/preferences';body={channel:d.channel,support:d.support==='true',marketing:d.marketing==='true',destination:d.destination||null}}
 if(kind==='complaint')path='complaints';
 if(kind==='settlement'){path='settlements';body={...d,amount_minor:Number(d.amount_minor),settled_at:d.settled_at||null}}
 if(kind==='refund'){path='refunds';body={...d,amount_minor:Number(d.amount_minor)}}
 if(kind==='experiment'){path='experiments';body={...d,customer_ids:d.customer_ids.split(',').map(x=>x.trim()).filter(Boolean),horizon_days:Number(d.horizon_days)||30}}
 if(kind==='promote'){path='models/candidates/'+encodeURIComponent(d.id)+'/promote';body={}}
 if(kind.startsWith('incident-'))path='incidents/'+kind.slice(9);
 try{modal(await api(path,body));await load()}catch(err){modal({error:err.message})}}
async function simplePost(path){try{modal(await api(path,{}));await load()}catch(e){modal({error:e.message})}}
async function dismissAction(id){const reason=prompt('Why should this recommendation be dismissed for seven days?');if(!reason)return;try{modal(await api('actions/'+id+'/override',{reason,days:7}));await load()}catch(e){modal({error:e.message})}}
async function signIn(e){e.preventDefault();const token=$('operator-token').value.trim();sessionStorage.setItem('careflow-token',token);try{const user=await api('session');$('login').hidden=true;$('operator').textContent=user.name+' · '+user.role;await load()}catch(e){sessionStorage.removeItem('careflow-token');$('login-error').textContent=e.message}}
function signOut(){sessionStorage.removeItem('careflow-token');location.reload()}
if(sessionStorage.getItem('careflow-token')){api('session').then(user=>{$('login').hidden=true;$('operator').textContent=user.name+' · '+user.role;load()}).catch(()=>{$('login').hidden=false})}
