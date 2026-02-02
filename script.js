/* App script: include loader + camera/upload logic (runs safely on all pages) */

// Simple include loader for partials (header/nav) using data-include attribute
async function loadIncludes(){
  const els = Array.from(document.querySelectorAll('[data-include]'));
  await Promise.all(els.map(async el => {
    try{
      const url = el.getAttribute('data-include');
      const res = await fetch(url);
      if(!res.ok) throw new Error('Failed to load ' + url);
      const html = await res.text();
      el.innerHTML = html;
    }catch(err){ console.warn('include error', err); }
  }));
}

let currentStream = null;

function announce(text){
  const messageEl = document.getElementById('message');
  if(messageEl) messageEl.textContent = text;
  const global = document.getElementById('globalStatus');
  if(global) global.textContent = text;
}

// Focus management: trap focus inside given container (basic implementation)
function trapFocus(container, onEscape){
  const focusable = container.querySelectorAll('a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])');
  const first = focusable[0]; const last = focusable[focusable.length-1];
  function keyListener(e){
    if(e.key === 'Escape'){ e.preventDefault(); onEscape && onEscape(); }
    if(e.key === 'Tab'){
      if(focusable.length === 0) { e.preventDefault(); return; }
      if(e.shiftKey && document.activeElement === first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey && document.activeElement === last){ e.preventDefault(); first.focus(); }
    }
  }
  document.addEventListener('keydown', keyListener);
  return ()=> document.removeEventListener('keydown', keyListener);
}

async function hasGetUserMedia(){ return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia); }

async function getCameras(selectEl){
  if(!selectEl) return;
  try{
    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoDevices = devices.filter(d=>d.kind==='videoinput');
    selectEl.innerHTML = '';
    if(videoDevices.length===0){ selectEl.style.display = 'none'; const msg = document.getElementById('camMsg'); if(msg){ msg.textContent = 'No cameras found'; msg.setAttribute('aria-hidden','false'); } return; }
    videoDevices.forEach((d,i)=>{
      const opt = document.createElement('option'); opt.value = d.deviceId; opt.text = d.label || `Camera ${i+1}`; selectEl.appendChild(opt);
    });
    selectEl.style.display = '';
    const msg = document.getElementById('camMsg'); if(msg){ msg.textContent = ''; msg.setAttribute('aria-hidden','true'); }
  }catch(e){ console.warn('enumerate devices', e); }
}

async function startStream(videoEl, deviceId){ if(!videoEl) return; stopStream();
  const constraints = { video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }, audio:false };
  try{ currentStream = await navigator.mediaDevices.getUserMedia(constraints); videoEl.srcObject = currentStream; await videoEl.play(); announce('Camera ready'); }
  catch(err){ console.error(err); const camMsg = document.getElementById('camMsg'); if(camMsg){ camMsg.textContent = 'Could not access camera'; camMsg.setAttribute('aria-hidden','false'); }
    announce('Camera unavailable'); }
}

function stopStream(){ if(currentStream){ currentStream.getTracks().forEach(t=>t.stop()); currentStream = null; }}

function captureToCanvas(videoEl, canvasEl){ if(!videoEl || videoEl.readyState === 0) { announce('Video not ready'); return null; }
  canvasEl.width = videoEl.videoWidth || 1280; canvasEl.height = videoEl.videoHeight || 720; const ctx = canvasEl.getContext('2d'); ctx.drawImage(videoEl,0,0,canvasEl.width,canvasEl.height); return canvasEl.toDataURL('image/png');
}

