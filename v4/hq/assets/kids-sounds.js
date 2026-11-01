/* TPA Kids sounds (HQ 4.11.0; owner 1 Nov 2026 18:47-18:49: "Fun icons that play sounds when you press on them
   (beep beep and such noises for a bus, train sounds for a train" ... "5 noises per type though, no point going mad").
   Every sound is made by the browser itself (Web Audio): no recordings, so nothing to download or license.
   Five per vehicle type, played in turn. Nothing plays until a person has tapped something (browser rule). */
(function () {
  'use strict';
  let ctx = null, last = null;
  const next = Object.create(null);
  function ac() {
    try {
      if (!ctx) { const C = window.AudioContext || window.webkitAudioContext; if (!C) return null; ctx = new C(); }
      if (ctx.state === 'suspended') ctx.resume();
    } catch (_) { return null; }
    return ctx;
  }
  function tone(c, t, o) {
    const f = o.f || 440, d = o.d || 0.2, v = o.v || 0.15, a = o.a || 0.01;
    const osc = c.createOscillator(), g = c.createGain();
    osc.type = o.type || 'square';
    osc.frequency.setValueAtTime(f, t);
    if (o.f2) osc.frequency.exponentialRampToValueAtTime(o.f2, t + d);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(v, t + a);
    g.gain.setValueAtTime(v, t + Math.max(a, d - 0.05));
    g.gain.linearRampToValueAtTime(0, t + d);
    osc.connect(g); g.connect(c.destination);
    osc.start(t); osc.stop(t + d + 0.02);
  }
  function noise(c, t, o) {
    const d = o.d || 0.3, len = Math.ceil(c.sampleRate * d), buf = c.createBuffer(1, len, c.sampleRate), data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource(); src.buffer = buf;
    const fl = c.createBiquadFilter(); fl.type = o.type || 'bandpass';
    fl.frequency.setValueAtTime(o.f || 800, t);
    if (o.f2) fl.frequency.exponentialRampToValueAtTime(o.f2, t + d);
    fl.Q.value = o.q || 1;
    const g = c.createGain(); g.gain.setValueAtTime(o.v || 0.3, t); g.gain.exponentialRampToValueAtTime(0.001, t + d);
    src.connect(fl); fl.connect(g); g.connect(c.destination);
    src.start(t); src.stop(t + d);
  }
  const SOUNDS = {
    bus: [
      ['Beep beep', (c, t) => { for (const s of [0, 0.24]) { tone(c, t + s, { f: 440, d: 0.18 }); tone(c, t + s, { f: 554, d: 0.18, v: 0.12 }); } }],
      ['Ding ding', (c, t) => { for (const s of [0, 0.35]) { tone(c, t + s, { f: 1318, d: 0.5, type: 'sine', v: 0.2, a: 0.003 }); tone(c, t + s, { f: 2637, d: 0.3, type: 'sine', v: 0.05, a: 0.003 }); } }],
      ['Vroom vroom', (c, t) => { tone(c, t, { f: 55, f2: 120, d: 0.9, type: 'sawtooth', v: 0.12, a: 0.05 }); noise(c, t, { d: 0.9, v: 0.15, f: 200, f2: 600, type: 'lowpass' }); }],
      ['Honk honk', (c, t) => { for (const s of [0, 0.4]) { tone(c, t + s, { f: 330, d: 0.3, v: 0.13 }); tone(c, t + s, { f: 415, d: 0.3, v: 0.11 }); } }],
      ['Psssh', (c, t) => { noise(c, t, { d: 0.7, v: 0.3, f: 4000, f2: 2500, type: 'highpass' }); }]          // the doors opening
    ],
    rail: [
      ['Choo choo', (c, t) => { for (let i = 0; i < 4; i++) noise(c, t + i * 0.28, { d: 0.24, v: 0.45, f: 900, q: 0.8 }); }],
      ['Toot toot', (c, t) => { for (const s of [0, 0.6]) { tone(c, t + s, { f: 880, d: 0.45, type: 'sine', v: 0.15, a: 0.04 }); tone(c, t + s, { f: 1108, d: 0.45, type: 'sine', v: 0.1, a: 0.04 }); } }],
      ['Clickety clack', (c, t) => { for (const s of [0, 0.12, 0.5, 0.62, 1, 1.12, 1.5, 1.62]) noise(c, t + s, { d: 0.06, v: 0.5, f: 2500, q: 3 }); }],
      ['Paaarp', (c, t) => { for (const f of [311, 370, 466]) tone(c, t, { f, d: 1, type: 'sawtooth', v: 0.05, a: 0.05 }); }],
      ['Woo woo', (c, t) => { for (const s of [0, 0.45]) tone(c, t + s, { f: 600, f2: 900, d: 0.4, type: 'triangle', v: 0.2 }); }]
    ],
    air: [
      ['Whoosh', (c, t) => { noise(c, t, { d: 1.2, v: 0.4, f: 300, f2: 3000, q: 1.5 }); }],
      ['Zoom', (c, t) => { tone(c, t, { f: 900, f2: 300, d: 1, type: 'sawtooth', v: 0.06, a: 0.1 }); noise(c, t, { d: 1, v: 0.2, f: 2000, f2: 400 }); }],
      ['Bing bong', (c, t) => { tone(c, t, { f: 784, d: 0.6, type: 'sine', v: 0.2, a: 0.005 }); tone(c, t + 0.45, { f: 622, d: 0.9, type: 'sine', v: 0.2, a: 0.005 }); }],
      ['Brrrrm', (c, t) => { for (let i = 0; i < 20; i++) tone(c, t + i * 0.05, { f: 120, d: 0.04, v: 0.07, a: 0.005 }); }],   // a propeller
      ['Neeeow', (c, t) => { noise(c, t, { d: 1.4, v: 0.3, f: 600, f2: 1500, type: 'lowpass' }); tone(c, t, { f: 1800, f2: 900, d: 1.4, type: 'sine', v: 0.04, a: 0.2 }); }]
    ]
  };
  window.TPAKidsSounds = {
    names(type) { return (SOUNDS[type] || []).map(s => s[0]); },
    /* the next of this type's five sounds; returns its name (also when muted, so the words can match) */
    play(type, muted) {
      const list = SOUNDS[type]; if (!list) return null;
      const i = (next[type] || 0) % list.length; next[type] = i + 1;
      const [name, fn] = list[i];
      last = { type, name, muted: !!muted, at: Date.now() };
      if (!muted) { const c = ac(); if (c) { try { fn(c, c.currentTime + 0.02); } catch (_) {} } }
      return name;
    },
    unlock() { ac(); },
    get last() { return last; }
  };
})();
