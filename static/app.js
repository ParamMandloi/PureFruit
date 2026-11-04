(() => {
  'use strict';
  const form=document.getElementById('order-form');
  if(!form) return;
  const plans={5:599,10:1099,20:1999};
  const slots={morning:'8 – 9 AM',afternoon:'4 – 5 PM'};
  const ready=document.body.dataset.paymentsReady==='true';
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
  if(!ready){submit.disabled=true;submit.textContent='Online checkout coming soon';}
  form.addEventListener('submit',async event=>{
    event.preventDefault();error.hidden=true;success.hidden=true;
    if(!ready){showError('Online checkout is not yet enabled.');return;}
    if(!window.Razorpay){showError('Payment checkout could not load. Please check your internet connection and try again.');return;}
    if(!form.reportValidity())return;
    const date=startField.value;
    if(!date||date<minStart||[0,6].includes(new Date(date+'T12:00:00').getDay())){showError('Choose a weekday starting '+minStart+' or later.');return;}
    const data={name:form.elements.name.value.trim(),phone:form.elements.phone.value.trim(),tower:form.elements.tower.value.trim(),flat:form.elements.flat.value.trim(),plan_days:Number(form.elements.plan_days.value),delivery_slot:form.elements.delivery_slot.value,start_date:date,website:form.elements.website.value};
    if(!/^[6-9]\d{9}$/.test(data.phone)){showError('Enter a valid 10-digit Indian mobile number.');return;}
    submit.disabled=true;submit.textContent='Opening secure checkout…';
    let result;
    try{result=await jsonPost('/api/orders',data);}
    catch(err){showError(err.message);submit.disabled=false;submit.textContent='Pay securely ↗';return;}
    const ref=result.reference;
    const options={
      key:result.key_id, amount:result.amount, currency:'INR', order_id:result.razorpay_order_id,
      name:'PureFruit', description:`${data.plan_days}-day fresh juice subscription`,
      prefill:{name:data.name,contact:data.phone},
      theme:{color:'#565656'},
      modal:{ondismiss:()=>{submit.disabled=false;submit.textContent='Pay securely ↗';showError('Checkout closed. No order is confirmed. If money was debited, contact PureFruit before paying again.');}},
      handler:async payment=>{
        submit.textContent='Verifying payment…';
        try{
          const verified=await jsonPost(`/api/orders/${encodeURIComponent(ref)}/verify`,payment);
          if(verified.status==='paid'){
            form.reset();updateChoices();
            submit.textContent='Payment complete';
            showSuccess(`Payment confirmed! Your PureFruit subscription reference is ${ref}. Please save this number.`);
            return;
          }
          showSuccess(`Payment is processing for ${ref}. It is not yet confirmed. Please check with PureFruit if the bank debited your account; do not pay twice.`);
        }catch(err){
          showError(`We could not confirm payment for ${ref}. If money was debited, please contact PureFruit before trying again. ${err.message}`);
        }
        submit.disabled=false;submit.textContent='Pay securely ↗';
      }
    };
    try{
      const checkout=new window.Razorpay(options);
      checkout.on('payment.failed',response=>{
        submit.disabled=false;submit.textContent='Pay securely ↗';
        showError((response.error?.description||'Payment failed.')+' If money was debited, contact PureFruit before retrying.');
      });
      checkout.open();
    }catch(err){submit.disabled=false;submit.textContent='Pay securely ↗';showError('Unable to open payment checkout. Please try again.');}
  });
})();
