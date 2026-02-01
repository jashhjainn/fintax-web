const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const photo = document.getElementById('photo');
const captureBtn = document.getElementById('captureBtn');
const retakeBtn = document.getElementById('retakeBtn');
const downloadBtn = document.getElementById('downloadBtn');
const cameraSelect = document.getElementById('cameraSelect');
const messageEl = document.getElementById('message');

let currentStream = null;

async function hasGetUserMedia() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

async function getCameras() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoDevices = devices.filter(d => d.kind === 'videoinput');
    cameraSelect.innerHTML = '';
    videoDevices.forEach((d, i) => {
      const option = document.createElement('option');
      option.value = d.deviceId;
      option.text = d.label || `Camera ${i + 1}`;
      cameraSelect.appendChild(option);
    });
    if (videoDevices.length === 0) {
      cameraSelect.innerHTML = '<option value="">No cameras found</option>';
    }
  } catch (err) {
    console.warn('Error enumerating devices:', err);
  }
}

async function startStream(deviceId) {
  stopStream();
  const constraints = {
    video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } },
    audio: false
  };
  try {
    currentStream = await navigator.mediaDevices.getUserMedia(constraints);
    video.srcObject = currentStream;
    await video.play();
    messageEl.textContent = '';
  } catch (err) {
    console.error('getUserMedia error:', err);
    messageEl.textContent = 'Could not access camera: ' + (err.message || err.name);
  }
}

function stopStream() {
  if (currentStream) {
    currentStream.getTracks().forEach(t => t.stop());
    currentStream = null;
  }
}

function capture() {
  if (!video || video.readyState === 0) return;
  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 480;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const data = canvas.toDataURL('image/png');
  photo.src = data;
  photo.hidden = false;
  downloadBtn.href = data;
  downloadBtn.hidden = false;
  retakeBtn.hidden = false;
  captureBtn.hidden = true;
  stopStream();
}

function retake() {
  photo.hidden = true;
  downloadBtn.hidden = true;
  retakeBtn.hidden = true;
  captureBtn.hidden = false;
  startStream(cameraSelect.value || undefined);
}

cameraSelect.addEventListener('change', () => {
  startStream(cameraSelect.value || undefined);
});

captureBtn.addEventListener('click', capture);
retakeBtn.addEventListener('click', retake);

async function init() {
  if (!await hasGetUserMedia()) {
    messageEl.textContent = 'getUserMedia() is not supported by your browser.';
    return;
  }

  // Start with a default stream to prompt camera permission and populate labels
  try {
    await startStream();
    await getCameras();
    // If a device list exists, set the select value
    if (cameraSelect.options.length > 0 && cameraSelect.value) {
      // nothing
    }
  } catch (err) {
    console.error(err);
  }
}

init();