// AudioWorklet: converts the browser's native audio (usually 44.1 or 48 kHz) to mono 16 kHz PCM16
// and posts ~100 ms chunks (3200 bytes, little-endian signed 16-bit) to the main thread.
//
// Resampling is a fractional box average: every output sample is the mean of the input samples it
// covers. That is a real (if simple) low-pass, so energy above 8 kHz is attenuated instead of folding
// back into the speech band, which plain sample-picking would do.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // input samples per output sample
    this.acc = 0;
    this.need = this.ratio; // input samples still needed to complete the current output sample
    this.out = [];
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const x = ch[i];
      if (this.need >= 1) {
        this.acc += x;
        this.need -= 1;
      } else {
        this.acc += x * this.need;
        this.out.push(this.acc / this.ratio);
        this.acc = x * (1 - this.need);
        this.need = this.ratio - (1 - this.need);
      }
    }
    if (this.out.length >= 1600) {
      const pcm = new Int16Array(this.out.length);
      for (let i = 0; i < this.out.length; i++) {
        const s = Math.max(-1, Math.min(1, this.out[i]));
        pcm[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
      this.out = [];
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