// Initialize header (theme toggle) and nav active state
function initHeaderNav(){
  // theme toggle (persisted Gruvbox themes: 'light' | 'dark')
  const themeToggle = document.getElementById('themeToggle');
  function applyTheme(theme){
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    try{ localStorage.setItem('theme', theme); }catch(e){}
    if(themeToggle){
      themeToggle.setAttribute('aria-pressed', String(theme === 'dark'));
      const icon = themeToggle.querySelector('i');
      if(icon){
        if(theme === 'dark'){ icon.classList.remove('fa-sun'); icon.classList.add('fa-moon'); }
        else { icon.classList.remove('fa-moon'); icon.classList.add('fa-sun'); }
      }
    }
  }

  (function initTheme(){
    const stored = (function(){ try{ return localStorage.getItem('theme'); }catch(e){ return null; } })();
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const initial = stored || (prefersDark ? 'dark' : 'light');
    applyTheme(initial);

    if(themeToggle){
      themeToggle.addEventListener('click', ()=>{
        const cur = document.documentElement.getAttribute('data-theme') || 'light';
        const next = (cur === 'dark' ? 'light' : 'dark');
        // animate icon briefly
        themeToggle.classList.add('toggled'); setTimeout(()=> themeToggle.classList.remove('toggled'), 420);
        applyTheme(next);
      });
    }

    // run contrast check at init and on theme change
    function runContrastChecks(){
      const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
      const text = getComputedStyle(document.documentElement).getPropertyValue('--text').trim();
      const ratio = contrastRatio(hexToRgb(bg), hexToRgb(text));
      if(ratio < 4.5){ console.warn('Low contrast detected between --bg and --text:', ratio.toFixed(2), 'Adjusting muted color for readability'); document.documentElement.style.setProperty('--muted', '#6b5f6d'); }
    }
    // initial check
    runContrastChecks();
    // observe changes to the data-theme attribute
    const obs = new MutationObserver(()=> runContrastChecks()); obs.observe(document.documentElement, { attributes:true, attributeFilter:['data-theme'] });
  })();

  // --- Contrast helpers ---
  function hexToRgb(hex){ if(!hex) return null; hex = hex.replace('#',''); if(hex.length===3) hex = hex.split('').map(c=>c+c).join(''); const int = parseInt(hex,16); return { r:(int>>16)&255, g:(int>>8)&255, b:int&255 }; }
  function luminance(rgb){ if(!rgb) return 0; const srgb = [rgb.r/255, rgb.g/255, rgb.b/255].map(c=> c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4)); return 0.2126*srgb[0] + 0.7152*srgb[1] + 0.0722*srgb[2]; }
  function contrastRatio(a,b){ if(!a||!b) return 1; const L1 = luminance(a); const L2 = luminance(b); const top = Math.max(L1,L2); const bot = Math.min(L1,L2); return (top + 0.05) / (bot + 0.05); }

  // active nav link highlight
  const nav = document.querySelector('#siteNav');
  if(nav){
    const links = Array.from(nav.querySelectorAll('.nav-item'));
    const path = (location.pathname || '').split('/').pop() || 'index.html';
    links.forEach(a=>{
      const href = a.getAttribute('href') || '';
      if(href.includes(path)) { a.classList.add('active'); a.setAttribute('aria-current','true'); } else { a.classList.remove('active'); a.removeAttribute('aria-current'); }
    });
  }
}

