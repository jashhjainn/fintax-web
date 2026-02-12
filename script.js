/* App script: include loader + camera/upload logic */

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

function getAuthToken(){
  try{ return localStorage.getItem('authToken'); }catch(e){ return null; }
}

function authHeaders(){
  const token = getAuthToken();
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

function clearAuth(){
  try{
    localStorage.removeItem('authToken');
    localStorage.removeItem('authEmail');
  }catch(e){}
}

function handleAuthError(message){
  clearAuth();
  alert(message || 'Session expired. Please login again.');
  window.location.href = 'login.html';
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

  // header logout button (if present)
  const logoutBtn = document.getElementById('logoutBtn');
  const logoutModal = document.getElementById('logoutModal');
  const confirmLogoutBtn = document.getElementById('confirmLogoutBtn');
  const cancelLogoutBtn = document.getElementById('cancelLogoutBtn');
  const closeLogoutModal = document.getElementById('closeLogoutModal');
  
  function showLogoutModal() {
    if (!logoutModal) return;
    logoutModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
    // Focus the cancel button for accessibility
    if (cancelLogoutBtn) cancelLogoutBtn.focus();
  }
  
  function hideLogoutModal() {
    if (!logoutModal) return;
    logoutModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
  }
  
  if(logoutBtn){
    logoutBtn.addEventListener('click', (e) => {
      e.preventDefault();
      showLogoutModal();
    });
  }
  
  // Confirm logout
  if(confirmLogoutBtn) {
    confirmLogoutBtn.addEventListener('click', () => {
      hideLogoutModal();
      clearAuth();
      window.location.href = 'auth.html';
    });
  }
  
  // Cancel logout
  if(cancelLogoutBtn) {
    cancelLogoutBtn.addEventListener('click', () => {
      hideLogoutModal();
    });
  }
  
  // Close logout modal
  if(closeLogoutModal) {
    closeLogoutModal.addEventListener('click', hideLogoutModal);
  }
  
  // Close logout modal when clicking outside
  if(logoutModal) {
    logoutModal.addEventListener('click', (e) => {
      if (e.target === logoutModal) {
        hideLogoutModal();
      }
    });
  }
  
  // Close logout modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && logoutModal && logoutModal.getAttribute('aria-hidden') === 'false') {
      hideLogoutModal();
    }
  });
}

