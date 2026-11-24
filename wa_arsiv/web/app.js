'use strict';
/* WhatsApp Arşivi — arayüz */

const $ = (s, el = document) => el.querySelector(s);
const state = {
  archives: [], aid: null, archive: null, chats: [], chatById: new Map(), chat: null,
  msgs: [], moreBefore: false, moreAfter: false, loadingOlder: false, loadingNewer: false,
  stickBottom: false, openToken: 0,
  globalQ: '', globalResults: null, globalLoading: false,
  importSel: null, importBackups: [], importPoll: null,
  mediaCache: new Map(), infoTab: 'photos',
};
const PAGE_FIRST = 150;   // sohbet açılınca hemen gösterilen mesaj sayısı
const PAGE_FILL = 800;    // arka planda yüklenen sayfa boyutu

/* ------------------------------------------------------------ yardımcılar */
async function api(path, opts = {}) {
  const init = { credentials: 'same-origin', headers: {} };
  if (opts.body !== undefined) {
    init.method = 'POST';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch('/api/' + path, init);
  let data = {};
  try { data = await res.json(); } catch (_) { /* boş */ }
  if (!res.ok) throw new Error(data.error || `İstek başarısız (${res.status})`);
  return data;
}
const qs = (o) => Object.entries(o)
  .filter(([, v]) => v !== undefined && v !== null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
const mediaUrl = (rel) => `/m/${encodeURIComponent(state.aid)}/` + rel.split('/').map(encodeURIComponent).join('/');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function div(cls, html) { const d = document.createElement('div'); if (cls) d.className = cls; if (html !== undefined) d.innerHTML = html; return d; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function store(key, val) { try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (_) { return null; } return null; }

let toastTimer;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
}

/* ------------------------------------------------------------ biçimlendirme */
const TR = 'tr-TR';
const fmtTime = new Intl.DateTimeFormat(TR, { hour: '2-digit', minute: '2-digit' });
const fmtDayLong = new Intl.DateTimeFormat(TR, { day: 'numeric', month: 'long', year: 'numeric' });
const fmtWeekday = new Intl.DateTimeFormat(TR, { weekday: 'long' });
const fmtShort = new Intl.DateTimeFormat(TR, { day: '2-digit', month: '2-digit', year: 'numeric' });
const nf = new Intl.NumberFormat(TR);
const cap = (s) => s ? s.charAt(0).toLocaleUpperCase('tr') + s.slice(1) : s;

function dayKey(ts) { const d = new Date(ts); return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate(); }
function startOfDay(ts) { const d = new Date(ts); d.setHours(0, 0, 0, 0); return d.getTime(); }
function daysAgo(ts) { return Math.round((startOfDay(Date.now()) - startOfDay(ts)) / 86400000); }
function dayLabel(ts) {
  const n = daysAgo(ts);
  if (n === 0) return 'Bugün';
  if (n === 1) return 'Dün';
  return fmtDayLong.format(ts);
}
function listDate(ts) {
  if (!ts) return '';
  const n = daysAgo(ts);
  if (n === 0) return fmtTime.format(ts);
  if (n === 1) return 'Dün';
  if (n > 1 && n < 7) return cap(fmtWeekday.format(ts));
  return fmtShort.format(ts);
}
function sizeStr(n) {
  if (!n) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toLocaleString(TR, { maximumFractionDigits: i >= 2 ? 1 : 0 })} ${u[i]}`;
}
function durStr(sec) {
  if (!sec || sec < 0) return '';
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const TRMAP = { 'İ': 'i', 'I': 'i', 'ı': 'i', 'Ş': 's', 'ş': 's', 'Ğ': 'g', 'ğ': 'g', 'Ü': 'u', 'ü': 'u', 'Ö': 'o', 'ö': 'o', 'Ç': 'c', 'ç': 'c', 'Â': 'a', 'â': 'a', 'Î': 'i', 'î': 'i', 'Û': 'u', 'û': 'u' };
/** Uzunluğu koruyarak Türkçe karakter ve büyük/küçük harf farkını kaldırır. */
function normChars(s) {
  let out = '';
  for (const ch of String(s || '')) {
    const m = TRMAP[ch];
    if (m) { out += m; continue; }
    const l = ch.toLowerCase();
    out += l.length === ch.length ? l : ch;
  }
  return out;
}
function highlight(text, q, around = false) {
  text = String(text || '');
  const nq = normChars(q.trim());
  if (!nq) return esc(text);
  let nt = normChars(text);
  if (around && text.length > 110) {
    const j = nt.indexOf(nq);
    if (j > 40) { text = '…' + text.slice(j - 30); nt = normChars(text); }
  }
  let out = ''; let i = 0;
  for (;;) {
    const j = nt.indexOf(nq, i);
    if (j < 0) break;
    out += esc(text.slice(i, j)) + '<mark>' + esc(text.slice(j, j + nq.length)) + '</mark>';
    i = j + nq.length;
  }
  return out + esc(text.slice(i));
}

/** WhatsApp metin biçimleri: *kalın* _italik_ ~üstü çizili~ ```kod``` ve bağlantılar. */
function formatText(raw) {
  let s = esc(String(raw || '').replace(/\s+$/, ''));
  const codes = [];
  s = s.replace(/```([\s\S]+?)```/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\b((?:https?:\/\/|www\.)[^\s<]+[^\s<.,;:!?)\]'"])/gi, (url) => {
    const href = url.startsWith('www.') ? 'https://' + url : url;
    return `<a href="${href}" target="_blank" rel="noopener noreferrer">${url}</a>`;
  });
  const wrap = (ch, tag) => {
    const c = ch.replace(/[*~]/g, '\\$&');
    const re = new RegExp(`(^|[\\s(>"'“])${c}(?!\\s)([^${c}\\n]+?)(?<!\\s)${c}(?=$|[\\s.,!?;:)<"'”])`, 'gm');
    s = s.replace(re, `$1<${tag}>$2</${tag}>`);
  };
  wrap('*', 'b'); wrap('_', 'i'); wrap('~', 's');
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  return s;
}

/* ------------------------------------------------------------ renkler / avatar */
const AVATAR_COLORS = ['#e17076', '#7bc862', '#e5ca77', '#65aadd', '#a695e7', '#ee7aae', '#6ec9cb', '#faa774', '#d4a15b', '#5fb6a3', '#c57bd6', '#8fa8c9'];
const SENDER_LIGHT = ['#1f7aec', '#e5427a', '#008069', '#a35cd8', '#d5650b', '#1f8f8f', '#c43e1c', '#5f66cd', '#3a8b2e', '#b3005b', '#0277bd', '#8a6d00'];
const SENDER_DARK = ['#53bdeb', '#ff72a1', '#06cf9c', '#c79ffe', '#ffa75b', '#5cd6d6', '#ff8b66', '#a5b4ff', '#7fd36a', '#ff6fa8', '#4fc3f7', '#e2c35b'];
const darkMQ = window.matchMedia('(prefers-color-scheme: dark)');
function hashStr(s) { let h = 7; for (const c of String(s || '')) h = (h * 31 + c.codePointAt(0)) | 0; return Math.abs(h); }
function senderColor(key) { const p = darkMQ.matches ? SENDER_DARK : SENDER_LIGHT; return p[hashStr(key) % p.length]; }
function initials(name) {
  const clean = String(name || '').replace(/^~/, '').replace(/\(.*\)/, '').replace(/[^\p{L}\p{N}\s]/gu, '').trim();
  if (!clean) return '#';
  const parts = clean.split(/\s+/);
  const a = [...parts[0]][0] || '';
  const b = parts.length > 1 ? ([...parts[parts.length - 1]][0] || '') : '';
  return (a + b).toLocaleUpperCase('tr');
}
function avatarEl(c, cls = '') {
  const el = div('avatar ' + cls);
  const fill = () => { el.style.background = AVATAR_COLORS[hashStr(c.jid || c.name) % AVATAR_COLORS.length]; el.textContent = c.group ? '👥' : initials(c.name); };
  if (c.avatar) {
    const img = new Image();
    img.loading = 'lazy'; img.alt = '';
    img.onerror = () => { img.remove(); fill(); };
    img.src = mediaUrl(c.avatar);
    el.appendChild(img);
  } else fill();
  return el;
}

/* ------------------------------------------------------------ arşiv & sohbet listesi */
async function loadState(preferId) {
  const data = await api('state');
  state.archives = data.archives;
  $('#versionLine').textContent = `WhatsApp Arşivi ${data.version || ''} · açık kaynak`;
  const sel = $('#archiveSel');
  sel.innerHTML = '';
  for (const a of state.archives) {
    const o = document.createElement('option');
    o.value = a.id; o.textContent = a.name; sel.appendChild(o);
  }
  sel.hidden = state.archives.length < 2;
  const saved = preferId || store('aid');
  const pick = state.archives.find((a) => a.id === saved) || state.archives[0];
  if (pick) await selectArchive(pick.id);
  else showNoArchive();
}

function showNoArchive() {
  state.aid = null; state.archive = null; state.chats = []; state.chatById = new Map(); state.chat = null;
  $('#archiveName').textContent = '';
  $('#chat').hidden = true; $('#welcome').hidden = false;
  $('#welcomeText').innerHTML = 'Henüz içe aktarılmış bir arşiv yok.<br>iPhone\'unuzun bu bilgisayardaki yedeğinden WhatsApp sohbetlerinizi içe aktarın.';
  $('#welcomeStats').innerHTML = '';
  $('#welcomeImport').hidden = false;
  renderSidebar();
}

async function selectArchive(id) {
  state.aid = id; store('aid', id);
  $('#archiveSel').value = id;
  state.chat = null; closeChatSearch();
  $('#chat').hidden = true; $('#welcome').hidden = false; $('#welcomeImport').hidden = true;
  $('#chatList').innerHTML = '<div class="empty-list"><span class="spinner"></span></div>';
  const data = await api('chats?' + qs({ a: id }));
  state.archive = data.archive;
  state.chats = data.chats;
  state.chatById = new Map(data.chats.map((c) => [c.id, c]));
  const a = data.archive;
  $('#archiveName').textContent = a.name + (a.backup_date ? ' · ' + fmtShort.format(a.backup_date) : '');
  $('#welcomeText').textContent = 'Soldan bir sohbet seçin.';
  const msgCount = state.chats.reduce((s, c) => s + c.count, 0);
  let stats = `<b>${nf.format(state.chats.length)}</b> sohbet · <b>${nf.format(msgCount)}</b> mesaj`;
  if (a.first_date && a.last_date) stats += `<br>${fmtDayLong.format(a.first_date)} – ${fmtDayLong.format(a.last_date)}`;
  if (a.missing_files) stats += `<br><small>${nf.format(a.missing_files)} dosya yedekte eksikti.</small>`;
  $('#welcomeStats').innerHTML = stats;
  renderSidebar();
}

function chatItem(c, q) {
  const el = div('chat-item' + (state.chat && state.chat.id === c.id ? ' active' : ''));
  el.dataset.id = c.id;
  el.appendChild(avatarEl(c));
  el.appendChild(div('ci-body',
    `<div class="ci-top"><span class="ci-name">${q ? highlight(c.name, q) : esc(c.name)}</span><span class="ci-time">${listDate(c.last)}</span></div>` +
    `<div class="ci-bottom"><span class="ci-prev">${esc(c.preview)}</span></div>`));
  el.onclick = () => openChat(c);
  return el;
}

function resultItem(r, q, inChat) {
  const el = div('res-item');
  if (!inChat) {
    const c = state.chatById.get(r.chat);
    if (c) el.appendChild(avatarEl(c, 'sm'));
  }
  const who = r.me ? 'Sen: ' : (r.group && r.sender ? r.sender + ': ' : '');
  const title = inChat ? (r.me ? 'Sen' : (r.sender || state.chat?.name || '')) : r.chat_name;
  const date = inChat ? `${fmtShort.format(r.ts)} ${fmtTime.format(r.ts)}` : listDate(r.ts);
  el.appendChild(div('res-body',
    `<div class="res-top"><span class="res-chat">${esc(title)}</span><span class="res-date">${esc(date)}</span></div>` +
    `<div class="res-text">${inChat ? '' : esc(who)}${highlight(r.text, q, true)}</div>`));
  el.onclick = () => {
    const c = state.chatById.get(r.chat);
    if (!c) return;
    if (state.chat && state.chat.id === c.id) jumpTo(r.id);
    else openChat(c, { around: r.id });
  };
  return el;
}

function renderSidebar() {
  const list = $('#chatList');
  list.innerHTML = '';
  if (!state.aid) {
    list.appendChild(div('empty-list', 'Henüz arşiv yok.<br>Sağ üstteki menüden<br><b>iPhone yedeğinden içe aktar</b>’ı seçin.'));
    return;
  }
  const q = $('#q').value.trim();
  const frag = document.createDocumentFragment();
  let chats = state.chats;
  if (q) {
    const nq = normChars(q);
    const digits = q.replace(/\D/g, '');
    chats = chats.filter((c) => normChars(c.name).includes(nq) || (digits.length >= 3 && (c.phone || '').replace(/\D/g, '').includes(digits)));
    if (chats.length) frag.appendChild(div('section-title', 'Sohbetler'));
  }
  for (const c of chats) frag.appendChild(chatItem(c, q));
  if (q) {
    if (q.length >= 2) {
      frag.appendChild(div('section-title', 'Mesajlar'));
      if (state.globalQ === q && state.globalResults) {
        if (!state.globalResults.length) frag.appendChild(div('res-note', 'Eşleşen mesaj bulunamadı.'));
        for (const r of state.globalResults) frag.appendChild(resultItem(r, q, false));
        if (state.globalResults.length >= 150) frag.appendChild(div('res-note', 'İlk 150 sonuç gösteriliyor. Aramayı daraltın.'));
      } else {
        frag.appendChild(div('res-note', '<span class="spinner"></span>'));
      }
    } else if (!chats.length) {
      frag.appendChild(div('res-note', 'Eşleşen sohbet yok.'));
    }
  } else if (!chats.length) {
    frag.appendChild(div('empty-list', 'Bu arşivde sohbet bulunamadı.'));
  }
  list.appendChild(frag);
}

const runGlobalSearch = debounce(async () => {
  const q = $('#q').value.trim();
  if (q.length < 2 || !state.aid) return;
  try {
    const data = await api('search?' + qs({ a: state.aid, q }));
    if ($('#q').value.trim() !== q) return;
    state.globalQ = q; state.globalResults = data.results;
    renderSidebar();
  } catch (e) { toast(e.message); }
}, 300);

/* ------------------------------------------------------------ sohbet görünümü */
const box = () => $('#messages');
const isGroupChat = () => !!(state.chat && state.chat.group);
const senderKey = (m) => (m.kind === 'system' ? 'sys' : m.me ? 'me' : (m.sender_jid || 'them'));
const isFirst = (m, prev) => !prev || dayKey(prev.ts) !== dayKey(m.ts) || senderKey(prev) !== senderKey(m);

async function openChat(c, opts = {}) {
  const token = ++state.openToken;
  const changed = !state.chat || state.chat.id !== c.id;
  state.chat = c;
  for (const el of document.querySelectorAll('.chat-item')) el.classList.toggle('active', +el.dataset.id === c.id);
  $('#welcome').hidden = true; $('#chat').hidden = false;
  const av = $('#chatAvatar'); const newAv = avatarEl(c); newAv.id = 'chatAvatar'; av.replaceWith(newAv);
  $('#chatName').textContent = c.name;
  updateLoadStatus();
  if (changed && !$('#searchPanel').hidden) { $('#chatQ').value = ''; $('#chatResults').innerHTML = ''; }
  if (changed && !$('#infoPanel').hidden) openInfo();

  $('#msgList').innerHTML = '';
  state.msgs = []; state.moreBefore = false; state.moreAfter = false;
  state.loadingOlder = state.loadingNewer = false;
  $('#topLoader').hidden = false;
  try {
    const params = { a: state.aid, c: c.id, around: opts.around, date: opts.date, n: opts.around || opts.date ? 200 : PAGE_FIRST };
    const data = await api('messages?' + qs(params));
    if (token !== state.openToken) return;
    state.msgs = data.messages;
    state.moreBefore = !!data.more_before;
    state.moreAfter = !!data.more_after;
    $('#msgList').appendChild(renderBatch(state.msgs, null));
    if (data.target) {
      state.stickBottom = false;
      scrollToMsg(data.target, true);
    } else {
      state.stickBottom = true;
      box().scrollTop = box().scrollHeight;
      setTimeout(() => { state.stickBottom = false; }, 4000);
    }
    if (opts.date && !data.target) toast('Bu tarihten sonra mesaj yok; en yeni mesajlar gösteriliyor.');
  } catch (e) {
    toast(e.message);
  } finally {
    if (token === state.openToken) $('#topLoader').hidden = true;
  }
  updateToBottom();
  if (token === state.openToken) fillAll(token);
}

/** Sohbetin kalanını (ilk mesaja kadar) arka planda sayfa sayfa yükler. */
async function fillAll(token) {
  updateLoadStatus();
  while (token === state.openToken && (state.moreBefore || state.moreAfter)) {
    let ok = true;
    if (state.moreAfter) ok = await loadNewer(PAGE_FILL);
    if (ok && token === state.openToken && state.moreBefore) ok = await loadOlder(PAGE_FILL);
    if (!ok) break;
    updateLoadStatus();
    await new Promise((r) => setTimeout(r, 10));
  }
  if (token === state.openToken) updateLoadStatus();
}

function updateLoadStatus() {
  const c = state.chat;
  if (!c) return;
  const sub = [];
  if (c.group) sub.push('Grup');
  if (c.phone && c.phone !== c.name) sub.push(c.phone);
  sub.push(`${nf.format(c.count)} mesaj`);
  if (state.moreBefore || state.moreAfter || !state.msgs.length) {
    const pct = c.count ? Math.min(99, Math.floor((state.msgs.length / c.count) * 100)) : 0;
    sub.push(`yükleniyor %${pct}`);
  }
  $('#chatSub').textContent = sub.join(' · ');
}

function renderBatch(msgs, prev) {
  const frag = document.createDocumentFragment();
  for (const m of msgs) {
    if (!prev || dayKey(prev.ts) !== dayKey(m.ts)) frag.appendChild(div('day', `<span>${esc(dayLabel(m.ts))}</span>`));
    frag.appendChild(msgEl(m, prev));
    prev = m;
  }
  return frag;
}

function timeSpan(m, cls = 'meta') { return `<span class="${cls}">${fmtTime.format(m.ts)}</span>`; }

function msgEl(m, prev) {
  if (m.kind === 'system') {
    const d = div('sys', `<span>${esc(m.text)}</span>`);
    d.dataset.id = m.id;
    return d;
  }
  const first = isFirst(m, prev);
  const row = div('row ' + (m.me ? 'out' : 'in') + (first ? ' first' : ''));
  row.dataset.id = m.id;
  const b = div('bubble');
  row.appendChild(b);

  if (first && isGroupChat() && !m.me && m.sender && m.kind !== 'sticker') {
    const s = div('sender'); s.textContent = m.sender; s.style.color = senderColor(m.sender_jid || m.sender);
    b.appendChild(s);
  }
  if (m.reply) b.appendChild(quoteEl(m.reply));

  const addText = (text, cls = 'text') => { b.appendChild(div(cls, formatText(text) + timeSpan(m))); };
  const addMetaBlock = () => { b.appendChild(div('meta-block', fmtTime.format(m.ts))); };
  const missing = (label) => { b.appendChild(div('missing-media', `${label} <small>— dosya yedekte yok</small>`)); };

  switch (m.kind) {
    case 'text':
      addText(m.text);
      break;
    case 'deleted':
      b.appendChild(div('text deleted', '🚫 Bu mesaj silindi' + timeSpan(m)));
      break;
    case 'image':
    case 'video':
    case 'gif': {
      const label = { image: '📷 Fotoğraf', video: '🎥 Video', gif: 'GIF' }[m.kind];
      if (m.media) {
        b.appendChild(m.kind === 'image' ? imageEl(m) : videoEl(m));
        if (m.caption) addText(m.caption, 'caption');
        else { b.classList.add('media-only'); b.appendChild(div('meta overlay', fmtTime.format(m.ts))); }
      } else {
        missing(label);
        if (m.caption) addText(m.caption, 'caption'); else addMetaBlock();
      }
      break;
    }
    case 'sticker':
      if (m.media) {
        const img = new Image(); img.className = 'sticker'; img.loading = 'lazy'; img.alt = 'Çıkartma';
        img.onload = onMediaLoad; img.src = mediaUrl(m.media);
        b.classList.add('sticker-b'); b.appendChild(img);
        b.appendChild(div('meta overlay', fmtTime.format(m.ts)));
      } else { missing('Çıkartma'); addMetaBlock(); }
      break;
    case 'voice':
    case 'audio': {
      const label = m.kind === 'voice' ? '🎤 Sesli mesaj' : '🎵 Ses dosyası';
      if (m.media) {
        b.appendChild(div('audio-label', `${label}${m.duration ? ' · ' + durStr(m.duration) : ''}`));
        const a = document.createElement('audio');
        a.controls = true; a.preload = 'none'; a.src = mediaUrl(m.media);
        b.appendChild(a);
      } else missing(label);
      if (m.caption) addText(m.caption, 'caption'); else addMetaBlock();
      break;
    }
    case 'document':
      b.appendChild(docEl(m));
      if (m.text) addText(m.text); else addMetaBlock();
      break;
    case 'location': {
      const hasCoords = typeof m.lat === 'number' && typeof m.lon === 'number' && (m.lat || m.lon);
      const el = document.createElement(hasCoords ? 'a' : 'div');
      el.className = 'loc';
      if (hasCoords) { el.href = `https://www.google.com/maps?q=${m.lat},${m.lon}`; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
      el.innerHTML = `<div class="loc-title">📍 ${esc(m.text || 'Konum')}</div>` + (hasCoords ? `<small>${m.lat.toFixed(5)}, ${m.lon.toFixed(5)} · Haritada aç</small>` : '');
      b.appendChild(el); addMetaBlock();
      break;
    }
    case 'contact':
      b.appendChild(div('vcard', `👤 ${esc(m.contact_name || 'Kişi kartı')}`)); addMetaBlock();
      break;
    case 'poll':
      addText('📊 Anket' + (m.text ? ': ' + m.text : ''));
      break;
    default:
      addText(m.text || '');
  }
  return row;
}

function imageEl(m) {
  const img = new Image();
  img.className = 'photo'; img.loading = 'lazy'; img.alt = m.caption || 'Fotoğraf';
  img.dataset.caption = m.caption || '';
  img.dataset.ts = m.ts;
  img.onload = onMediaLoad;
  img.onerror = () => {
    // ör. HEIC gibi gösterilemeyen biçimler: sistemde açma kartına dönüştür
    const card = docEl({ ...m, file_name: m.media.split('/').pop() });
    img.replaceWith(card);
  };
  img.onclick = () => openPhotoFromMessage(m);
  img.src = mediaUrl(m.media);
  return img;
}

function videoEl(m) {
  const v = document.createElement('video');
  v.className = 'video';
  v.src = mediaUrl(m.media);
  v.onloadedmetadata = onMediaLoad;
  if (m.kind === 'gif') { v.autoplay = true; v.loop = true; v.muted = true; v.playsInline = true; v.preload = 'auto'; }
  else { v.controls = true; v.preload = 'metadata'; }
  return v;
}

function docEl(m) {
  const name = m.file_name || (m.media ? m.media.split('/').pop() : 'Belge');
  const ext = (name.includes('.') ? name.split('.').pop() : 'DOSYA').toUpperCase().slice(0, 4);
  const el = div('doc' + (m.media ? '' : ' missing'));
  const sub = m.media ? [sizeStr(m.size), ext].filter(Boolean).join(' · ') + ' · Açmak için tıklayın' : 'Dosya yedekte yok';
  el.innerHTML = `<div class="doc-icon">${esc(ext)}</div><div><div class="doc-name">${esc(name)}</div><div class="doc-sub">${esc(sub)}</div></div>`;
  if (m.media) {
    el.title = 'Bilgisayardaki varsayılan programla aç';
    el.onclick = async () => {
      try { await api('open', { body: { a: state.aid, path: m.media } }); } catch (e) { toast(e.message); }
    };
  }
  return el;
}

function quoteEl(r) {
  const q = div('quote');
  const color = r.me ? (darkMQ.matches ? '#06cf9c' : '#008069') : senderColor(r.sender_jid || r.sender);
  q.innerHTML = `<div class="q-bar" style="background:${color}"></div><div class="q-body"><div class="q-name" style="color:${color}">${esc(r.sender || '')}</div><div class="q-text">${esc(r.text || '')}</div></div>`;
  if (r.thumb) { const img = new Image(); img.loading = 'lazy'; img.alt = ''; img.src = mediaUrl(r.thumb); q.appendChild(img); }
  q.title = 'Alıntılanan mesaja git';
  q.onclick = (e) => { e.stopPropagation(); jumpTo(r.id); };
  return q;
}

function onMediaLoad() {
  if (state.stickBottom) box().scrollTop = box().scrollHeight;
}

function scrollToMsg(id, flash) {
  const el = $(`#msgList [data-id="${id}"]`);
  if (!el) return false;
  el.scrollIntoView({ block: 'center' });
  if (flash) {
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 2200);
  }
  return true;
}

function jumpTo(id) {
  state.stickBottom = false;
  if (!scrollToMsg(id, true) && state.chat) openChat(state.chat, { around: id });
}

/** Mesaj alanında şu an görünen bir mesaj öğesi (kaydırma konumunu korumak için). */
function visibleAnchor() {
  // Yapışkan tarih etiketleri (.day) çapa olamaz; ekleme sırasında yerleri sabit kalır.
  const b = box(); const r = b.getBoundingClientRect();
  if (r.height) {
    for (const fy of [0.5, 0.3, 0.7, 0.1, 0.9]) {
      for (const fx of [0.5, 0.25, 0.75]) {
        const el = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
        const item = el && el.closest('#msgList > .row, #msgList > .sys');
        if (item) return item;
      }
    }
  }
  return $('#msgList > .row, #msgList > .sys');  // mevcut ilk mesaj: eklenen kadar aşağı kayar
}

/** Yeni eklenen (daha eski) mesajlardan sonra, var olan ilk mesajın baş/kuyruk durumunu düzeltir. */
function fixRunStart(prevMsg, firstMsg) {
  if (!prevMsg || !firstMsg || isFirst(firstMsg, prevMsg)) return;
  const row = $(`#msgList .row[data-id="${firstMsg.id}"]`);
  if (!row) return;
  row.classList.remove('first');
  const s = row.querySelector('.bubble > .sender');
  if (s) s.remove();
}

async function loadOlder(n = 120) {
  if (state.loadingOlder || !state.moreBefore || !state.msgs.length) return true;
  state.loadingOlder = true;
  const token = state.openToken;
  const first = state.msgs[0];
  let ok = true;
  $('#topLoader').hidden = box().scrollTop > 400;  // yalnızca en üstteyken göster
  try {
    const data = await api('messages?' + qs({ a: state.aid, c: state.chat.id, before: first.cur.join(','), n }));
    if (token !== state.openToken) return;
    const batch = data.messages;
    state.moreBefore = !!data.more_before;
    if (batch.length) {
      const b = box(); const list = $('#msgList');
      // Ekranda görünen mesajı yerinde tutmak için konumunu ölç (tarayıcının kendi
      // sabitlemesi büyük eklemelerde güvenilir değil; bu işlem sırasında kapatılır).
      const anchor = visibleAnchor();
      const before = anchor ? anchor.getBoundingClientRect().top : 0;
      b.style.overflowAnchor = 'none';
      const last = batch[batch.length - 1];
      const firstEl = list.firstElementChild;
      if (firstEl && firstEl.classList.contains('day') && dayKey(last.ts) === dayKey(first.ts)) firstEl.remove();
      fixRunStart(last, first);
      list.insertBefore(renderBatch(batch, null), list.firstChild);
      state.msgs = batch.concat(state.msgs);
      if (anchor && anchor.isConnected) b.scrollTop += anchor.getBoundingClientRect().top - before;
      b.style.overflowAnchor = '';
    }
  } catch (e) {
    toast(e.message);
    ok = false;
  } finally {
    if (token === state.openToken) { state.loadingOlder = false; $('#topLoader').hidden = true; }
  }
  requestAnimationFrame(checkEdges);
  return ok;
}

async function loadNewer(n = 120) {
  if (state.loadingNewer || !state.moreAfter || !state.msgs.length) return true;
  state.loadingNewer = true;
  const token = state.openToken;
  const last = state.msgs[state.msgs.length - 1];
  let ok = true;
  const b0 = box();
  $('#bottomLoader').hidden = b0.scrollHeight - b0.scrollTop - b0.clientHeight > 400;
  try {
    const data = await api('messages?' + qs({ a: state.aid, c: state.chat.id, after: last.cur.join(','), n }));
    if (token !== state.openToken) return;
    state.moreAfter = !!data.more_after;
    if (data.messages.length) {
      $('#msgList').appendChild(renderBatch(data.messages, last));
      state.msgs = state.msgs.concat(data.messages);
    }
  } catch (e) {
    toast(e.message);
    ok = false;
  } finally {
    if (token === state.openToken) { state.loadingNewer = false; $('#bottomLoader').hidden = true; }
  }
  updateToBottom();
  requestAnimationFrame(checkEdges);
  return ok;
}

function checkEdges() {
  if (!state.chat) return;
  const b = box();
  if (b.scrollTop < 900 && state.moreBefore) loadOlder();
  if (b.scrollHeight - b.scrollTop - b.clientHeight < 900 && state.moreAfter) loadNewer();
}

function updateToBottom() {
  const b = box();
  const far = b.scrollHeight - b.scrollTop - b.clientHeight > 500;
  $('#toBottom').hidden = !(state.chat && (far || state.moreAfter));
}

/* ------------------------------------------------------------ fotoğraf / video görüntüleyici */
const viewer = { items: [], index: 0 };

/** Sohbetin tüm fotoğraf, video, dosya ve bağlantı listesi (önbellekli). */
async function getChatMedia(chatId) {
  const key = `${state.aid}:${chatId}`;
  if (!state.mediaCache.has(key)) {
    const p = api('media?' + qs({ a: state.aid, c: chatId }));
    state.mediaCache.set(key, p);
    p.catch(() => state.mediaCache.delete(key));
  }
  return state.mediaCache.get(key);
}

async function openPhotoFromMessage(m) {
  try {
    const data = await getChatMedia(m.chat);
    const idx = data.photos.findIndex((x) => x.id === m.id);
    if (idx >= 0) return openViewer(data.photos, idx);
  } catch (e) { toast(e.message); }
  openViewer([m], 0);
}

function openViewer(items, index) {
  viewer.items = items; viewer.index = index;
  $('#lightbox').hidden = false;
  showViewer();
}

function showViewer() {
  const it = viewer.items[viewer.index];
  if (!it) return;
  const img = $('#lbImg'); const vid = $('#lbVideo');
  if (it.kind === 'image') {
    vid.pause(); vid.removeAttribute('src'); vid.hidden = true;
    img.hidden = false; img.src = mediaUrl(it.media);
  } else {
    img.hidden = true; img.removeAttribute('src');
    vid.hidden = false; vid.src = mediaUrl(it.media);
    const gif = it.kind === 'gif';
    vid.loop = gif; vid.muted = gif; vid.controls = !gif;
    vid.play().catch(() => {});
  }
  const who = it.me ? 'Sen' : (it.sender || (state.chat ? state.chat.name : ''));
  $('#lbInfo').innerHTML = `<b>${esc(who)}</b><span>${fmtDayLong.format(it.ts)} ${fmtTime.format(it.ts)}` +
    (viewer.items.length > 1 ? ` · ${nf.format(viewer.index + 1)} / ${nf.format(viewer.items.length)}` : '') + '</span>';
  $('#lbCaption').textContent = it.caption || '';
  $('.lb-prev').hidden = viewer.index <= 0;
  $('.lb-next').hidden = viewer.index >= viewer.items.length - 1;
  // komşu fotoğrafları önceden yükle (hızlı gezinme için)
  for (const j of [viewer.index - 1, viewer.index + 1]) {
    const n = viewer.items[j];
    if (n && n.kind === 'image') { const pre = new Image(); pre.src = mediaUrl(n.media); }
  }
}

function stepViewer(d) {
  const j = viewer.index + d;
  if (j < 0 || j >= viewer.items.length) return;
  viewer.index = j; showViewer();
}

function closeLightbox() {
  $('#lightbox').hidden = true;
  const vid = $('#lbVideo'); vid.pause(); vid.removeAttribute('src');
  $('#lbImg').removeAttribute('src');
}

/* ------------------------------------------------------------ sohbet bilgisi paneli (medya, dosyalar, linkler) */
const fmtMonth = new Intl.DateTimeFormat(TR, { month: 'long', year: 'numeric' });
const INFO_TABS = [['photos', 'Fotoğraflar'], ['videos', 'Videolar'], ['docs', 'Dosyalar'], ['links', 'Linkler']];
let tileObserver = null;

async function openInfo() {
  const c = state.chat;
  if (!c) return;
  closeChatSearch();
  $('#infoPanel').hidden = false;
  $('#infoTitle').textContent = c.group ? 'Grup bilgisi' : 'Kişi bilgisi';
  const av = avatarEl(c, 'xl'); av.id = 'infoAvatar'; $('#infoAvatar').replaceWith(av);
  $('#infoName').textContent = c.name;
  const sub = [];
  if (c.phone && c.phone !== c.name) sub.push(c.phone);
  if (c.group) sub.push('Grup');
  sub.push(`${nf.format(c.count)} mesaj`);
  $('#infoSub').textContent = sub.join(' · ');
  $('#infoTabs').innerHTML = '';
  $('#infoContent').innerHTML = '<div class="res-note"><span class="spinner"></span></div>';
  let data;
  try { data = await getChatMedia(c.id); } catch (e) { $('#infoContent').innerHTML = `<div class="res-note">${esc(e.message)}</div>`; return; }
  if (state.chat !== c) return;
  const tabs = INFO_TABS.filter(([k]) => data[k].length);
  if (!tabs.length) {
    $('#infoContent').innerHTML = '<div class="res-note">Bu sohbette fotoğraf, video, dosya veya link yok.</div>';
    return;
  }
  const active = tabs.some(([k]) => k === state.infoTab) ? state.infoTab : tabs[0][0];
  for (const [k, label] of tabs) {
    const t = document.createElement('button');
    t.className = 'tab' + (k === active ? ' active' : '');
    t.dataset.tab = k;
    t.innerHTML = `${label}<small>${nf.format(data[k].length)}</small>`;
    t.onclick = () => { state.infoTab = k; for (const x of document.querySelectorAll('#infoTabs .tab')) x.classList.toggle('active', x === t); renderInfoTab(k, data); };
    $('#infoTabs').appendChild(t);
  }
  renderInfoTab(active, data);
}

function closeInfo() { $('#infoPanel').hidden = true; if (tileObserver) tileObserver.disconnect(); }

function monthGroups(items) {
  const groups = []; let cur = null;
  for (let i = items.length - 1; i >= 0; i--) {  // en yeni üstte
    const it = items[i]; const d = new Date(it.ts); const k = d.getFullYear() * 12 + d.getMonth();
    if (!cur || cur.k !== k) { cur = { k, label: cap(fmtMonth.format(it.ts)), items: [] }; groups.push(cur); }
    cur.items.push(it);
  }
  return groups;
}

function gotoBtn(id) {
  const b = document.createElement('button');
  b.className = 'goto'; b.type = 'button'; b.textContent = 'Mesaja git';
  b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); jumpTo(id); };
  return b;
}

function renderInfoTab(key, data) {
  const box2 = $('#infoContent');
  box2.innerHTML = '';
  if (tileObserver) tileObserver.disconnect();
  const items = data[key];
  if (key === 'photos' || key === 'videos') {
    tileObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const tile = e.target; tileObserver.unobserve(tile);
        const it = tile._item;
        if (it.kind === 'image') {
          const img = new Image(); img.decoding = 'async'; img.alt = ''; img.src = mediaUrl(it.media); tile.prepend(img);
        } else {
          const v = document.createElement('video'); v.muted = true; v.preload = 'metadata';
          v.src = mediaUrl(it.media) + '#t=0.1'; tile.prepend(v);
        }
      }
    }, { root: $('#infoBody'), rootMargin: '300px 0px' });
    for (const g of monthGroups(items)) {
      box2.appendChild(div('month', esc(g.label)));
      const grid = div('grid');
      for (const it of g.items) {
        const tile = document.createElement('button');
        tile.type = 'button'; tile.className = 'tile'; tile._item = it;
        tile.title = `${fmtDayLong.format(it.ts)} ${fmtTime.format(it.ts)}`;
        if (it.kind !== 'image') tile.appendChild(div('vbadge', it.kind === 'gif' ? 'GIF' : `▶ ${durStr(it.duration)}`));
        tile.onclick = () => openViewer(items, items.indexOf(it));
        grid.appendChild(tile);
        tileObserver.observe(tile);
      }
      box2.appendChild(grid);
    }
    return;
  }
  if (key === 'docs') {
    for (const g of monthGroups(items)) {
      box2.appendChild(div('month', esc(g.label)));
      for (const it of g.items) {
        const name = it.file_name || 'Dosya';
        const ext = (name.includes('.') ? name.split('.').pop() : 'DOSYA').toUpperCase().slice(0, 4);
        const row = div('doc-row' + (it.media ? '' : ' missing'));
        const sub = [it.media ? sizeStr(it.size) : 'Dosya yedekte yok', fmtShort.format(it.ts), it.me ? 'Sen' : (it.sender || '')].filter(Boolean).join(' · ');
        row.innerHTML = `<div class="doc-icon">${esc(ext)}</div><div class="dr-body"><div class="dr-name">${esc(name)}</div><div class="dr-sub">${esc(sub)}</div></div>`;
        row.appendChild(gotoBtn(it.id));
        if (it.media) {
          row.title = 'Bilgisayardaki varsayılan programla aç';
          row.onclick = async () => { try { await api('open', { body: { a: state.aid, path: it.media } }); } catch (e) { toast(e.message); } };
        }
        box2.appendChild(row);
      }
    }
    return;
  }
  // linkler
  for (const g of monthGroups(items)) {
    box2.appendChild(div('month', esc(g.label)));
    for (const it of g.items) {
      const row = div('link-row');
      const a = document.createElement('a');
      a.className = 'lr-main'; a.href = it.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.innerHTML = `<div class="lr-icon">🔗</div><div class="lr-body"><div class="lr-domain">${esc(it.domain)}</div><div class="lr-url">${esc(it.display)}</div></div>`;
      row.appendChild(a);
      const text = it.text && it.text.trim() !== it.display ? it.text : '';
      if (text) row.appendChild(div('lr-text', esc(text)));
      const foot = div('lr-sub', esc([fmtShort.format(it.ts), it.me ? 'Sen' : (it.sender || '')].filter(Boolean).join(' · ')));
      foot.appendChild(gotoBtn(it.id));
      row.appendChild(foot);
      box2.appendChild(row);
    }
  }
}

