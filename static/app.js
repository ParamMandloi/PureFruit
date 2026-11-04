(() => {
  'use strict';
  const form = document.getElementById('order-form');
  if (!form) return;
  const plans = {5:599,10:1099,20:1999};
  const slots = {morning:'8 – 9 AM', afternoon:'4 – 5 PM'};
  const minLaunch = '2026-11-06';
  const preview = document.body.dataset.preview === 'true';

  function localISO(date) {
    const y=date.getFullYear(),m=String(date.getMonth()+1).padStart(2,'0'),d=String(date.getDate()).padStart(2,'0');
    return `${y}-${m}-${d}`;
  }
  function nextWeekday(d) {
    const v=new Date(d.getFullYear(),d.getMonth(),d.getDate());
    while(v.getDay()===0 || v.getDay()===6) v.setDate(v.getDate()+1);
    return v;
  }
  const now = new Date();
  const startField = form.elements.start_date;
  const first = new Date(Math.max(new Date(minLaunch+'T12:00:00').getTime(), new Date(now.getFullYear(),now.getMonth(),now.getDate(),12).getTime()));
  const minStart=localISO(nextWeekday(first));
  startField.min=minStart;
  if(!startField.value || startField.value<minStart) startField.value=minStart;

  function updateChoices(){
    form.querySelectorAll('.plan-card').forEach(el=>el.classList.toggle('is-selected',el.querySelector('input').checked));
    form.querySelectorAll('.slot-card').forEach(el=>el.classList.toggle('is-selected',el.querySelector('input').checked));
    const days=Number(form.elements.plan_days.value);
    document.getElementById('summary-plan').textContent=`${days} delivery days`;
    document.getElementById('summary-price').textContent=`₹${plans[days].toLocaleString('en-IN')}`;
    document.getElementById('summary-slot').textContent=slots[form.elements.delivery_slot.value];
  }
  form.addEventListener('change',updateChoices); updateChoices();

  function showError(message){const div=document.getElementById('form-error');div.textContent=message;div.hidden=false;div.scrollIntoView({block:'nearest',behavior:'smooth'});}
  function whatsappMessage(data,ref){
    const date=new Date(data.start_date+'T12:00:00');
    const readable=date.toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'});
    return ['Hello PureFruit! I would like to request a juice subscription.','',`Reference: ${ref}`,`Plan: ${data.plan_days} delivery days – ₹${plans[data.plan_days].toLocaleString('en-IN')}`,`Delivery: ${data.delivery_slot==='morning'?'Morning (8–9 AM)':'Afternoon (4–5 PM)'}`,`Starting: ${readable}`,`Name: ${data.name}`,`Tower: ${data.tower}`,`Flat: ${data.flat}`,`Phone: ${data.phone}`,'','Please confirm availability and payment instructions.'].join('\n');
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const err=document.getElementById('form-error');err.hidden=true;
    const success=document.getElementById('form-success');success.hidden=true;
    const dateVal=startField.value;
    if(!form.reportValidity()) return;
    if(!/^\d{10}$/.test(form.elements.phone.value.replace(/\D/g,''))){showError('Enter a valid 10-digit Indian mobile number.');return;}
    if(!dateVal || dateVal<minStart || [0,6].includes(new Date(dateVal+'T12:00:00').getDay())){showError('Choose a weekday start date from '+minStart+' onwards.');return;}
    const data={name:form.elements.name.value.trim(),phone:form.elements.phone.value.trim(),tower:form.elements.tower.value.trim(),flat:form.elements.flat.value.trim(),plan_days:Number(form.elements.plan_days.value),delivery_slot:form.elements.delivery_slot.value,start_date:dateVal,website:form.elements.website.value};
    const button=document.getElementById('submit-order');button.disabled=true;button.textContent='Preparing your WhatsApp message…';
    try{
      let ref,url;
      if(preview){
        ref='PREVIEW';
        url='https://wa.me/918074062979?text='+encodeURIComponent(whatsappMessage(data,ref));
      } else {
        const response=await fetch('/api/orders',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},credentials:'same-origin',body:JSON.stringify(data)});
        const result=await response.json();
        if(!response.ok) throw new Error(result.error || 'Could not save your request. Please try again.');
        ref=result.reference;url=result.whatsapp_url;
      }
      const a=document.createElement('a');a.href=url;a.rel='noopener noreferrer';a.textContent='Open WhatsApp to send your request';
      success.replaceChildren(document.createTextNode(`Request reference: ${ref}. `),a);
      success.hidden=false;
      window.location.assign(url);
    }catch(error){showError(error.message || 'Something went wrong. Please try again.');}
    finally{button.disabled=false;button.innerHTML='Continue to WhatsApp <span aria-hidden="true">↗</span>';}
  });
})();
