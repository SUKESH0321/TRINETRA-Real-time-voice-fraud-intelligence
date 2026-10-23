// AudioWorklet: converts any input to mono 16 kHz PCM16 and posts ~100 ms chunks to the main thread.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.out = [];
    this.prev = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    // linear-interpolation downsample to 16 kHz
    for (; this.pos < ch.length; this.pos += this.ratio) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const a = i === 0 ? ch[0] : ch[i];
      const b = i + 1 < ch.length ? ch[i + 1] : ch[i];
      this.out.push(a + (b - a) * frac);
    }
    this.pos -= ch.length;
    if (this.out.length >= 1600) {
      const pcm = new Int16Array(this.out.length);
      for (let i = 0; i < this.out.length; i++) {
        const s = Math.max(-1, Math.min(1, this.out[i]));
        pcm[i] = s < 0 ? s * 32768 : s * 32767;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
      this.out = [];
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