/* ------------------------------------------------------------ sohbet içi arama */
function openChatSearch() {
  if (!state.chat) return;
  closeInfo();
  $('#searchPanel').hidden = false;
  $('#chatQ').focus(); $('#chatQ').select();
  if (!$('#chatQ').value) $('#chatResults').innerHTML = '<div class="res-note">Bu sohbetteki mesajlarda arayın.</div>';
}
function closeChatSearch() { $('#searchPanel').hidden = true; }
const runChatSearch = debounce(async () => {
  const q = $('#chatQ').value.trim();
  const out = $('#chatResults');
  if (!state.chat) return;
  if (q.length < 2) { out.innerHTML = '<div class="res-note">En az 2 harf yazın.</div>'; return; }
  out.innerHTML = '<div class="res-note"><span class="spinner"></span></div>';
  try {
    const data = await api('search?' + qs({ a: state.aid, c: state.chat.id, q }));
    if ($('#chatQ').value.trim() !== q) return;
    out.innerHTML = '';
    if (!data.results.length) { out.innerHTML = '<div class="res-note">Eşleşen mesaj bulunamadı.</div>'; return; }
    for (const r of data.results) out.appendChild(resultItem(r, q, true));
  } catch (e) { out.innerHTML = `<div class="res-note">${esc(e.message)}</div>`; }
}, 300);

