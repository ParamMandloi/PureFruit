(() => {
  'use strict';
  const form=document.getElementById('order-form');
  if(!form) return;
  // Prices are always supplied by the India-time server.
  // Refreshing the config handles the 5–6 Nov offer and automatic 7 Nov 00:00 IST expiry.
  let checkoutConfig=JSON.parse(document.getElementById('checkout-config').textContent);
  let plans=checkoutConfig.plans;
  const slots={morning:'8 – 9 AM',afternoon:'4 – 5 PM'};
  const submit=document.getElementById('submit-order');
  const csrf=document.querySelector('meta[name="csrf-token"]').content;
  const startField=form.elements.start_date;
  let minStart=checkoutConfig.min_start;
  function applyCheckoutConfig(config){
    if(currentRef) return; // Existing orders retain the amount on their own UPI QR.
    checkoutConfig=config;
    plans=config.plans;
    minStart=config.min_start;
    // Only weekday dates appear in the mobile-friendly dropdown.
    const previous=startField.value;
    startField.replaceChildren(...config.start_dates.map(opt=>{
      const el=document.createElement('option');el.value=opt.value;el.textContent=opt.label;return el;
    }));
    if(config.start_dates.some(x=>x.value===previous))startField.value=previous;
    const banner=document.getElementById('dhanteras-banner');
    banner.hidden=!config.promo_active;
    document.querySelectorAll('.plan-price').forEach(el=>{
      const days=el.dataset.plan;
      el.textContent=`₹${Number(plans[days]).toLocaleString('en-IN')}`;
    });
    document.getElementById('dhanteras-savings').hidden=!config.promo_active;
    updateChoices();
  }
  async function refreshCheckoutConfig(){
    if(currentRef) return;
    try{
      const response=await fetch('/api/checkout-config',{cache:'no-store',credentials:'same-origin'});
      if(!response.ok) return;
      const config=await response.json();
      applyCheckoutConfig(config);
    }catch(e){ /* Server still verifies the correct price when placing an order. */ }
  }
  function updateChoices(){
    form.querySelectorAll('.plan-card').forEach(e=>e.classList.toggle('is-selected',e.querySelector('input').checked));
    form.querySelectorAll('.slot-card').forEach(e=>e.classList.toggle('is-selected',e.querySelector('input').checked));
    const days=Number(form.elements.plan_days.value);
    document.getElementById('summary-plan').textContent=`${days} delivery days`;
    document.getElementById('summary-price').textContent=`₹${Number(plans[days]).toLocaleString('en-IN')}`;
    document.getElementById('summary-regular-price').textContent=`₹${Number(checkoutConfig.regular_plans[days]).toLocaleString('en-IN')}`;
    document.getElementById('summary-slot').textContent=slots[form.elements.delivery_slot.value];
  }
  form.addEventListener('change',updateChoices);updateChoices();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshCheckoutConfig();});
  window.addEventListener('focus',refreshCheckoutConfig);
  window.setInterval(refreshCheckoutConfig,60000);
  refreshCheckoutConfig();
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
  // A customer click is only a payment *report*, not bank-verified receipt.
  // Keep the order in pending_verification until staff checks PhonePe Business.
  function showPaymentAcknowledgement(ref, verified=false){
    const heading=document.createElement('strong');
    heading.textContent=verified ? 'Payment confirmed — thank you!' : 'Thank you for your payment!';
    const message=document.createElement('p');
    message.textContent=verified
      ? 'Your payment has been verified and your subscription is confirmed. Your team-confirmed delivery schedule is ready.'
      : 'We have received your payment notification. Our team will verify the payment and confirm your delivery details. Once confirmed, your selected delivery schedule will begin. If your preferred start date has passed, we will confirm the next available weekday.';
    const orderId=document.createElement('p');
    orderId.textContent=`PureFruit order reference: ${ref}`;
    success.replaceChildren(heading,message,orderId);
    success.hidden=false;
    qrBox.hidden=true; // No duplicate-payment prompt after reporting.
    success.scrollIntoView({block:'center',behavior:'smooth'});
  }
  function freezeForm(){
    form.querySelectorAll('fieldset input, fieldset select, .invisible-field').forEach(el=>{el.disabled=true;});
    submit.disabled=true;submit.textContent='UPI payment details below';
  }
  async function showPayment(ref){
    const response=await fetch(`/api/upi/orders/${encodeURIComponent(ref)}/payment`,{credentials:'same-origin',cache:'no-store'});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'Unable to load UPI payment details.');
    currentRef=ref;
    orderLabel.textContent=`Order reference: ${ref}`;
    if(data.status==='paid'){
      freezeForm();showPaymentAcknowledgement(ref,true);return;
    }
    if(data.status==='pending_verification'){
      freezeForm();showPaymentAcknowledgement(ref);return;
    }
    paymentIntent=data.intent;
    qrPrice.textContent=`₹${Number(data.amount).toLocaleString('en-IN')}`;
    // Per-order dynamic QR is rendered by our server, never by a third party.
    qrImage.src=data.qr;qrImage.hidden=false;
    saveQr.href=data.qr;saveQr.hidden=false;
    payApp.href=paymentIntent;payApp.hidden=!supportsIntent;
    qrBox.hidden=false;
    freezeForm();
    feedback.textContent='After paying, return here and tap “I’ve completed payment”. No UTR entry required.';
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();error.hidden=true;success.hidden=true;
    if(currentRef){qrBox.hidden=false;qrBox.scrollIntoView({block:'start',behavior:'smooth'});return;}
    // Refresh again at submission time so an open page can't silently keep yesterday's price.
    await refreshCheckoutConfig();
    if(!form.reportValidity())return;
    const date=startField.value;
    if(!date||date<minStart||!checkoutConfig.start_dates.some(x=>x.value===date)){
      showError('Please select a Monday–Friday date from the list.');return;
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
      claimButton.textContent='Payment reported';
      showPaymentAcknowledgement(currentRef);
      // Preserve the order reference during this browsing session so the thank-you
      // message can be recovered after a refresh or return from a UPI app.
    }catch(err){feedback.textContent=err.message;claimButton.disabled=false;claimButton.textContent="I've completed payment";}
  });
  // Never assume returning from the payment app means success.
})();
