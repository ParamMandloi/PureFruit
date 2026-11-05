(() => {
  'use strict';
  const form=document.getElementById('order-form');
  if(!form) return;
  const plans={5:599,10:1099,20:1999};
  const slots={morning:'8 – 9 AM',afternoon:'4 – 5 PM'};
  const submit=document.getElementById('submit-order');
  const csrf=document.querySelector('meta[name="csrf-token"]').content;
  const startField=form.elements.start_date;
  function iso(d){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
  function nextWeekday(d){const t=new Date(d.getFullYear(),d.getMonth(),d.getDate());while([0,6].includes(t.getDay()))t.setDate(t.getDate()+1);return t;}
  // Server provides the actual minimum for India time, so do not override it from device time.
  const minStart=startField.min;
  if(!startField.value||startField.value<minStart) startField.value=minStart;
  function updateChoices(){
    form.querySelectorAll('.plan-card').forEach(e=>e.classList.toggle('is-selected',e.querySelector('input').checked));
    form.querySelectorAll('.slot-card').forEach(e=>e.classList.toggle('is-selected',e.querySelector('input').checked));
    const days=Number(form.elements.plan_days.value);
    document.getElementById('summary-plan').textContent=`${days} delivery days`;
    document.getElementById('summary-price').textContent=`₹${plans[days].toLocaleString('en-IN')}`;
    document.getElementById('summary-slot').textContent=slots[form.elements.delivery_slot.value];
  }
  form.addEventListener('change',updateChoices);updateChoices();
  const error=document.getElementById('form-error');
  const success=document.getElementById('form-success');
  function showError(message){error.textContent=message;error.hidden=false;error.scrollIntoView({block:'nearest',behavior:'smooth'});}
  function showSuccess(message){success.textContent=message;success.hidden=false;success.scrollIntoView({block:'nearest',behavior:'smooth'});}
  async function jsonPost(path,data){
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},credentials:'same-origin',body:JSON.stringify(data)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok&&response.status!==202)throw new Error(result.error||'Something went wrong. Please try again.');
    return result;
  }
  let currentRef=null;
  const qrBox=document.getElementById('upi-checkout');
  const qrPrice=document.getElementById('upi-due');
  const utrForm=document.getElementById('upi-ref-form');
  const utrFeedback=document.getElementById('upi-ref-feedback');
  form.addEventListener('submit',async event=>{
    event.preventDefault();error.hidden=true;success.hidden=true;
    if(currentRef){qrBox.hidden=false;qrBox.scrollIntoView({block:'start',behavior:'smooth'});return;}
    if(!form.reportValidity())return;
    const date=startField.value;
    if(!date||date<minStart||[0,6].includes(new Date(date+'T12:00:00').getDay())){
      showError('Choose a weekday starting '+minStart+' or later.');return;
    }
    const data={name:form.elements.name.value.trim(),phone:form.elements.phone.value.trim(),tower:form.elements.tower.value.trim(),flat:form.elements.flat.value.trim(),plan_days:Number(form.elements.plan_days.value),delivery_slot:form.elements.delivery_slot.value,start_date:date,website:form.elements.website.value};
    if(!/^[6-9]\d{9}$/.test(data.phone)){showError('Enter a valid 10-digit Indian mobile number.');return;}
    submit.disabled=true;submit.textContent='Saving your order…';
    try{
      const result=await jsonPost('/api/upi/orders',data);
      currentRef=result.reference;
      try{sessionStorage.setItem('purefruit_upi_pending',JSON.stringify({ref:currentRef,amount:result.amount}));}catch(e){}
      qrPrice.textContent=`₹${Number(result.amount).toLocaleString('en-IN')}`;
      qrBox.hidden=false;
      showSuccess(`Order ${currentRef} saved, pending bank verification. Pay the exact amount displayed below.`);
      // No changes to customer details once an order reference exists.
      form.querySelectorAll('fieldset input, .invisible-field').forEach(el=>{el.disabled=true;});
      submit.textContent='UPI QR displayed';
      qrBox.scrollIntoView({block:'start',behavior:'smooth'});
    }catch(err){showError(err.message);submit.disabled=false;submit.textContent='Place order & show UPI QR ↗';}
  });
  // Recover the QR if a mobile browser reloads after switching to a UPI app.
  try{
    const pending=JSON.parse(sessionStorage.getItem('purefruit_upi_pending')||'null');
    if(pending && /^PF-[A-Z0-9-]{8,40}$/.test(pending.ref) && [599,1099,1999].includes(Number(pending.amount))){
      currentRef=pending.ref;qrPrice.textContent=`₹${Number(pending.amount).toLocaleString('en-IN')}`;
      qrBox.hidden=false;
      submit.textContent='UPI QR displayed';
      showSuccess(`Your pending order ${currentRef} is saved. Complete payment and submit its reference below.`);
    }
  }catch(e){}
  utrForm.querySelector('input').addEventListener('keydown', e=>{
    if(e.key==='Enter') { e.preventDefault(); document.getElementById('upi-ref-submit').click(); }
  });
  document.getElementById('upi-ref-submit').addEventListener('click',async event=>{
    event.preventDefault();
    if(!currentRef)return;
    const utr=utrForm.elements.upi_reference.value.trim();
    if(!/^[a-z0-9-]{10,40}$/i.test(utr)){
      utrFeedback.textContent='Enter a valid reference shown in your payment app.';return;
    }
    const button=document.getElementById('upi-ref-submit');
    button.disabled=true;button.textContent='Submitting…';
    try{
      await jsonPost(`/api/upi/orders/${encodeURIComponent(currentRef)}/submit`,{upi_reference:utr});
      utrFeedback.textContent=`Reference received for ${currentRef}. Await verification by PureFruit before delivery begins.`;
      utrForm.querySelector('input').disabled=true;
      button.textContent='Reference submitted';
    }catch(err){utrFeedback.textContent=err.message;button.disabled=false;button.textContent='Submit payment reference';}
  });
})();