// Page init: attach listeners only for present elements
async function pageInit(){
  const currentPage = (location.pathname || '').split('/').pop() || 'index.html';
  const authRequiredPages = ['upload.html', 'ledger.html', 'profile.html'];
  if(authRequiredPages.includes(currentPage) && !getAuthToken()){
    window.location.href = 'login.html';
    return;
  }
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

  // Dashboard page: fetch and display financial stats
  const dashboardStats = document.querySelector('.stats');
  if(dashboardStats){
    async function loadDashboardStats(){
      try{
        const apiBase = await resolveApiBase();
        const token = getAuthToken();
        
        console.log('Fetching total sales from:', `${apiBase}/ledger/total-sales`);
        console.log('Auth token present:', !!token);
        
        // Fetch total sales (sum of all invoice amounts for the user)
        const response = await fetch(`${apiBase}/ledger/total-sales`, {
          headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        
        console.log('Response status:', response.status);
        
        if(response.status === 401){
          handleAuthError('Session expired. Please login again.');
          return;
        }
        
        if(response.ok){
          const data = await response.json();
          console.log('API response data:', data);
          
          const totalSales = data.total_sales;
          const invoiceCount = data.invoice_count;
          console.log('Raw total_sales value:', totalSales, 'Type:', typeof totalSales);
          console.log('Invoice count:', invoiceCount);
          
          // Update total sales stat
          const salesStat = document.querySelector('.stat-card--sales .stat-value');
          if(salesStat){
            // Handle different data formats from API with robust type checking
            let numericValue;
            
            if (totalSales === null || totalSales === undefined || totalSales === 0) {
              console.log('totalSales is null/undefined/zero:', totalSales);
              salesStat.textContent = 'No data';
              // Reset the label to default
              const labelStat = document.querySelector('.stat-card--sales .stat-label');
              if(labelStat) {
                labelStat.textContent = 'Total Sales';
              }
              return;
            }
            
            if (typeof totalSales === 'string') {
              // Extract numbers from formatted strings like "₹ 1,234.50"
              const cleanString = totalSales.replace(/[^\d.]/g, '');
              numericValue = parseFloat(cleanString);
              console.log('Parsed from string:', cleanString, '->', numericValue);
            } else if (typeof totalSales === 'number') {
              // Direct number value
              numericValue = totalSales;
              console.log('Using direct number:', numericValue);
            } else {
              // Unknown type
              console.log('Unknown totalSales type:', typeof totalSales, 'Value:', totalSales);
              salesStat.textContent = 'No data';
              // Reset the label to default
              const labelStat = document.querySelector('.stat-card--sales .stat-label');
              if(labelStat) {
                labelStat.textContent = 'Total Sales';
              }
              return;
            }
            
            // Validate the parsed value
            if (!isFinite(numericValue) || numericValue === 0) {
              console.log('Invalid or zero numeric value:', numericValue);
              salesStat.textContent = 'No data';
              // Reset the label to default
              const labelStat = document.querySelector('.stat-card--sales .stat-label');
              if(labelStat) {
                labelStat.textContent = 'Total Sales';
              }
              return;
            }
            
            // Format the amount with Indian numbering system
            const formattedAmount = new Intl.NumberFormat('en-IN', {
              style: 'currency',
              currency: 'INR',
              minimumFractionDigits: 2
            }).format(numericValue);
            
            console.log('Final formatted amount:', formattedAmount);
            
            // Update the display without using data-target
            salesStat.textContent = formattedAmount;
            
            // Also update the stat label to show invoice count
            const labelStat = document.querySelector('.stat-card--sales .stat-label');
            if(labelStat && invoiceCount > 0) {
              labelStat.textContent = `Total Sales (${invoiceCount} invoices)`;
            } else {
              labelStat.textContent = 'Total Sales';
            }
          } else {
            // No data found, show default message
            console.log('Sales stat element not found');
          }
        } else {
          // If no data found, show default message
          const salesStat = document.querySelector('.stat-card--sales .stat-value');
          if(salesStat){
            console.log('API returned non-ok status, showing "No data"');
            salesStat.textContent = 'No data';
          }
        }
      } catch(error){
        console.error('Error loading dashboard stats:', error);
        // Keep default values on error
        const salesStat = document.querySelector('.stat-card--sales .stat-value');
        if(salesStat){
          salesStat.textContent = 'Error loading';
        }
      }
    }
    
    async function loadDashboardGSTPayable(){
      try{
        const apiBase = await resolveApiBase();
        const token = getAuthToken();
        
        console.log('Fetching total GST payable from:', `${apiBase}/ledger/total-gst-payable`);
        console.log('Auth token present:', !!token);
        
        // Fetch total GST payable (sum of all gst_payable amounts for the user)
        const response = await fetch(`${apiBase}/ledger/total-gst-payable`, {
          headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        
        console.log('GST Response status:', response.status);
        
        if(response.status === 401){
          handleAuthError('Session expired. Please login again.');
          return;
        }
        
        if(response.ok){
          const data = await response.json();
          console.log('GST API response data:', data);
          
          const totalGSTPayable = data.total_gst_payable;
          const invoiceCount = data.invoice_count;
          console.log('Raw total_gst_payable value:', totalGSTPayable, 'Type:', typeof totalGSTPayable);
          console.log('GST Invoice count:', invoiceCount);
          
          // Update GST payable stat
          const gstStat = document.querySelector('.stat-card--gst .stat-value');
          if(gstStat){
            // Handle different data formats from API with robust type checking
            let numericValue;
            
            if (totalGSTPayable === null || totalGSTPayable === undefined || totalGSTPayable === 0) {
              console.log('total_gst_payable is null/undefined/zero:', totalGSTPayable);
              gstStat.textContent = 'No data';
              // Reset the label to default
              const labelStat = document.querySelector('.stat-card--gst .stat-label');
              if(labelStat) {
                labelStat.textContent = 'Gst Payable';
              }
              return;
            }
            
            if (typeof totalGSTPayable === 'string') {
              // Extract numbers from formatted strings like "₹ 1,234.50"
              const cleanString = totalGSTPayable.replace(/[^\d.]/g, '');
              numericValue = parseFloat(cleanString);
              console.log('GST Parsed from string:', cleanString, '->', numericValue);
            } else if (typeof totalGSTPayable === 'number') {
              // Direct number value
              numericValue = totalGSTPayable;
              console.log('GST Using direct number:', numericValue);
            } else {
              // Unknown type
              console.log('Unknown total_gst_payable type:', typeof totalGSTPayable, 'Value:', totalGSTPayable);
              gstStat.textContent = 'No data';
              // Reset the label to default
              const labelStat = document.querySelector('.stat-card--gst .stat-label');
              if(labelStat) {
                labelStat.textContent = 'Gst Payable';
              }
              return;
            }
            
            // Validate the parsed value
            if (!isFinite(numericValue) || numericValue === 0) {
              console.log('Invalid or zero numeric value:', numericValue);
              gstStat.textContent = 'No data';
              // Reset the label to default
              const labelStat = document.querySelector('.stat-card--gst .stat-label');
              if(labelStat) {
                labelStat.textContent = 'Gst Payable';
              }
              return;
            }
            
            // Format the amount with Indian numbering system
            const formattedAmount = new Intl.NumberFormat('en-IN', {
              style: 'currency',
              currency: 'INR',
              minimumFractionDigits: 2
            }).format(numericValue);
            
            console.log('GST Final formatted amount:', formattedAmount);
            
            // Update the display
            gstStat.textContent = formattedAmount;
            
            // Also update the stat label to show invoice count
            const labelStat = document.querySelector('.stat-card--gst .stat-label');
            if(labelStat && invoiceCount > 0) {
              labelStat.textContent = `Gst Payable (${invoiceCount} invoices)`;
            } else {
              labelStat.textContent = 'Gst Payable';
            }
          } else {
            // No data found, show default message
            console.log('GST stat element not found');
          }
        } else {
          // If no data found, show default message
          const gstStat = document.querySelector('.stat-card--gst .stat-value');
          if(gstStat){
            console.log('GST API returned non-ok status, showing "No data"');
            gstStat.textContent = 'No data';
          }
        }
      } catch(error){
        console.error('Error loading dashboard GST stats:', error);
        // Keep default values on error
        const gstStat = document.querySelector('.stat-card--gst .stat-value');
        if(gstStat){
          gstStat.textContent = 'Error loading';
        }
      }
    }
    
    async function calculateTaxableAmount(){
      try{
        const salesStat = document.querySelector('.stat-card--sales .stat-value');
        const gstStat = document.querySelector('.stat-card--gst .stat-value');
        const taxableStat = document.querySelector('.stat-card--taxable .stat-value');
        
        if(!salesStat || !gstStat || !taxableStat){
          console.log('One or more stat elements not found for taxable calculation');
          return;
        }
        
        // Get sales amount
        const salesText = salesStat.textContent.trim();
        let salesAmount = 0;
        
        if(salesText !== 'No data' && salesText !== 'Error loading' && salesText !== ''){
          try{
            // Extract number from formatted currency string
            const salesMatch = salesText.replace(/[^\d.]/g, '');
            salesAmount = parseFloat(salesMatch);
            if(!isFinite(salesAmount)) salesAmount = 0;
          }catch(e){
            console.log('Could not parse sales amount:', salesText);
            salesAmount = 0;
          }
        }
        
        // Get GST amount
        const gstText = gstStat.textContent.trim();
        let gstAmount = 0;
        
        if(gstText !== 'No data' && gstText !== 'Error loading' && gstText !== ''){
          try{
            // Extract number from formatted currency string
            const gstMatch = gstText.replace(/[^\d.]/g, '');
            gstAmount = parseFloat(gstMatch);
            if(!isFinite(gstAmount)) gstAmount = 0;
          }catch(e){
            console.log('Could not parse GST amount:', gstText);
            gstAmount = 0;
          }
        }
        
        console.log('Sales amount:', salesAmount, 'GST amount:', gstAmount);
        
        // Calculate taxable amount: Total Sales - GST Payable
        let taxableAmount = salesAmount - gstAmount;
        
        // Handle edge cases
        if(salesAmount === 0 && gstAmount === 0){
          taxableStat.textContent = 'No data';
          const labelStat = document.querySelector('.stat-card--taxable .stat-label');
          if(labelStat) {
            labelStat.textContent = 'Taxable Amount';
          }
          return;
        }
        
        if(taxableAmount < 0){
          console.warn('Taxable amount is negative, setting to 0');
          taxableAmount = 0;
        }
        
        // Format the taxable amount with Indian numbering system
        const formattedAmount = new Intl.NumberFormat('en-IN', {
          style: 'currency',
          currency: 'INR',
          minimumFractionDigits: 2
        }).format(taxableAmount);
        
        console.log('Taxable amount calculated:', formattedAmount);
        
        // Update the display
        taxableStat.textContent = formattedAmount;
        
        // Update the label to show calculation info
        const labelStat = document.querySelector('.stat-card--taxable .stat-label');
        if(labelStat) {
          labelStat.textContent = 'Taxable Amount (Sales - GST)';
        }
        
      } catch(error){
        console.error('Error calculating taxable amount:', error);
        const taxableStat = document.querySelector('.stat-card--taxable .stat-value');
        if(taxableStat){
          taxableStat.textContent = 'Error calculating';
        }
      }
    }
    
    // Load stats when dashboard is visible
    if('IntersectionObserver' in window){
      const dashboardObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if(entry.isIntersecting){
            console.log('Dashboard stats section is now visible, loading data...');
            loadDashboardStats().then(() => {
              loadDashboardGSTPayable().then(() => {
                // Calculate taxable amount after both sales and GST are loaded
                setTimeout(calculateTaxableAmount, 100);
              });
            });
            dashboardObserver.unobserve(entry.target);
          }
        });
      }, { threshold: 0.1 });
      
      dashboardStats.parentElement ? dashboardObserver.observe(dashboardStats.parentElement) : loadDashboardStats();
    } else {
      // Fallback for browsers without IntersectionObserver
      console.log('IntersectionObserver not available, loading stats immediately...');
      loadDashboardStats();
      loadDashboardGSTPayable();
      // Calculate taxable amount after a short delay to ensure both are loaded
      setTimeout(calculateTaxableAmount, 500);
    }
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
            headers: authHeaders(),
            body: formData
        });

        if (response.status === 401) { handleAuthError('Session expired. Please login again.'); return; }
        if (response.ok) {
            const result = await response.json();
            
            // Check for validation failure
            if (result.validation && !result.validation.is_valid) {
              // Show validation modal instead of proceeding
              showValidationModal(result.validation);
              if(progressOverlay) progressOverlay.hidden = true;
              return;
            }
            
            // Check for duplicate bill number
            if (result.status === 'duplicate_detected') {
              // Show duplicate detection modal
              showDuplicateModal(result);
              if(progressOverlay) progressOverlay.hidden = true;
              return;
            }
            
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

  // Validation modal functionality
  const validationModal = document.getElementById('validationModal');
  const validationCard = document.getElementById('validationCard');
  const validationMessage = document.getElementById('validationMessage');
  const validationDetails = document.getElementById('validationDetails');
  const missingFieldsList = document.getElementById('missingFieldsList');
  const closeValidationBtn = document.getElementById('closeValidationBtn');
  const retryValidationBtn = document.getElementById('retryValidationBtn');
  const cancelValidationBtn = document.getElementById('cancelValidationBtn');

  function showValidationModal(validationResult) {
    if (!validationModal || !validationCard) return;
    
    // Update modal content
    validationMessage.textContent = validationResult.message || 'kindly upload the image of invoice';
    
    if (validationResult.missing_fields && validationResult.missing_fields.length > 0) {
      validationDetails.hidden = false;
      validationDetails.setAttribute('aria-hidden', 'false');
      missingFieldsList.innerHTML = validationResult.missing_fields
        .map(field => `<li>${field.charAt(0).toUpperCase() + field.slice(1)}</li>`)
        .join('');
    } else {
      validationDetails.hidden = true;
      validationDetails.setAttribute('aria-hidden', 'true');
    }
    
    // Show modal
    validationModal.setAttribute('aria-hidden', 'false');
    validationCard.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
    
    // Focus the retry button for accessibility
    if (retryValidationBtn) retryValidationBtn.focus();
  }

  function hideValidationModal() {
    if (!validationModal || !validationCard) return;
    
    validationModal.setAttribute('aria-hidden', 'true');
    validationCard.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
  }

  // Validation modal event listeners
  if (closeValidationBtn) {
    closeValidationBtn.addEventListener('click', hideValidationModal);
  }
  
  if (cancelValidationBtn) {
    cancelValidationBtn.addEventListener('click', () => {
      hideValidationModal();
      // Reset preview state
      const fp = document.getElementById('filePreview'); 
      if(fp){ 
        fp.hidden = true; 
        fp.setAttribute('aria-hidden','true'); 
      }
      if(fileInput) fileInput.value = '';
      capturedFile = null;
    });
  }
  
  if (retryValidationBtn) {
    retryValidationBtn.addEventListener('click', () => {
      hideValidationModal();
      // Reopen camera if captured file was used, otherwise reopen file picker
      if (capturedFile) {
        const openCameraBtn = document.getElementById('openCameraBtn');
        if (openCameraBtn) openCameraBtn.click();
      } else {
        fileInput?.click();
      }
    });
  }

  // Close validation modal when clicking outside
  if (validationModal) {
    validationModal.addEventListener('click', (e) => {
      if (e.target === validationModal) {
        hideValidationModal();
      }
    });
  }

  // Close validation modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && validationModal && validationModal.getAttribute('aria-hidden') === 'false') {
      hideValidationModal();
    }
  });

  // Duplicate modal functionality
  const duplicateModal = document.getElementById('duplicateModal');
  const duplicateCard = document.getElementById('duplicateCard');
  const duplicateMessage = document.getElementById('duplicateMessage');
  const duplicateBillNumber = document.getElementById('duplicateBillNumber');
  const duplicateDetails = document.getElementById('duplicateDetails');
  const closeDuplicateBtn = document.getElementById('closeDuplicateBtn');
  const viewExistingBtn = document.getElementById('viewExistingBtn');
  const cancelDuplicateBtn = document.getElementById('cancelDuplicateBtn');

  function showDuplicateModal(duplicateResult) {
    if (!duplicateModal || !duplicateCard) return;
    
    // Update modal content
    duplicateMessage.textContent = 'An invoice with the same bill number already exists in your ledger.';
    
    if (duplicateResult.bill_number) {
      duplicateBillNumber.textContent = duplicateResult.bill_number;
      duplicateDetails.hidden = false;
      duplicateDetails.setAttribute('aria-hidden', 'false');
    } else {
      duplicateDetails.hidden = true;
      duplicateDetails.setAttribute('aria-hidden', 'true');
    }
    
    // Show modal
    duplicateModal.setAttribute('aria-hidden', 'false');
    duplicateCard.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
    
    // Focus the view existing button for accessibility
    if (viewExistingBtn) viewExistingBtn.focus();
  }

  function hideDuplicateModal() {
    if (!duplicateModal || !duplicateCard) return;
    
    duplicateModal.setAttribute('aria-hidden', 'true');
    duplicateCard.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
  }

  // Duplicate modal event listeners
  if (closeDuplicateBtn) {
    closeDuplicateBtn.addEventListener('click', hideDuplicateModal);
  }
  
  if (cancelDuplicateBtn) {
    cancelDuplicateBtn.addEventListener('click', () => {
      hideDuplicateModal();
      // Reset preview state
      const fp = document.getElementById('filePreview'); 
      if(fp){ 
        fp.hidden = true; 
        fp.setAttribute('aria-hidden','true'); 
      }
      if(fileInput) fileInput.value = '';
      capturedFile = null;
    });
  }
  
  if (viewExistingBtn) {
    viewExistingBtn.addEventListener('click', () => {
      hideDuplicateModal();
      // Navigate to ledger page to view existing invoice
      window.location.href = 'ledger.html';
    });
  }

  // Close duplicate modal when clicking outside
  if (duplicateModal) {
    duplicateModal.addEventListener('click', (e) => {
      if (e.target === duplicateModal) {
        hideDuplicateModal();
      }
    });
  }

  // Close duplicate modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && duplicateModal && duplicateModal.getAttribute('aria-hidden') === 'false') {
      hideDuplicateModal();
    }
  });

    // Ledger page: render OCR result
    const ledgerRoot = document.getElementById('ledgerRoot');
    if(ledgerRoot){
      const statusEl = document.getElementById('ledgerStatus');
      const ledgerModal = document.getElementById('ledgerImageModal');
      const ledgerModalImg = document.getElementById('ledgerImageFull');
      const ledgerModalClose = document.getElementById('ledgerImageClose');
      const downloadBtn = document.getElementById('downloadLedgerPdf');
      const financialYearSelect = document.getElementById('financialYear');

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
        
        // Use the bill_number field from the database if available, otherwise extract from text
        const billNo = (data && data.bill_number) || findBillNo() || 'Not detected';

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
          gstPayable: (data && data.gst_payable != null) ? formatAmount(data.gst_payable) : gstAmt,
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
          // Clear the table when no data is found
          const tbody = document.getElementById('ledgerBody');
          if(tbody){
            tbody.innerHTML = '';
          }
          return;
        }
        if(statusEl) statusEl.textContent = `Showing ${items.length} ledger entr${items.length === 1 ? 'y' : 'ies'}.`;

        const rows = items.map(extractLedgerRows);
        const tbody = document.getElementById('ledgerBody');
        if(tbody){
          const base = apiBase || cachedApiBase || DEFAULT_API_BASES[0] || '';
          const token = getAuthToken();
          tbody.innerHTML = rows.map((r, i)=>`
            <tr data-ledger-id="${r.id}">
              <td class="col-preview">${r.id ? `<img class="ledger-preview" src="${base}/ledger/${r.id}/image${token ? `?token=${encodeURIComponent(token)}` : ''}" alt="Invoice preview">` : ''}</td>
              <td>${i + 1}</td>
              <td>${r.billNo}</td>
              <td>${r.particulars}</td>
              <td>${r.gstPayable}</td>
              <td>${r.totalAmt}</td>
              <td class="actions-cell">
                <button class="btn danger delete-ledger-btn" data-ledger-id="${r.id}" title="Delete entry">
                  <i class="fa-solid fa-trash" aria-hidden="true"></i>
                  <span class="sr-only">Delete</span>
                </button>
              </td>
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
        ledgerBody.addEventListener('click', async (e)=>{
          const img = e.target && e.target.closest ? e.target.closest('img.ledger-preview') : null;
          if(img && img.getAttribute('src')){ openLedgerPreview(img.getAttribute('src')); return; }
          
          // Handle delete button clicks
          const deleteBtn = e.target && e.target.closest ? e.target.closest('.delete-ledger-btn') : null;
          if(deleteBtn){
            const ledgerId = deleteBtn.getAttribute('data-ledger-id');
            if(ledgerId){
              await handleDeleteLedger(ledgerId);
            }
          }
        });
      }

      if(downloadBtn){
        downloadBtn.addEventListener('click', async ()=>{
          try{
            const apiBase = await resolveApiBase();
            const token = getAuthToken();
            
            // Get current ledger items from the table
            const tbody = document.getElementById('ledgerBody');
            if(!tbody || tbody.children.length === 0){
              alert('No ledger entries to download. Please upload some invoices first.');
              return;
            }
            
            // Extract ledger items from the current table view
            const currentItems = [];
            const rows = tbody.querySelectorAll('tr');
            rows.forEach(row => {
              const cells = row.querySelectorAll('td');
              if(cells.length >= 6) {
                // Get the ledger ID from the preview cell
                const previewCell = cells[0];
                const img = previewCell.querySelector('img');
                const ledgerId = img ? img.src.split('/').pop().split('?')[0] : null;
                
                if(ledgerId) {
                  // Create a minimal ledger object with just the ID for PDF generation
                  currentItems.push({
                    id: ledgerId,
                    owner_email: (function(){ try{ return localStorage.getItem('authEmail'); }catch(e){ return null; } })()
                  });
                }
              }
            });
            
            if(currentItems.length === 0) {
              alert('No ledger entries found in current view.');
              return;
            }
            
            // Generate PDF from current items using the existing endpoint with limit
            const limit = currentItems.length;
            const url = `${apiBase}/ledger/list/pdf?limit=${limit}${token ? `&token=${encodeURIComponent(token)}` : ''}`;
            const link = document.createElement('a');
            link.href = url;
            link.target = '_blank';
            link.rel = 'noopener';
            document.body.appendChild(link);
            link.click();
            link.remove();
          }catch(e){
            console.error('PDF download error:', e);
            alert('Unable to download PDF. Make sure the backend is running on port 8000.');
          }
        });
      }

      // Financial year selection functionality
      if(financialYearSelect){
        financialYearSelect.addEventListener('change', async ()=>{
          try{
            const selectedFY = financialYearSelect.value;
            const apiBase = await resolveApiBase();
            
            let data = null;
            let endpoint = '';
            
            if(selectedFY === 'all'){
              endpoint = `${apiBase}/ledger/list?limit=500`;
            } else {
              endpoint = `${apiBase}/ledger/financial-year/${selectedFY}`;
            }
            
            const res = await fetch(endpoint, { headers: authHeaders() });
            if(res.status === 401){ handleAuthError('Session expired. Please login again.'); return; }
            if(res.ok){
              const response = await res.json();
              data = response.items || response;
              
              // Update status to show financial year
              if(selectedFY === 'all'){
                statusEl.textContent = `Showing all ledger entries (${data.length} total)`;
              } else {
                // Convert financial year number to display format
                const endYear = parseInt(selectedFY);
                const startYear = endYear - 1;
                statusEl.textContent = `Showing ledger entries for Financial Year ${startYear}-${endYear} (${data.length} entries)`;
              }
              
              renderLedger(data, apiBase);
            } else {
              throw new Error('Failed to fetch data');
            }
          }catch(e){
            console.error('Error fetching financial year data:', e);
            statusEl.textContent = 'Unable to load financial year data.';
          }
        });
      }

      // Function to load initial data based on current financial year selection
      async function loadInitialData() {
        try{
          const selectedFY = financialYearSelect ? financialYearSelect.value : 'all';
          const apiBase = await resolveApiBase();
          
          let data = null;
          let endpoint = '';
          
          if(selectedFY === 'all'){
            endpoint = `${apiBase}/ledger/list?limit=500`;
          } else {
            endpoint = `${apiBase}/ledger/financial-year/${selectedFY}`;
          }
          
          const res = await fetch(endpoint, { headers: authHeaders() });
          if(res.status === 401){ handleAuthError('Session expired. Please login again.'); return; }
          if(res.ok){
            const response = await res.json();
            data = response.items || response;
            
            // Update status to show financial year
            if(selectedFY === 'all'){
              statusEl.textContent = `Showing all ledger entries (${data.length} total)`;
            } else {
              // Convert financial year number to display format
              const endYear = parseInt(selectedFY);
              const startYear = endYear - 1;
              statusEl.textContent = `Showing ledger entries for Financial Year ${startYear}-${endYear} (${data.length} entries)`;
            }
            
            renderLedger(data, apiBase);
          } else {
            throw new Error('Failed to fetch initial data');
          }
        }catch(e){
          console.error('Error loading initial data:', e);
          if(statusEl) statusEl.textContent = 'Unable to load ledger data.';
        }
      }

      // Load initial data when page loads
      loadInitialData();
    }

    // Handle delete ledger entry
    async function handleDeleteLedger(ledgerId) {
      if (!ledgerId) return;

      // Confirm deletion
      const confirmed = confirm('Are you sure you want to delete this ledger entry? This action cannot be undone.');
      if (!confirmed) return;

      try {
        const apiBase = await resolveApiBase();
        const token = getAuthToken();

        const response = await fetch(`${apiBase}/ledger/${ledgerId}`, {
          method: 'DELETE',
          headers: authHeaders()
        });

        if (response.status === 401) {
          handleAuthError('Session expired. Please login again.');
          return;
        }

        if (response.ok) {
          showToast('✅ Ledger entry deleted successfully!', 'success', 1500);
          
          // Remove the row from the table
          const row = document.querySelector(`tr[data-ledger-id="${ledgerId}"]`);
          if (row) {
            row.remove();
          }

          // Update status message
          const statusEl = document.getElementById('ledgerStatus');
          const tbody = document.getElementById('ledgerBody');
          if (statusEl && tbody) {
            const rowCount = tbody.children.length;
            statusEl.textContent = `Showing ${rowCount} ledger entr${rowCount === 1 ? 'y' : 'ies'}.`;
          }
        } else {
          const errorData = await response.json().catch(() => ({}));
          const errorMessage = errorData.detail || 'Failed to delete ledger entry';
          showToast(`❌ ${errorMessage}`, 'error', 2000);
        }
      } catch (error) {
        console.error('Error deleting ledger entry:', error);
        showToast('❌ Network error. Please try again.', 'error', 2000);
      }
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
        const data = await res.json();
        try{
          if(data.token) localStorage.setItem('authToken', data.token);
          if(data.email) localStorage.setItem('authEmail', data.email);
          if(data.name) localStorage.setItem('authName', data.name);
        }catch(e){}
        showToast('✅ Login successful!', 'success', 2000);
        setTimeout(()=> { window.location.href = 'index.html'; }, 1500);
      }catch(err){
        alert('❌ ' + err.message);
      }
    });
  }

  // auth pages: toggle password visibility for login, signup, and change password
  const eyeButtons = Array.from(document.querySelectorAll('.auth-eye, .login-eye, .signup-eye, .password-toggle'));
  if(eyeButtons.length){
    eyeButtons.forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const wrapper = btn.closest('.auth-password, .login-password, .signup-password, .password-field');
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

  // Profile page: load user information
  const profileNameEl = document.getElementById('profileName');
  const profileEmailEl = document.getElementById('profileEmail');
  
  if(profileNameEl || profileEmailEl){
    async function loadUserProfile(){
      try{
        const apiBase = await resolveApiBase();
        const token = getAuthToken();
        
        if(!token){
          handleAuthError('Please login to view your profile.');
          return;
        }
        
        // Try to get user profile from the backend API
        try {
          const response = await fetch(`${apiBase}/profile/`, {
            headers: authHeaders()
          });
          
          if(response.status === 401){
            handleAuthError('Session expired. Please login again.');
            return;
          }
          
          if(response.ok){
            const profileData = await response.json();
            
            // Display the actual name from the database
            if(profileNameEl && profileData.name){
              profileNameEl.textContent = profileData.name;
            }
            
            // Display the email
            if(profileEmailEl && profileData.email){
              profileEmailEl.textContent = `Email: ${profileData.email}`;
            }
            
            // Store the name in localStorage for fallback
            if(profileData.name){
              try{
                localStorage.setItem('authName', profileData.name);
              }catch(e){}
            }
            
            // Store the email in localStorage for fallback
            if(profileData.email){
              try{
                localStorage.setItem('authEmail', profileData.email);
              }catch(e){}
            }
            
            return;
          }
        } catch (apiError) {
          console.warn('Failed to fetch profile from API, falling back to localStorage:', apiError);
        }
        
        // Fallback to localStorage if API call fails
        const storedEmail = (function(){ try{ return localStorage.getItem('authEmail'); }catch(e){ return null; } })();
        const storedName = (function(){ try{ return localStorage.getItem('authName'); }catch(e){ return null; } })();
        
        if(storedEmail){
          if(profileEmailEl){
            profileEmailEl.textContent = `Email: ${storedEmail}`;
          }
          
          if(storedName && storedName.trim()){
            // Use the stored name from signup exactly as written
            if(profileNameEl){
              profileNameEl.textContent = storedName;
            }
          } else {
            // Fallback to extracting name from email (minimal processing)
            const name = storedEmail.split('@')[0].replace(/\./g, ' ');
            if(profileNameEl){
              profileNameEl.textContent = name;
            }
          }
        } else {
          if(profileNameEl){
            profileNameEl.textContent = 'User';
          }
          if(profileEmailEl){
            profileEmailEl.textContent = 'Email not available';
          }
        }
      }catch(error){
        console.error('Error loading profile:', error);
        if(profileNameEl){
          profileNameEl.textContent = 'Error loading profile';
        }
        if(profileEmailEl){
          profileEmailEl.textContent = 'Please login again';
        }
      }
    }
    
    // Load profile when page loads
    loadUserProfile();
  }

  // Profile dropdown functionality
  const dropdownTrigger = document.querySelector('.dropdown-trigger');
  const dropdownMenu = document.querySelector('.dropdown-menu');
  const dropdownItems = document.querySelectorAll('.dropdown-item');

  if(dropdownTrigger && dropdownMenu){
    // Toggle dropdown on click
    dropdownTrigger.addEventListener('click', (e) => {
      e.preventDefault();
      const isExpanded = dropdownTrigger.getAttribute('aria-expanded') === 'true';
      dropdownTrigger.setAttribute('aria-expanded', String(!isExpanded));
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
      if (!dropdownTrigger.contains(e.target) && !dropdownMenu.contains(e.target)) {
        dropdownTrigger.setAttribute('aria-expanded', 'false');
      }
    });

    // Keyboard navigation
    dropdownTrigger.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        const isExpanded = dropdownTrigger.getAttribute('aria-expanded') === 'true';
        dropdownTrigger.setAttribute('aria-expanded', String(!isExpanded));
        if (!isExpanded && dropdownItems.length > 0) {
          dropdownItems[0].focus();
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        dropdownTrigger.setAttribute('aria-expanded', 'true');
        if (dropdownItems.length > 0) {
          dropdownItems[0].focus();
        }
      }
    });

    // Handle dropdown items keyboard navigation
    dropdownItems.forEach((item, index) => {
      item.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          const nextIndex = (index + 1) % dropdownItems.length;
          dropdownItems[nextIndex].focus();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const prevIndex = (index - 1 + dropdownItems.length) % dropdownItems.length;
          dropdownItems[prevIndex].focus();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          dropdownTrigger.setAttribute('aria-expanded', 'false');
          dropdownTrigger.focus();
        }
      });

      // Close dropdown when an item is clicked
      item.addEventListener('click', () => {
        dropdownTrigger.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // Change Password Modal functionality
  const changePasswordTrigger = document.querySelector('.change-password-trigger');
  const changePasswordModal = document.getElementById('changePasswordModal');
  const closeChangePasswordModal = document.getElementById('closeChangePasswordModal');
  const closeChangePasswordModalBtn = document.getElementById('closeChangePasswordModalBtn');
  const savePasswordBtn = document.getElementById('savePasswordBtn');
  const changePasswordForm = document.getElementById('changePasswordForm');
  const passwordError = document.getElementById('passwordError');

  // Password visibility toggle for change password modal
  function setupPasswordVisibilityToggles() {
    // General password eye buttons (for all modals and forms)
    const passwordEyeButtons = Array.from(document.querySelectorAll('.password-toggle'));
    if(passwordEyeButtons.length){
      passwordEyeButtons.forEach(btn=>{
        // Remove existing listeners to avoid duplicates by using event delegation
        btn.removeEventListener('click', togglePasswordVisibility);
        btn.addEventListener('click', togglePasswordVisibility);
      });
    }
  }

  // Password toggle function
  function togglePasswordVisibility(e) {
    const btn = e.currentTarget;
    const wrapper = btn.closest('.password-field');
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
  }

  // Setup password visibility toggles initially
  setupPasswordVisibilityToggles();

  // Also setup when change password modal is shown
  if (changePasswordTrigger) {
    changePasswordTrigger.addEventListener('click', (e) => {
      e.preventDefault();
      showChangePasswordModal();
      // Setup password visibility toggles after modal is shown
      setTimeout(setupPasswordVisibilityToggles, 100);
    });
  }

  function showChangePasswordModal() {
    if (!changePasswordModal) return;
    changePasswordModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
    
    // Clear form and error
    if (changePasswordForm) {
      changePasswordForm.reset();
    }
    if (passwordError) {
      passwordError.style.display = 'none';
      passwordError.textContent = '';
    }
    
    // Focus first input
    const firstInput = document.getElementById('currentPassword');
    if (firstInput) firstInput.focus();
  }

  function hideChangePasswordModal() {
    if (!changePasswordModal) return;
    changePasswordModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
  }

  // Open change password modal
  if (changePasswordTrigger) {
    changePasswordTrigger.addEventListener('click', (e) => {
      e.preventDefault();
      showChangePasswordModal();
    });
  }

  // Close change password modal
  if (closeChangePasswordModal) {
    closeChangePasswordModal.addEventListener('click', hideChangePasswordModal);
  }

  if (closeChangePasswordModalBtn) {
    closeChangePasswordModalBtn.addEventListener('click', hideChangePasswordModal);
  }

  // Close change password modal when clicking outside
  if (changePasswordModal) {
    changePasswordModal.addEventListener('click', (e) => {
      if (e.target === changePasswordModal) {
        hideChangePasswordModal();
      }
    });
  }

  // Close change password modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && changePasswordModal && changePasswordModal.getAttribute('aria-hidden') === 'false') {
      hideChangePasswordModal();
    }
  });

  // Handle password change form submission
  if (savePasswordBtn && changePasswordForm) {
    savePasswordBtn.addEventListener('click', async () => {
      const currentPassword = document.getElementById('currentPassword').value;
      const newPassword = document.getElementById('newPassword').value;
      const confirmNewPassword = document.getElementById('confirmNewPassword').value;

      // Clear previous error
      if (passwordError) {
        passwordError.style.display = 'none';
        passwordError.textContent = '';
      }

      // Validate form
      if (!currentPassword) {
        showError('Please enter your current password');
        return;
      }

      if (!newPassword) {
        showError('Please enter a new password');
        return;
      }

      if (newPassword.length < 6) {
        showError('New password must be at least 6 characters long');
        return;
      }

      if (!confirmNewPassword) {
        showError('Please confirm your new password');
        return;
      }

      if (newPassword !== confirmNewPassword) {
        showError('New password and confirmation do not match');
        return;
      }

      try {
        const apiBase = await resolveApiBase();
        const token = getAuthToken();

        const response = await fetch(`${apiBase}/change-password/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
            confirm_new_password: confirmNewPassword
          })
        });

        if (response.status === 401) {
          handleAuthError('Session expired. Please login again.');
          return;
        }

        if (response.ok) {
          showToast('✅ Password changed successfully!', 'success', 2000);
          hideChangePasswordModal();
          // Clear form
          changePasswordForm.reset();
        } else {
          const errorData = await response.json().catch(() => ({}));
          const errorMessage = errorData.detail || 'Failed to change password';
          showError(errorMessage);
        }
      } catch (error) {
        console.error('Error changing password:', error);
        showError('Network error. Please try again.');
      }
    });
  }

  function showError(message) {
    if (passwordError) {
      passwordError.style.display = 'block';
      passwordError.textContent = message;
    }
  }

  // Edit Personal Info Modal functionality
  const editPersonalInfoModal = document.getElementById('editPersonalInfoModal');
  const editPersonalInfoTrigger = document.querySelector('.dropdown-item[href="#"]');
  const closeEditPersonalInfoModalBtn = document.getElementById('closeEditPersonalInfoModal');
  const closeEditPersonalInfoModalBtn2 = document.getElementById('closeEditPersonalInfoModalBtn');
  const savePersonalInfoBtn = document.getElementById('savePersonalInfoBtn');
  const editPersonalInfoForm = document.getElementById('editPersonalInfoForm');
  const editNameInput = document.getElementById('editName');
  const editEmailInput = document.getElementById('editEmail');
  const personalInfoError = document.getElementById('personalInfoError');

  // Find the correct "Edit Personal Info" dropdown item (not the change password one)
  const editDropdownItems = document.querySelectorAll('.dropdown-item');
  let editPersonalInfoTriggerElement = null;
  editDropdownItems.forEach(item => {
    if (item.textContent.includes('Edit Personal Info')) {
      editPersonalInfoTriggerElement = item;
    }
  });

  // Close modal functions
  const closeEditPersonalInfoModal = () => {
    editPersonalInfoModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
    editPersonalInfoForm.reset();
    personalInfoError.style.display = 'none';
  };

  // Close modal events
  if (closeEditPersonalInfoModalBtn) {
    closeEditPersonalInfoModalBtn.addEventListener('click', closeEditPersonalInfoModal);
  }
  if (closeEditPersonalInfoModalBtn2) {
    closeEditPersonalInfoModalBtn2.addEventListener('click', closeEditPersonalInfoModal);
  }
  if (editPersonalInfoModal) {
    editPersonalInfoModal.addEventListener('click', (e) => {
      if (e.target === editPersonalInfoModal) {
        closeEditPersonalInfoModal();
      }
    });
  }

  // Open modal
  if (editPersonalInfoTriggerElement) {
    editPersonalInfoTriggerElement.addEventListener('click', (e) => {
      e.preventDefault();
      loadUserProfileForEdit();
      editPersonalInfoModal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('no-scroll');
    });
  }

  // Load user profile data into the modal
  const loadUserProfileForEdit = async () => {
    try {
      const apiBase = await resolveApiBase();
      const token = getAuthToken();
      
      if(!token){
        handleAuthError('Please login to view your profile.');
        return;
      }
      
      const response = await fetch(`${apiBase}/profile/`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (response.ok) {
        const userData = await response.json();
        if (editNameInput) editNameInput.value = userData.name || '';
        if (editEmailInput) editEmailInput.value = userData.email || '';
      } else {
        console.error('Failed to load user profile');
      }
    } catch (error) {
      console.error('Error loading user profile:', error);
    }
  };

  // Form submission
  if (savePersonalInfoBtn) {
    savePersonalInfoBtn.addEventListener('click', async () => {
      const newName = editNameInput ? editNameInput.value.trim() : '';

      if (!newName) {
        personalInfoError.textContent = 'Name is required.';
        personalInfoError.style.display = 'block';
        return;
      }

      try {
        const apiBase = await resolveApiBase();
        const token = getAuthToken();

        const response = await fetch(`${apiBase}/update-profile/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({
            name: newName
          })
        });

        const data = await response.json();

        if (response.ok) {
          showToast('Profile updated successfully!', 'success');
          closeEditPersonalInfoModal();
          // Update the profile display
          updateProfileDisplay(newName);
        } else {
          personalInfoError.textContent = data.detail || 'Failed to update profile.';
          personalInfoError.style.display = 'block';
        }
      } catch (error) {
        console.error('Error updating profile:', error);
        personalInfoError.textContent = 'An error occurred. Please try again.';
        personalInfoError.style.display = 'block';
      }
    });
  }

  // Function to update the profile display with new name
  const updateProfileDisplay = (newName) => {
    const profileNameElement = document.getElementById('profileName');
    if (profileNameElement) {
      profileNameElement.textContent = newName;
    }
  };
}



// Run includes first, then initialize UI
(async function start(){ await loadIncludes(); initHeaderNav(); await pageInit(); })();