/* ------------------------------------------------------------ içe aktarma */
function showStep(name) {
  for (const s of ['Pick', 'Progress', 'Done']) $('#step' + s).hidden = s.toLowerCase() !== name;
  $('#importClose').hidden = name === 'progress';
}

function openImport() {
  $('#importDlg').hidden = false;
  showStep('pick');
  state.importSel = null;
  $('#pwRow').hidden = true; $('#pwInput').value = '';
  $('#pickError').hidden = true;
  $('#startImport').disabled = true;
  $('#manualPath').hidden = true;
  loadBackups();
}
function closeImport() { $('#importDlg').hidden = true; $('#pwInput').value = ''; }

async function loadBackups() {
  const list = $('#backupList');
  list.innerHTML = '<div class="no-backup"><span class="spinner"></span></div>';
  try {
    const data = await api('backups');
    state.importBackups = data.backups;
    renderBackups();
  } catch (e) { list.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

function renderBackups() {
  const list = $('#backupList');
  list.innerHTML = '';
  if (!state.importBackups.length) {
    list.innerHTML = '<div class="no-backup">Bu bilgisayarda iPhone yedeği bulunamadı.<br>Yukarıdaki <b>“Yedek nasıl alınır?”</b> adımlarını izleyip <b>Yenile</b>’ye basın ya da yedeğin bulunduğu klasörü seçin.</div>';
    $('.howto').open = true;
    return;
  }
  for (const bk of state.importBackups) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'backup' + (state.importSel && state.importSel.path === bk.path ? ' sel' : '');
    const sub = [bk.product, bk.ios ? 'iOS ' + bk.ios : '', bk.date ? 'Yedek: ' + fmtDayLong.format(bk.date) + ' ' + fmtTime.format(bk.date) : ''].filter(Boolean).join(' · ');
    el.innerHTML = `<div class="b-ico">📱</div><div class="b-body"><div class="b-name">${esc(bk.name)}</div><div class="b-sub" title="${esc(bk.path)}">${esc(sub)}</div></div>` +
      (bk.encrypted ? '<span class="badge lock">🔒 Şifreli</span>' : '<span class="badge">Şifresiz</span>');
    el.onclick = () => selectBackup(bk);
    list.appendChild(el);
  }
  if (!state.importSel) selectBackup(state.importBackups[0]);
}

function selectBackup(bk) {
  state.importSel = bk;
  for (const el of document.querySelectorAll('#backupList .backup')) el.classList.remove('sel');
  const idx = state.importBackups.indexOf(bk);
  const el = document.querySelectorAll('#backupList .backup')[idx];
  if (el) el.classList.add('sel');
  $('#pwRow').hidden = !bk.encrypted;
  $('#startImport').disabled = false;
  $('#pickError').hidden = true;
}

async function inspectFolder(path) {
  $('#pickError').hidden = true;
  try {
    const data = await api('inspect-folder', { body: { path } });
    for (const bk of data.backups.slice().reverse()) {
      if (!state.importBackups.some((x) => x.path === bk.path)) state.importBackups.unshift(bk);
    }
    state.importSel = null;
    renderBackups();
    selectBackup(state.importBackups.find((x) => x.path === data.backups[0].path));
  } catch (e) { showPickError(e.message); }
}
function showPickError(msg) { const el = $('#pickError'); el.textContent = msg; el.hidden = false; }

async function startImport() {
  const bk = state.importSel;
  if (!bk) return;
  const password = $('#pwInput').value;
  if (bk.encrypted && !password) { showPickError('Lütfen yedek şifresini girin.'); $('#pwInput').focus(); return; }
  try {
    await api('import', { body: { path: bk.path, password: bk.encrypted ? password : null } });
  } catch (e) { showPickError(e.message); return; }
  $('#pwInput').value = '';
  showStep('progress');
  pollImport();
}

async function pollImport() {
  clearTimeout(state.importPoll);
  let st;
  try { st = await api('import'); } catch (e) { state.importPoll = setTimeout(pollImport, 1500); return; }
  if (st.state === 'running') {
    $('#progPhase').textContent = st.phase;
    const bar = $('#progBar');
    const frac = st.bytes_total ? st.bytes_done / st.bytes_total : (st.total ? st.done / st.total : 0);
    if (st.total) { bar.classList.remove('indet'); bar.style.width = `${Math.min(100, frac * 100).toFixed(1)}%`; }
    else bar.classList.add('indet');
    $('#progDetail').textContent = st.total
      ? `${nf.format(st.done)} / ${nf.format(st.total)} dosya` + (st.bytes_total ? ` · ${sizeStr(st.bytes_done) || '0 B'} / ${sizeStr(st.bytes_total)}` : '')
      : '';
    state.importPoll = setTimeout(pollImport, 600);
    return;
  }
  if (st.state === 'done') {
    showStep('done');
    $('#doneIcon').className = 'done-icon'; $('#doneIcon').textContent = '✓';
    const lines = st.archives.map((a) => `<b>${esc(a.name)}</b><br>${nf.format(a.chat_count)} sohbet · ${nf.format(a.message_count)} mesaj` +
      (a.linked_files ? `<br><small>${nf.format(a.linked_files)} medya dosyası (${sizeStr(a.linked_bytes)}) kopyalanmadan bağlandı; ek disk alanı kullanılmadı.</small>` : '') +
      (a.missing_files ? `<br><small>${nf.format(a.missing_files)} dosya yedekte eksikti.</small>` : ''));
    $('#doneText').innerHTML = 'İçe aktarma tamamlandı.<br><br>' + lines.join('<br><br>');
    $('#doneBack').hidden = true;
    $('#doneOk').textContent = 'Arşivi aç';
    $('#doneOk').onclick = async () => { closeImport(); await loadState(st.archives[0] && st.archives[0].id); };
    return;
  }
  if (st.state === 'error') {
    showStep('done');
    $('#doneIcon').className = 'done-icon err'; $('#doneIcon').textContent = '!';
    $('#doneText').textContent = st.message;
    $('#doneBack').hidden = false;
    $('#doneOk').textContent = 'Kapat';
    $('#doneOk').onclick = closeImport;
    return;
  }
  // iptal / boşta
  closeImport();
  if (st.state === 'cancelled') toast('İçe aktarma iptal edildi.');
}

/* ------------------------------------------------------------ menü */
function toggleMenu(force) {
  const m = $('#menu');
  m.hidden = force === undefined ? !m.hidden : !force;
  m.querySelector('[data-act="rename"]').disabled = !state.aid;
  m.querySelector('[data-act="delete"]').disabled = !state.aid;
}
async function menuAction(act) {
  toggleMenu(false);
  if (act === 'import') return openImport();
  if (!state.archive) return;
  if (act === 'rename') {
    const name = window.prompt('Arşivin yeni adı:', state.archive.name);
    if (!name || !name.trim()) return;
    try { await api('rename-archive', { body: { a: state.aid, name: name.trim() } }); await loadState(state.aid); } catch (e) { toast(e.message); }
  }
  if (act === 'delete') {
    const ok = window.confirm(`“${state.archive.name}” arşivi silinsin mi?\n\nİçe aktarılan mesajlar ve medyalar bu bilgisayardan silinir. Bu işlem geri alınamaz. (iPhone yedeğiniz etkilenmez.)`);
    if (!ok) return;
    try { await api('delete-archive', { body: { a: state.aid } }); store('aid', ''); await loadState(); toast('Arşiv silindi.'); } catch (e) { toast(e.message); }
  }
}

/* ------------------------------------------------------------ olaylar */
function bind() {
  $('#q').addEventListener('input', () => {
    const q = $('#q').value.trim();
    if (q !== state.globalQ) state.globalResults = null;
    renderSidebar();
    if (q.length >= 2) runGlobalSearch();
  });
  $('#archiveSel').addEventListener('change', (e) => selectArchive(e.target.value).catch((err) => toast(err.message)));
  $('#menuBtn').addEventListener('click', (e) => { e.stopPropagation(); toggleMenu(); });
  $('#menu').addEventListener('click', (e) => { const b = e.target.closest('button[data-act]'); if (b && !b.disabled) menuAction(b.dataset.act); });
  document.addEventListener('click', (e) => { if (!$('#menu').hidden && !e.target.closest('#menu')) toggleMenu(false); });
  $('#welcomeImport').addEventListener('click', openImport);

  const b = box();
  let ticking = false;
  b.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; checkEdges(); updateToBottom(); });
  });
  for (const ev of ['wheel', 'mousedown', 'keydown', 'touchstart']) b.addEventListener(ev, () => { state.stickBottom = false; }, { passive: true });
  $('#toBottom').addEventListener('click', () => {
    if (state.moreAfter) openChat(state.chat);
    else box().scrollTo({ top: box().scrollHeight, behavior: 'smooth' });
  });

  $('#dateBtn').addEventListener('click', () => {
    const inp = $('#dateInput');
    if (state.msgs.length && !inp.value) {
      const d = new Date(state.msgs[0].ts);
      inp.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    try { inp.showPicker(); } catch (_) { inp.focus(); inp.click(); }
  });
  $('#dateInput').addEventListener('change', (e) => {
    const v = e.target.value;
    if (!v || !state.chat) return;
    const [y, mo, d] = v.split('-').map(Number);
    openChat(state.chat, { date: new Date(y, mo - 1, d).getTime() });
  });

  $('#chatSearchBtn').addEventListener('click', () => ($('#searchPanel').hidden ? openChatSearch() : closeChatSearch()));
  $('#closeSearch').addEventListener('click', closeChatSearch);
  $('#chatQ').addEventListener('input', runChatSearch);

  $('#lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox' || e.target.classList.contains('lb-close')) closeLightbox(); });
  $('.lb-prev').addEventListener('click', (e) => { e.stopPropagation(); stepViewer(-1); });
  $('.lb-next').addEventListener('click', (e) => { e.stopPropagation(); stepViewer(1); });
  $('#lbGoto').addEventListener('click', (e) => {
    e.stopPropagation();
    const it = viewer.items[viewer.index];
    closeLightbox();
    if (it) jumpTo(it.id);
  });
  // Sohbet başlığına (isim / profil) tıklayınca bilgi paneli
  $('.chat-head').addEventListener('click', (e) => {
    if (e.target.closest('button, input')) return;
    if ($('#infoPanel').hidden) openInfo(); else closeInfo();
  });
  $('#closeInfo').addEventListener('click', closeInfo);

  $('#importClose').addEventListener('click', closeImport);
  $('#refreshBackups').addEventListener('click', loadBackups);
  $('#pickFolder').addEventListener('click', async () => {
    const pyapi = window.pywebview && window.pywebview.api;
    if (pyapi && pyapi.pick_folder) {
      try { const p = await pyapi.pick_folder(); if (p) inspectFolder(p); } catch (e) { showPickError(String(e.message || e)); }
    } else {
      $('#manualPath').hidden = false; $('#manualPathInput').focus();
    }
  });
  $('#manualPathBtn').addEventListener('click', () => { const p = $('#manualPathInput').value.trim(); if (p) inspectFolder(p); });
  $('#manualPathInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#manualPathBtn').click(); });
  $('#pwInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') startImport(); });
  $('#startImport').addEventListener('click', startImport);
  $('#cancelImport').addEventListener('click', async () => { $('#progPhase').textContent = 'İptal ediliyor…'; try { await api('import-cancel', { body: {} }); } catch (_) { /* yok say */ } });
  $('#doneBack').addEventListener('click', () => { showStep('pick'); });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('#lightbox').hidden) return closeLightbox();
      if (!$('#importDlg').hidden && $('#stepProgress').hidden) return closeImport();
      if (!$('#menu').hidden) return toggleMenu(false);
      if (!$('#searchPanel').hidden) return closeChatSearch();
      if (!$('#infoPanel').hidden) return closeInfo();
    }
    if (!$('#lightbox').hidden) {
      if (e.key === 'ArrowLeft') $('.lb-prev').click();
      if (e.key === 'ArrowRight') $('.lb-next').click();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && state.chat) { e.preventDefault(); openChatSearch(); }
  });
  darkMQ.addEventListener('change', () => { if (state.chat) openChat(state.chat); });
}

bind();
loadState().catch((e) => toast(e.message));
// Tarayıcı modunda programın, sayfa açık olduğu sürece çalışmaya devam etmesi için
setInterval(() => { fetch('/api/ping', { credentials: 'same-origin' }).catch(() => {}); }, 20000);