// Page init: attach listeners only for present elements
async function pageInit(){
  // camera & upload controls (clean UI)
  const cameraModal = document.getElementById('cameraModal');
  const openCameraBtn = document.getElementById('openCameraBtn');
  const closeCameraBtn = document.getElementById('closeCameraBtn');
  const cameraCard = document.getElementById('cameraCard');
  const videoEl = document.getElementById('video');
  const canvasEl = document.getElementById('canvas');
  const photoEl = document.getElementById('photo');

  const retakeBtn = document.getElementById('retakeBtn');
  const cameraSelect = document.getElementById('cameraSelect');

  const fileInput = document.getElementById('fileInput');
  const chooseBtn = document.getElementById('chooseFileBtn');
  const previewThumb = document.getElementById('previewThumb');
  const previewName = document.getElementById('previewName');
  const previewDownload = document.getElementById('previewDownload');

  const uploadSimple = document.getElementById('uploadSimple');
  let removeFocusTrap = null;

  // Open camera modal and hide the initial two-button layout (full-bleed on mobile)
  if(openCameraBtn && cameraModal && videoEl){
    openCameraBtn.addEventListener('click', async ()=>{
      if(uploadSimple) uploadSimple.hidden = true;
      cameraModal.setAttribute('aria-hidden','false'); cameraCard.setAttribute('aria-hidden','false');
      document.body.classList.add('no-scroll');
      document.body.classList.add('camera-open');
      const camMsgEl = document.getElementById('camMsg');
      const cameraControls = document.querySelector('.camera-controls');
      const camThumb = document.getElementById('camThumb');
      const preview = document.getElementById('filePreview');
      const uploadBtn = document.getElementById('uploadBtn');
      const previewThumbLocal = document.getElementById('previewThumb');
      // reset modal UI and clear any previous previews
      if(camMsgEl){ camMsgEl.textContent = ''; camMsgEl.setAttribute('aria-hidden','true'); }
      if(camThumb){ camThumb.hidden = true; camThumb.setAttribute('aria-hidden','true'); }
      if(preview){ preview.hidden = true; preview.setAttribute('aria-hidden','true'); }
      if(previewThumbLocal){ previewThumbLocal.hidden = true; previewThumbLocal.src = ''; }
      if(uploadBtn){ uploadBtn.hidden = true; }
      if(photoEl){ photoEl.hidden = true; try{ photoEl.removeAttribute('src'); }catch(e){} }
      if(canvasEl){ try{ canvasEl.width = canvasEl.height = 0; const ctx = canvasEl.getContext && canvasEl.getContext('2d'); if(ctx) ctx.clearRect(0,0,canvasEl.width || 0, canvasEl.height || 0); }catch(e){} }
      if(videoEl) videoEl.hidden = false;
      if(cameraControls) cameraControls.hidden = false;

      if(await hasGetUserMedia()){
        try{
          await startStream(videoEl); await getCameras(cameraSelect);
          // focus close button and trap focus inside modal
          if(closeCameraBtn) closeCameraBtn.focus();
          removeFocusTrap = trapFocus(cameraModal, ()=>{ if(closeCameraBtn) closeCameraBtn.click(); });
        }catch(err){
          // show concise inline message and hide video/controls
          if(camMsgEl){ camMsgEl.textContent = 'Could not access camera'; camMsgEl.setAttribute('aria-hidden','false'); }
          if(videoEl) videoEl.hidden = true;
          if(cameraControls) cameraControls.hidden = true;
          if(closeCameraBtn) closeCameraBtn.focus();
          removeFocusTrap = trapFocus(cameraModal, ()=>{ if(closeCameraBtn) closeCameraBtn.click(); });
        }
      } else {
        // no devices available: show minimal inline message
        if(camMsgEl){ camMsgEl.textContent = 'No camera detected'; camMsgEl.setAttribute('aria-hidden','false'); }
        if(videoEl) videoEl.hidden = true;
        if(cameraControls) cameraControls.hidden = true;
        if(closeCameraBtn) closeCameraBtn.focus();
        removeFocusTrap = trapFocus(cameraModal, ()=>{ if(closeCameraBtn) closeCameraBtn.click(); });
      }
    });
  }

  // Close camera modal and restore initial layout
  if(closeCameraBtn && cameraModal){
    closeCameraBtn.addEventListener('click', ()=>{
      cameraModal.setAttribute('aria-hidden','true'); cameraCard.setAttribute('aria-hidden','true'); stopStream();
      document.body.classList.remove('no-scroll'); document.body.classList.remove('camera-open');
      const camThumb = document.getElementById('camThumb'); if(camThumb){ camThumb.hidden = true; camThumb.setAttribute('aria-hidden','true'); }
      if(removeFocusTrap){ removeFocusTrap(); removeFocusTrap = null; }
      if(uploadSimple && openCameraBtn){ uploadSimple.hidden = false; openCameraBtn.focus(); }
    });

  }

  // After capturing, close camera and show preview in the main layout
  // use the shutter control (single capture control) to perform capture
  const shutter = document.querySelector('.camera-shutter');
  async function performCapture(){
    const data = captureToCanvas(videoEl,canvasEl); if(!data) return;
    stopStream();
    // set preview (main area)
    if(previewThumb){ previewThumb.src = data; previewThumb.hidden = false; }
    // hide small modal thumbnail — keep modal unobstructed
    const camThumb = document.getElementById('camThumb'); if(camThumb){ camThumb.hidden = true; camThumb.setAttribute('aria-hidden','true'); }
    // hide any terse label (we don't need "Captured photo")
    if(previewName) previewName.textContent = '';
    if(document.getElementById('filePreview')){ const fp = document.getElementById('filePreview'); fp.hidden = false; fp.setAttribute('aria-hidden','false'); }
    // ensure upload button inside preview is visible
    const upBtn = document.getElementById('uploadBtn'); if(upBtn) upBtn.hidden = false;
    // close modal and remove focus trap and show initial layout with preview visible
    cameraModal.setAttribute('aria-hidden','true'); cameraCard.setAttribute('aria-hidden','true'); document.body.classList.remove('no-scroll'); document.body.classList.remove('camera-open');
    if(removeFocusTrap){ removeFocusTrap(); removeFocusTrap = null; }
    if(uploadSimple) uploadSimple.hidden = false;
    if(openCameraBtn) openCameraBtn.focus();
    announce('Photo captured');
  }
  if(shutter){
    shutter.addEventListener('click', performCapture);
    shutter.addEventListener('keydown', (e)=>{ if(e.key === 'Enter' || e.key === ' ') { e.preventDefault(); performCapture(); } });
  }

  if(retakeBtn){ retakeBtn.addEventListener('click', ()=>{ if(previewThumb) previewThumb.hidden = true; if(previewDownload) previewDownload.hidden = true; const upBtn = document.getElementById('uploadBtn'); if(upBtn) upBtn.hidden = true; if(previewName) previewName.textContent = ''; const camThumb = document.getElementById('camThumb'); if(camThumb){ camThumb.hidden = true; camThumb.setAttribute('aria-hidden','true'); }
    if(cameraModal) { cameraModal.setAttribute('aria-hidden','false'); cameraCard.setAttribute('aria-hidden','false'); document.body.classList.add('no-scroll'); startStream(videoEl, cameraSelect?.value); }
  }); }
  if(cameraSelect && videoEl) cameraSelect.addEventListener('change', ()=> startStream(videoEl, cameraSelect.value));

  // Profile page: animate stat numbers when they come into view
  const statEls = Array.from(document.querySelectorAll('.stat-value'));
  if(statEls && statEls.length){
    const fmt = v => new Intl.NumberFormat('en-IN').format(v);
    function animateNumber(el, to){
      const raw = (''+to).replace(/[^\\d]/g,'');
      const target = Number(raw) || to;
      let start = 0;
      const duration = 900;
      let startTime = null;
      function step(ts){
        if(!startTime) startTime = ts;
        const t = Math.min(1, (ts - startTime) / duration);
        const value = Math.floor(t * target);
        el.textContent = (el.textContent.trim().startsWith('₹') ? '₹ ' : '') + fmt(value);
        if(t < 1) requestAnimationFrame(step);
        else el.textContent = (el.textContent.trim().startsWith('₹') ? '₹ ' : '') + fmt(target);
      }
      requestAnimationFrame(step);
    }

    const obs = new IntersectionObserver((entries, o)=>{
      entries.forEach(en=>{
        if(en.isIntersecting){
          const el = en.target;
          const t = el.getAttribute('data-target') || el.textContent;
          animateNumber(el, t);
          o.unobserve(el);
        }
      });
    }, {threshold:0.5});

    statEls.forEach(el=> obs.observe(el));
  }

  // file input / preview for the simplified flow
  if(fileInput){ fileInput.addEventListener('change', (e)=>{ const file = e.target.files && e.target.files[0]; if(!file) return; const url = URL.createObjectURL(file); if(previewThumb){ previewThumb.src = url; previewThumb.hidden = false; } if(previewName) previewName.textContent = file.name; const fp = document.getElementById('filePreview'); if(fp){ fp.hidden = false; fp.setAttribute('aria-hidden','false'); }
      // show upload and cancel
      const upBtn = document.getElementById('uploadBtn'); if(upBtn) upBtn.hidden = false; const cancelBtn = document.getElementById('cancelPreview'); if(cancelBtn) cancelBtn.hidden = false;
      announce('Image ready'); }); }

  // choose file button triggers input
  if(chooseBtn){ chooseBtn.addEventListener('click', ()=> fileInput?.click()); }

  // cancel preview
  const cancelPreview = document.getElementById('cancelPreview'); if(cancelPreview){ cancelPreview.addEventListener('click', ()=>{ const fp = document.getElementById('filePreview'); if(fp){ fp.hidden = true; fp.setAttribute('aria-hidden','true'); } if(previewThumb) previewThumb.hidden = true; if(previewName) previewName.textContent = ''; }); }

  // upload button in preview triggers simulated upload
  const uploadBtnPreview = document.getElementById('uploadBtn'); if(uploadBtnPreview){ uploadBtnPreview.addEventListener('click', ()=>{ if(!fileInput || !fileInput.files || !fileInput.files[0]) { announce('Please select a file first'); return; }
      // simulated upload progress (reusing overlay if present)
      const progressOverlay = document.querySelector('.progress-overlay'); const progressFillLocal = document.getElementById('uploadProgress'); const progressText = document.getElementById('progressText');
      if(progressOverlay){ progressOverlay.hidden = false; progressOverlay.setAttribute('aria-hidden','false'); if(progressFillLocal) progressFillLocal.style.width = '0%'; if(progressText) progressText.textContent = 'Uploading…'; let pct = 0; const t = setInterval(()=>{ pct += 10; if(progressFillLocal) progressFillLocal.style.width = pct+'%'; if(pct >= 100){ clearInterval(t); setTimeout(()=>{ if(progressOverlay){ progressOverlay.hidden = true; progressOverlay.setAttribute('aria-hidden','true'); } announce('Upload complete'); },400); } }, 150); } }); }
}

// Run includes first, then initialize UI
(async function start(){ await loadIncludes(); initHeaderNav(); await pageInit(); })();
