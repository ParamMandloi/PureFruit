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
  let paymentIntent=null;
  const qrBox=document.getElementById('upi-checkout');
  const qrPrice=document.getElementById('upi-due');
  const qrImage=document.getElementById('upi-qr-image');
  const payApp=document.getElementById('upi-pay-app');
  const saveQr=document.getElementById('upi-save-qr');
  const orderLabel=document.getElementById('upi-order-ref');
  const claimButton=document.getElementById('upi-paid-claim');
  const feedback=document.getElementById('upi-payment-feedback');
  const supportsIntent=/Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
  function freezeForm(){
    form.querySelectorAll('fieldset input, .invisible-field').forEach(el=>{el.disabled=true;});
    submit.disabled=true;submit.textContent='UPI payment details below';
  }
  async function showPayment(ref){
    const response=await fetch(`/api/upi/orders/${encodeURIComponent(ref)}/payment`,{credentials:'same-origin',cache:'no-store'});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'Unable to load UPI payment details.');
    currentRef=ref;
    orderLabel.textContent=`Order reference: ${ref}`;
    if(data.status==='paid'){
      qrBox.hidden=false;qrImage.hidden=true;payApp.hidden=true;saveQr.hidden=true;claimButton.hidden=true;
      feedback.textContent='Payment already verified. Your order is confirmed.';
      freezeForm();return;
    }
    paymentIntent=data.intent;
    qrPrice.textContent=`₹${Number(data.amount).toLocaleString('en-IN')}`;
    // Per-order dynamic QR is rendered by our server, never by a third party.
    qrImage.src=data.qr;qrImage.hidden=false;
    saveQr.href=data.qr;saveQr.hidden=false;
    payApp.href=paymentIntent;payApp.hidden=!supportsIntent;
    qrBox.hidden=false;
    freezeForm();
    feedback.textContent=data.status==='pending_verification'
      ? 'We received your payment report. Staff will verify the actual PhonePe bank credit.'
      : 'After paying, return here and tap “I’ve completed payment”. No UTR entry required.';
    if(data.status==='pending_verification'){
      claimButton.disabled=true;claimButton.textContent='Payment reported';
    }
  }
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
      try{sessionStorage.setItem('purefruit_upi_pending',currentRef);}catch(e){}
      await showPayment(currentRef);
      showSuccess(`Order ${currentRef} saved. Pay ₹${result.amount} using the QR or UPI button below.`);
      qrBox.scrollIntoView({block:'start',behavior:'smooth'});
    }catch(err){showError(err.message);if(!currentRef){submit.disabled=false;submit.textContent='Place order & show amount-filled QR ↗';}}
  });
  // Recover a pending payment when the customer returns from the UPI app.
  try{
    const pending=sessionStorage.getItem('purefruit_upi_pending');
    if(pending&&/^PF-[A-Za-z0-9-]{8,40}$/.test(pending)){
      showPayment(pending).catch(err=>{feedback.textContent=err.message;});
    }
  }catch(e){}
  claimButton.addEventListener('click',async ()=>{
    if(!currentRef)return;
    claimButton.disabled=true;claimButton.textContent='Reporting payment…';
    try{
      await jsonPost(`/api/upi/orders/${encodeURIComponent(currentRef)}/claim`,{});
      feedback.textContent=`Payment reported for ${currentRef}. PureFruit staff will verify the actual bank credit before activating your subscription.`;
      claimButton.textContent='Payment reported';
      try{sessionStorage.removeItem('purefruit_upi_pending');}catch(e){}
    }catch(err){feedback.textContent=err.message;claimButton.disabled=false;claimButton.textContent="I've completed payment";}
  });
  // Never assume returning from the payment app means success.
})();
