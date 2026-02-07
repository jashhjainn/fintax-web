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
    }catch(err){ 
      console.warn('include error', err); 
      const id = el.getAttribute('id') || '';
      if(id === 'siteNav'){
        el.innerHTML = '<nav class="bottom-nav" role="navigation" aria-label="Primary"><a class="nav-item" href="index.html"><i class="fa-solid fa-house" aria-hidden="true"></i><span>Home</span></a><a class="nav-item" href="upload.html"><i class="fa-solid fa-file-arrow-up" aria-hidden="true"></i><span>Upload</span></a><a class="nav-item" href="ledger.html"><i class="fa-solid fa-receipt" aria-hidden="true"></i><span>Ledger</span></a><a class="nav-item" href="profile.html"><i class="fa-solid fa-user" aria-hidden="true"></i><span>Profile</span></a></nav>';
      } else if(id === 'siteHeader'){
        el.innerHTML = '<header class="app-header" role="banner"><div class="brand"><a href="index.html" class="brand-link">FINTAX</a></div><div class="header-actions"><button type="button" id="themeToggle" aria-pressed="false" aria-label="Toggle theme" title="Toggle theme"><i class="fa-solid fa-moon" aria-hidden="true"></i></button></div><div id="globalStatus" class="visually-hidden" aria-live="polite" aria-atomic="true"></div></header>';
      }
    }
  }));
}

let currentStream = null;
let capturedFile = null; // Holds the file object from a camera capture

// --- API base resolution (robust local dev) ---
const DEFAULT_API_BASES = [
  'http://127.0.0.1:8000',
  'http://localhost:8000',
  (location && location.hostname) ? `http://${location.hostname}:8000` : null
].filter(Boolean);

let cachedApiBase = null;

function withTimeout(promise, ms){
  const ctrl = new AbortController();
  const timer = setTimeout(()=> ctrl.abort(), ms);
  return promise(ctrl).finally(()=> clearTimeout(timer));
}

async function resolveApiBase(){
  if(cachedApiBase) return cachedApiBase;
  const override = (window.API_BASE || (function(){ try{ return localStorage.getItem('apiBase'); }catch(e){ return null; } })());
  const bases = override ? [override, ...DEFAULT_API_BASES.filter(b=>b!==override)] : DEFAULT_API_BASES;

  for(const base of bases){
    try{
      const ok = await withTimeout(
        (ctrl)=> fetch(`${base}/`, { method:'GET', cache:'no-store', signal: ctrl.signal }),
        1500
      );
      if(ok && ok.ok){
        cachedApiBase = base;
        return base;
      }
    }catch(e){}
  }
  throw new Error('Backend not reachable at port 8000. Start the server and retry.');
}

function announce(text){
  const messageEl = document.getElementById('message');
  if(messageEl) messageEl.textContent = text;
  const global = document.getElementById('globalStatus');
  if(global) global.textContent = text;
}

function ensureToastContainer(){
  let container = document.getElementById('toastContainer');
  if(!container){
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  return container;
}

function showToast(message, variant = 'success', duration = 1200){
  const container = ensureToastContainer();
  const toast = document.createElement('div');
  toast.className = `toast toast-${variant}`;
  toast.setAttribute('role', 'status');
  toast.setAttribute('aria-live', 'polite');
  toast.textContent = message;
  container.appendChild(toast);
  requestAnimationFrame(()=> toast.classList.add('show'));
  setTimeout(()=>{
    toast.classList.remove('show');
    setTimeout(()=> toast.remove(), 220);
  }, duration);
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
  const previewRetake = document.getElementById('previewRetake');

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

    // Convert dataURL to File object
    try {
      const blob = await (await fetch(data)).blob();
      capturedFile = new File([blob], `invoice-${Date.now()}.png`, { type: blob.type });
      // Clear file input to avoid confusion
      if(fileInput) fileInput.value = '';
    } catch (e) {
      console.error("Error converting captured image to file:", e);
      announce("Could not process captured image.");
      return;
    }

    // set preview (main area)
    if(previewThumb){ previewThumb.src = data; previewThumb.hidden = false; }
    if(previewName) previewName.textContent = capturedFile.name;
    if(previewDownload){ previewDownload.href = data; previewDownload.hidden = false; previewDownload.setAttribute('download', capturedFile.name || 'invoice.png'); }
    if(document.getElementById('filePreview')){ const fp = document.getElementById('filePreview'); fp.hidden = false; fp.setAttribute('aria-hidden','false'); }
    // ensure upload button inside preview is visible
    const upBtn = document.getElementById('uploadBtn'); if(upBtn) upBtn.hidden = false;
    const cancelBtn = document.getElementById('cancelPreview'); if(cancelBtn) cancelBtn.hidden = false;

    // close modal and remove focus trap and show initial layout with preview visible
    cameraModal.setAttribute('aria-hidden','true'); cameraCard.setAttribute('aria-hidden','true'); document.body.classList.remove('no-scroll'); document.body.classList.remove('camera-open');
    if(removeFocusTrap){ removeFocusTrap(); removeFocusTrap = null; }
    if(uploadSimple) uploadSimple.hidden = false;
    if(openCameraBtn) openCameraBtn.focus();
    announce('Photo captured. Ready to upload.');
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
  if(fileInput){ fileInput.addEventListener('change', (e)=>{ const file = e.target.files && e.target.files[0]; if(!file) return;
      capturedFile = null; // Clear captured file if user selects a new one
      const url = URL.createObjectURL(file); 
      if(previewThumb){ previewThumb.src = url; previewThumb.hidden = false; } 
      if(previewName) previewName.textContent = file.name; 
      if(previewDownload){ previewDownload.href = url; previewDownload.hidden = false; previewDownload.setAttribute('download', file.name || 'invoice.png'); }
      const fp = document.getElementById('filePreview'); if(fp){ fp.hidden = false; fp.setAttribute('aria-hidden','false'); }
      // show upload and cancel
      const upBtn = document.getElementById('uploadBtn'); if(upBtn) upBtn.hidden = false; const cancelBtn = document.getElementById('cancelPreview'); if(cancelBtn) cancelBtn.hidden = false;
      announce('Image ready'); }); }

  // choose file button triggers input
  if(chooseBtn){ chooseBtn.addEventListener('click', ()=> fileInput?.click()); }

  // cancel preview
  const cancelPreview = document.getElementById('cancelPreview'); if(cancelPreview){ cancelPreview.addEventListener('click', ()=>{
      const fp = document.getElementById('filePreview'); if(fp){ fp.hidden = true; fp.setAttribute('aria-hidden','true'); }
      if(previewThumb) { previewThumb.hidden = true; previewThumb.src = ''; }
      if(previewDownload){ previewDownload.hidden = true; previewDownload.removeAttribute('href'); }
      if(previewName) previewName.textContent = '';
      // Clear state
      if(fileInput) fileInput.value = '';
      capturedFile = null;
  }); }

  // Retake from preview: reopen camera if captured, else re-open file picker
  if(previewRetake){
    previewRetake.addEventListener('click', ()=>{
      if(capturedFile){
        if(cameraModal && cameraCard){
          cameraModal.setAttribute('aria-hidden','false');
          cameraCard.setAttribute('aria-hidden','false');
          document.body.classList.add('no-scroll');
          startStream(videoEl, cameraSelect?.value);
        }
      } else {
        fileInput?.click();
      }
    });
  }

  // upload button in preview triggers actual upload to FastAPI
  const uploadBtnPreview = document.getElementById('uploadBtn'); if(uploadBtnPreview){ uploadBtnPreview.addEventListener('click', async ()=>{
      let fileToUpload = null;

      if (capturedFile) {
        fileToUpload = capturedFile;
      } else if (fileInput && fileInput.files && fileInput.files[0]) {
        fileToUpload = fileInput.files[0];
      }

      if(!fileToUpload) {
        announce('Please select or capture a file first');
        return;
      }
      
      const formData = new FormData();
      formData.append('file', fileToUpload);

      // Show overlay
      const progressOverlay = document.querySelector('.progress-overlay'); 
      const progressFillLocal = document.getElementById('uploadProgress'); 
      const progressText = document.getElementById('progressText');
      
      if(!progressOverlay) {
          announce('Uploading... Please wait.');
          uploadBtnPreview.disabled = true;
          uploadBtnPreview.textContent = 'Uploading...';
      } else {
        progressOverlay.hidden = false; 
        progressOverlay.setAttribute('aria-hidden','false'); 
        if(progressFillLocal) progressFillLocal.style.width = '50%'; 
        if(progressText) progressText.textContent = 'Uploading to Server…'; 
      }

      try {
        const apiBase = await resolveApiBase();
        // INFO: OCR + store to ledger
        const response = await fetch(`${apiBase}/ocr/`, {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const result = await response.json();
            if(progressOverlay) {
                if(progressFillLocal) progressFillLocal.style.width = '100%';
                setTimeout(() => { progressOverlay.hidden = true; }, 500);
            }
            announce('OCR complete: ' + result.id);
            try{
              if(result && result.ledger){
                localStorage.setItem('ledgerLastId', result.id);
                localStorage.setItem('ledgerLastData', JSON.stringify(result.ledger));
              }
            }catch(e){}
            alert(`✅ OCR Processed Successfully!\nID: ${result.id}`);
            window.location.href = 'ledger.html';
        } else {
            let errorDetail = 'Upload failed';
            try {
                const err = await response.json();
                errorDetail = err.detail || errorDetail;
            } catch(e){}
            throw new Error(errorDetail);
        }
      } catch (error) {
          console.error('Error uploading:', error);
          if(progressOverlay) progressOverlay.hidden = true;
          announce('Upload failed');
          alert('❌ Upload failed:\n' + error.message + '\n\nMake sure the backend server is running on port 8000.');
      } finally {
          if(!progressOverlay) {
             uploadBtnPreview.disabled = false;
             uploadBtnPreview.innerHTML = '<i class="fa-solid fa-cloud-arrow-up" aria-hidden="true"></i> Upload';
          }
          // Reset state after upload
          if(fileInput) fileInput.value = '';
          capturedFile = null;
          const fp = document.getElementById('filePreview'); if(fp){ fp.hidden = true; fp.setAttribute('aria-hidden','true'); }
      }
  }); }

  // Ledger page: render OCR result
  const ledgerRoot = document.getElementById('ledgerRoot');
  if(ledgerRoot){
    const statusEl = document.getElementById('ledgerStatus');
    const ledgerModal = document.getElementById('ledgerImageModal');
    const ledgerModalImg = document.getElementById('ledgerImageFull');
    const ledgerModalClose = document.getElementById('ledgerImageClose');

    function formatLines(lines){
      if(!lines || !lines.length) return '<p class="subtitle">No text lines detected.</p>';
      const items = lines.slice(0, 50).map(l=> `<li>${l}</li>`).join('');
      return `<ul>${items}</ul>`;
    }

    function extractLedgerRows(data){
      const lines = (data && data.lines) ? data.lines : [];
      const textBlob = (data && data.cleaned_text) ? data.cleaned_text : '';
      const moneyPattern = /\s*([0-9,]+(?:\.\d{2})?)/g;

      function parseAmountToken(token){
        if(!token) return null;
        const cleaned = token.replace(/[, ]+/g, '').replace(/[^\d.]/g, '');
        if(!cleaned) return null;
        const val = Number(cleaned);
        return Number.isFinite(val) ? val : null;
      }

      function formatAmount(val){
        if(val == null) return 'Not detected';
        try{
          return `₹ ${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        }catch(e){
          return `₹ ${val.toFixed(2)}`;
        }
      }

      function lastAmountInLine(ln){
        let match = null;
        for(const m of ln.matchAll(moneyPattern)){ match = m; }
        return match ? parseAmountToken(match[1]) : null;
      }

      function findAmountByKeyword(keywordRe){
        for(const ln of lines){
          if(keywordRe.test(ln)){
            const amount = lastAmountInLine(ln);
            if(amount != null) return amount;
          }
        }
        return null;
      }

      function findBillNo(){
        for(const ln of lines){
          const m = ln.match(/(?:bill|invoice)\s*(?:no\.?|number)?\s*[:#-]?\s*([A-Z0-9-]+)/i);
          if(m && m[1]) return m[1].trim();
        }
        // fallback: any token near "no"
        for(const ln of lines){
          if(/no\.?|number/i.test(ln)){
            const t = ln.match(/\b([A-Z0-9-]{4,})\b/);
            if(t && t[1]) return t[1].trim();
          }
        }
        const m = textBlob.match(/(?:bill|invoice)\s*(?:no\.?|number|#|:)\s*([A-Z0-9-]+)/i);
        return m && m[1] ? m[1].trim() : null;
      }
      const billNo = findBillNo() || 'Not detected';

      function findClientName(){
        for(const ln of lines){
          const m = ln.match(/\bclient\b\s*[:\-]\s*(.+)$/i);
          if(m && m[1]) return m[1].trim();
        }
        return null;
      }
      const clientName = findClientName();

      const subtotalAmt = findAmountByKeyword(/\bsub\s*total\b|\bsubtotal\b/i);
      const totalAmtVal = (function(){
        const fromLine = findAmountByKeyword(/\btotal\b|\btotal amount\b|\bgrand total\b|\bamount due\b|\bnet total\b|\bbalance due\b/i);
        if(fromLine != null) return fromLine;
        if(data && data.total_amount) return parseAmountToken(data.total_amount);
        return null;
      })();

      let gstAmtVal = null;
      if(totalAmtVal != null && subtotalAmt != null && totalAmtVal >= subtotalAmt){
        gstAmtVal = Number((totalAmtVal - subtotalAmt).toFixed(2));
      } else if(totalAmtVal != null){
        // If total includes GST and subtotal is missing, derive GST from total (18% default)
        const base = totalAmtVal / 1.18;
        gstAmtVal = Number((totalAmtVal - base).toFixed(2));
      } else {
        const gstMatch = (textBlob.match(/(?:gst|tax)\s*(?:amt|amount|:)\s*([0-9,]+(?:\.\d{2})?)/i) || [])[1];
        gstAmtVal = gstMatch ? parseAmountToken(gstMatch) : null;
      }

      const gstAmt = formatAmount(gstAmtVal);
      const totalAmt = totalAmtVal != null ? formatAmount(totalAmtVal) : (data && data.total_amount ? data.total_amount : 'Not detected');

      function findParticulars(){
        for(const ln of lines){
          const clean = ln.replace(/\s+/g, ' ').trim();
          if(clean.length < 3) continue;
          if(/invoice|bill|date|gst|tax|total|amount|balance/i.test(clean)) continue;
          if(/\bclient\b/i.test(clean)) continue;
          if(/[A-Za-z]/.test(clean)) return clean;
        }
        const vendor = data && data.vendor ? data.vendor : null;
        if(vendor) return vendor.toString().replace(/\s+/g, ' ').trim();
        return 'Unknown';
      }
      let particulars = findParticulars();
      if(clientName && !particulars.toLowerCase().includes(clientName.toLowerCase())){
        particulars = `${particulars} (Client: ${clientName})`;
      }

      return {
        id: data && (data.id || data._id) ? (data.id || data._id) : '',
        billNo,
        particulars,
        gstAmt,
        totalAmt
      };
    }

    function normalizeLedgerItems(data){
      if(!data) return [];
      if(Array.isArray(data)) return data;
      if(data.items && Array.isArray(data.items)) return data.items;
      return [data];
    }

    function renderLedger(data, apiBase){
      const items = normalizeLedgerItems(data);
      if(!items.length){
        if(statusEl) statusEl.textContent = 'No OCR data found.';
        return;
      }
      if(statusEl) statusEl.textContent = `Showing ${items.length} ledger entr${items.length === 1 ? 'y' : 'ies'}.`;

      const rows = items.map(extractLedgerRows);
      const tbody = document.getElementById('ledgerBody');
      if(tbody){
        const base = apiBase || cachedApiBase || DEFAULT_API_BASES[0] || '';
        tbody.innerHTML = rows.map((r, i)=>`
          <tr>
            <td class="col-preview">${r.id ? `<img class="ledger-preview" src="${base}/ledger/${r.id}/image" alt="Invoice preview">` : 'No image'}</td>
            <td>${i + 1}</td>
            <td>${r.billNo}</td>
            <td>${r.particulars}</td>
            <td>${r.gstAmt}</td>
            <td>${r.totalAmt}</td>
          </tr>
        `).join('');
      }
    }

    function openLedgerPreview(src){
      if(!ledgerModal || !ledgerModalImg) return;
      ledgerModalImg.src = src;
      ledgerModal.setAttribute('aria-hidden', 'false');
    }

    function closeLedgerPreview(){
      if(!ledgerModal || !ledgerModalImg) return;
      ledgerModal.setAttribute('aria-hidden', 'true');
      ledgerModalImg.removeAttribute('src');
    }

    if(ledgerModalClose){
      ledgerModalClose.addEventListener('click', closeLedgerPreview);
    }
    if(ledgerModal){
      ledgerModal.addEventListener('click', (e)=>{ if(e.target === ledgerModal) closeLedgerPreview(); });
      document.addEventListener('keydown', (e)=>{ if(e.key === 'Escape' && ledgerModal.getAttribute('aria-hidden') === 'false') closeLedgerPreview(); });
    }
    const ledgerBody = document.getElementById('ledgerBody');
    if(ledgerBody){
      ledgerBody.addEventListener('click', (e)=>{
        const img = e.target && e.target.closest ? e.target.closest('img.ledger-preview') : null;
        if(img && img.getAttribute('src')){ openLedgerPreview(img.getAttribute('src')); return; }
      });
    }

    (async ()=>{
      try{
        let data = null;
        let apiBase = null;
        try{
          const cachedList = localStorage.getItem('ledgerListData');
          if(cachedList) data = JSON.parse(cachedList);
        }catch(e){}
        if(!data){
          try{
            apiBase = await resolveApiBase();
            const resList = await fetch(`${apiBase}/ledger/list?limit=50`);
            if(resList.ok) data = await resList.json();
          }catch(e){}
        }
        if(!data){
          try{
            const cached = localStorage.getItem('ledgerLastData');
            if(cached) data = JSON.parse(cached);
          }catch(e){}
        }
        if(!data){
          apiBase = await resolveApiBase();
          const lastId = localStorage.getItem('ledgerLastId');
          const url = lastId ? `${apiBase}/ledger/${lastId}` : `${apiBase}/ledger/latest`;
          const res = await fetch(url);
          if(res.ok) data = await res.json();
        }
        try{
          if(data && data.items) localStorage.setItem('ledgerListData', JSON.stringify(data.items));
          else if(Array.isArray(data)) localStorage.setItem('ledgerListData', JSON.stringify(data));
        }catch(e){}
        renderLedger(data, apiBase);
      }catch(e){
        if(statusEl) statusEl.textContent = 'Unable to load ledger data.';
      }
    })();
  }

  // Signup form handling
  const signupForm = document.getElementById('signupForm');
  if(signupForm){
    signupForm.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const name = (document.getElementById('signupName')?.value || '').trim();
      const email = (document.getElementById('signupEmail')?.value || '').trim().toLowerCase();
      const password = document.getElementById('signupPassword')?.value || '';
      const confirm = document.getElementById('signupConfirmPassword')?.value || '';

      if(!name){ alert('Name is required'); return; }
      if(!email.endsWith('@gmail.com')){ alert('Email must be @gmail.com'); return; }
      if(password.length < 6){ alert('Password must be at least 6 characters'); return; }
      if(password !== confirm){ alert('Passwords do not match'); return; }

      try{
        const apiBase = await resolveApiBase();
        const res = await fetch(`${apiBase}/signup/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, email, password, confirm_password: confirm })
        });
        if(!res.ok){
          const err = await res.json().catch(()=> ({}));
          throw new Error(err.detail || 'Signup failed');
        }
        showToast('✅ Signup successful! Redirecting to login...', 'success', 1500);
        setTimeout(()=> { window.location.href = 'login.html'; }, 1500);
      }catch(err){
        alert('❌ ' + err.message);
      }
    });
  }

  // Login form handling
  const loginForm = document.getElementById('loginForm');
  if(loginForm){
    loginForm.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const email = (document.getElementById('loginEmail')?.value || '').trim().toLowerCase();
      const password = document.getElementById('loginPassword')?.value || '';

      if(!email || !password){ alert('Email and password are required'); return; }
      if(!email.endsWith('@gmail.com')){ alert('Email must be @gmail.com'); return; }

      try{
        const apiBase = await resolveApiBase();
        const res = await fetch(`${apiBase}/login/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password })
        });
        if(!res.ok){
          const err = await res.json().catch(()=> ({}));
          throw new Error(err.detail || 'Login failed');
        }
        showToast('✅ Login successful!', 'success', 2000);
        setTimeout(()=> { window.location.href = 'index.html'; }, 1500);
      }catch(err){
        alert('❌ ' + err.message);
      }
    });
  }

  // auth pages: toggle password visibility
  const eyeButtons = Array.from(document.querySelectorAll('.auth-eye'));
  if(eyeButtons.length){
    eyeButtons.forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const wrapper = btn.closest('.auth-password');
        const input = wrapper ? wrapper.querySelector('input[type="password"], input[type="text"]') : null;
        if(!input) return;
        const isPassword = input.type === 'password';
        input.type = isPassword ? 'text' : 'password';
        btn.setAttribute('aria-pressed', isPassword ? 'true' : 'false');
        btn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
        btn.setAttribute('title', isPassword ? 'Hide password' : 'Show password');
        const icon = btn.querySelector('i');
        if(icon){
          icon.classList.toggle('fa-eye', !isPassword);
          icon.classList.toggle('fa-eye-slash', isPassword);
        }
      });
    });
  }
}


// Run includes first, then initialize UI
(async function start(){ await loadIncludes(); initHeaderNav(); await pageInit(); })();








