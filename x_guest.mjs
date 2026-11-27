// Lecture de X SANS compte (mode invité) avec la bibliothèque open source the-convocation/twitter-scraper.
// Limité aux tweets publics des comptes suivis (la recherche par ticker exige un compte).
import { Scraper, ErrorRateLimitStrategy, SearchMode } from '@the-convocation/twitter-scraper';
import fs from 'node:fs';
const cfg = JSON.parse(fs.readFileSync('config.json', 'utf8'));
const s = new Scraper({ rateLimitStrategy: new ErrorRateLimitStrategy() });
// Compte secondaire facultatif (cookies) : donne les tweets les plus RÉCENTS au lieu des tweets « populaires » montrés aux visiteurs.
const LOGGED = !!(process.env.X_AUTH_TOKEN && process.env.X_CT0);
let LOGGED_OK = false;
if (LOGGED) { try { await s.setCookies([`auth_token=${process.env.X_AUTH_TOKEN.trim()}; Domain=.x.com; Path=/; Secure; HttpOnly`, `ct0=${process.env.X_CT0.trim()}; Domain=.x.com; Path=/; Secure`]); LOGGED_OK = await Promise.race([s.isLoggedIn(), new Promise(r => setTimeout(() => r(false), 15000))]); } catch (e) { console.log('cookies refusés', String(e).slice(0, 160)); } console.log('compte X secondaire connecté :', LOGGED_OK); }
const out = { at: new Date().toISOString(), tweets: [], errors: {} };
let PREF = { vip: [], hidden: [] };
let prevAll = {}; try { prevAll = JSON.parse(fs.readFileSync('data/x_guest.json', 'utf8')); } catch (e) {}
// X limite à ~50 lectures par quart d'heure : les comptes d'actualité ne sont relus qu'un passage sur deux
const SKIP_NEWS = LOGGED_OK && Math.floor(Date.now() / 900000) % 2 === 1 && (prevAll.tweets || []).length > 0;
if (SKIP_NEWS) out.tweets = prevAll.tweets;
function mergeInflu() {
  const seen = new Set(out.influ.map(t => t.url)), lim = Date.now() - 30 * 86400e3;
  for (const t of (prevAll.influ || [])) if (!seen.has(t.url)) { seen.add(t.url); out.influ.push(t); }
  const HX = new Set((PREF.hidden || []).map(h => String(h).toLowerCase()));
  out.influ = out.influ.filter(t => Date.parse(t.t) > lim && !HX.has(String(t.h).toLowerCase())).sort((a, b) => Date.parse(b.t) - Date.parse(a.t)).slice(0, 2500);
}
// garde-fou : au bout de 5 min on enregistre ce qu'on a et on s'arrête
setTimeout(() => { try { mergeInflu(); fs.mkdirSync('data', { recursive: true }); fs.writeFileSync('data/x_guest.json', JSON.stringify(out)); } catch (e) {} console.log('x_guest: arrêt garde-fou'); process.exit(0); }, 600000).unref();
const BULL = /\b(beat|surge|soar|jump|rall(y|ies)|upgrade|record|bullish|breakout|deal|rate cut)\b/i, BEAR = /\b(miss|fall|drop|plunge|crash|downgrade|bearish|tariff|sanction|war|ban|probe|lawsuit|hack|default|recession)\b/i;
const tag = t => { const b = BULL.test(t), r = BEAR.test(t); return b && !r ? 'bull' : r && !b ? 'bear' : 'mix'; };
for (const a of (SKIP_NEWS ? [] : cfg.x_accounts || [])) {
  try {
    let n = 0;
    for await (const t of s.getTweets(a, 12)) {
      if (!t || !t.text) continue;
      out.tweets.push({ h: t.username || a, fol: 0, t: (t.timeParsed ? new Date(t.timeParsed) : new Date()).toISOString(), eng: (t.likes || 0) + 2 * (t.retweets || 0),
        url: t.permanentUrl || `https://x.com/${a}/status/${t.id}`, fr: t.text.slice(0, 400), tag: tag(t.text) });
      if (++n >= 12) break;
    }
  } catch (e) { out.errors[a] = String(e).slice(0, 160); }
  await new Promise(r => setTimeout(r, 1500));
}
out.influ = [];
let prevProf = prevAll.influ_prof || {};
out.influ_prof = { ...prevProf }; 
out.influ_mode = LOGGED_OK ? 'compte' : 'invite'; out.logged_try = LOGGED; let rateHit = false;
// rotation : on traite une tranche des comptes à chaque passage (toutes les 30 min), et on garde les tweets des passages précédents
// préférences réglées depuis le dashboard (étoile = favori relu à chaque passage, croix = compte ignoré)
try { const r = await fetch('https://lqefewiwrecdjommktgm.supabase.co/rest/v1/influ_prefs?id=eq.analyserxxx&select=vip,hidden,extra', { headers: { apikey: 'sb_publishable_IMMxd7lqThb0elf0Z2JwNQ_3Da4tdWq' }, signal: AbortSignal.timeout(8000) }); const j = await r.json(); if (j && j[0]) PREF = j[0]; } catch (e) { out.errors.prefs = String(e).slice(0, 80); }
const HID = new Set((PREF.hidden || []).map(h => String(h).toLowerCase()));
// comptes ajoutés depuis le dashboard : intégrés à la liste (config.json), et lus en priorité tant qu'ils n'ont aucun tweet en mémoire
const NEWX = [];
{ const low = new Set((cfg.influencers || []).map(h => h.toLowerCase()));
  for (const h of (PREF.extra || [])) { const x = String(h).trim().replace(/^@/, ''); if (/^[A-Za-z0-9_]{1,15}$/.test(x) && !low.has(x.toLowerCase())) { cfg.influencers.push(x); low.add(x.toLowerCase()); NEWX.push(x); } }
  if (NEWX.length) { try { const disk = JSON.parse(fs.readFileSync('config.json', 'utf8')); disk.influencers = cfg.influencers; fs.writeFileSync('config.json', JSON.stringify(disk, null, 1)); } catch (e) { out.errors.extra = String(e).slice(0, 80); } } }
cfg.influ_vip = [...(PREF.vip || []), ...(cfg.influ_vip || [])];
const INF = (cfg.influencers || []).filter(h => !HID.has(h.toLowerCase())), SL = LOGGED_OK ? 80 : 40;
const INFL = new Set((cfg.influencers || []).map(h => String(h).toLowerCase()));
// priorité : les comptes qui ont fait des calls ($ticker ou CA) ces 7 derniers jours sont relus à chaque passage
const VIP = [...new Set([...NEWX, ...(PREF.extra || []).filter(h => !(prevAll.influ || []).some(t => String(t.h).toLowerCase() === String(h).toLowerCase())), ...(cfg.influ_vip || [])])].filter(h => INF.some(x => x.toLowerCase() === String(h).toLowerCase())).slice(0, 18);
const hot0 = [...new Set((prevAll.influ || []).filter(t => Date.parse(t.t) > Date.now() - 7 * 86400e3 && /\$[A-Za-z]{2,10}\b|\b0x[a-fA-F0-9]{40}\b/.test(t.fr || '')).map(t => t.h))].filter(h => INF.some(x => x.toLowerCase() === String(h).toLowerCase())).slice(0, 12);
// favoris (config.json → influ_vip) : toujours relus en premier
const hot = [...new Map([...VIP, ...hot0].map(h => [h.toLowerCase(), h])).values()].slice(0, 20);
const rest = INF.filter(h => !hot.some(x => x.toLowerCase() === h.toLowerCase()));
// rotation : on reprend là où le passage précédent s'est arrêté
let start = Math.max(0, rest.findIndex(h => h.toLowerCase() === String(prevAll.rot_next || '').toLowerCase()));
const order = [...rest.slice(start), ...rest.slice(0, start)];
const slice = [...hot, ...order.slice(0, Math.max(0, SL - hot.length))];
out.rot_next = order[0] || null;
// photos et abonnés : lus avec une session invitée séparée (le compte connecté renvoyait des erreurs sur les profils),
// 10 comptes manquants par passage, ceux qui échouent sont retentés 6 h plus tard
{ const sp = LOGGED_OK ? new Scraper({ rateLimitStrategy: new ErrorRateLimitStrategy() }) : s;
  const fail = prevAll.prof_fail || {}; out.prof_fail = {};
  for (const [k, v] of Object.entries(fail)) if (Date.now() - v < 6 * 36e5) out.prof_fail[k] = v;
  const miss = INF.filter(h => !out.influ_prof[h.toLowerCase()] && !out.prof_fail[h.toLowerCase()]).slice(0, 10);
  let ok = 0;
  for (const a of miss) {
    try { const p = await Promise.race([sp.getProfile(a), new Promise((_, r) => setTimeout(() => r(new Error('timeout')), 8000))]);
      out.influ_prof[a.toLowerCase()] = { h: p.username || a, n: p.name || '', fol: p.followersCount || 0, av: p.avatar || null }; ok++; }
    catch (e) { out.prof_fail[a.toLowerCase()] = Date.now(); if (!out.errors.profils) out.errors.profils = (a + ': ' + String(e)).slice(0, 160); if (/status: 429|Too Many Requests|Rate limit exceeded/i.test(String(e).split('| headers')[0])) break; }
  }
  out.prof_ok = ok; }
function mkRow(t, a) {
  const row = { h: t.username || a, n: t.name || '', t: (t.timeParsed ? new Date(t.timeParsed) : new Date()).toISOString(), eng: (t.likes || 0) + 2 * (t.retweets || 0),
    url: t.permanentUrl || `https://x.com/${t.username || a}/status/${t.id}`, fr: t.text.slice(0, 500), rt: !!t.isRetweet, img: (t.photos && t.photos[0] && t.photos[0].url) || null };
  if (t.isQuoted && t.quotedStatus && t.quotedStatus.username && INFL.has(t.quotedStatus.username.toLowerCase()) && t.quotedStatus.username.toLowerCase() !== String(row.h).toLowerCase())
    row.q = { id: t.quotedStatusId || null, h: t.quotedStatus.username, tx: (t.quotedStatus.text || '').slice(0, 300) };
  return row;
}
// BALAYAGE RAPIDE (compte connecté) : une recherche X « from:a OR from:b … » couvre ~20 comptes d'un coup.
// ~11 recherches suffisent pour toute la liste : chaque compte est vu à chaque passage (15 min) au lieu de toutes les 4-5 h.
// Les comptes dont la recherche a échoué retombent dans la lecture compte par compte ci-dessous.
const covered = new Set();
if (LOGGED_OK) {
  const groups = []; let cur = [];
  const qOf = g => '(' + g.map(h => 'from:' + h).join(' OR ') + ') -filter:replies';
  for (const h of INF) { const nx = [...cur, h]; if (qOf(nx).length > 480 && cur.length) { groups.push(cur); cur = [h]; } else cur = nx; }
  if (cur.length) groups.push(cur);
  let okG = 0, nT = 0;
  for (const g of groups) {
    try {
      const seen = new Set(); let cursor;
      for (let page = 0; page < 2; page++) {
        const r = await Promise.race([s.fetchSearchTweets(qOf(g), 20, SearchMode.Latest, cursor), new Promise((_, rj) => setTimeout(() => rj(new Error('timeout')), 15000))]);
        const tw = (r && r.tweets) || [];
        for (const t of tw) { if (!t || !t.text || seen.has(t.id)) continue; seen.add(t.id); out.influ.push(mkRow(t, t.username)); nT++; }
        // 2e page seulement si la 1re est pleine de tweets récents (sinon tout le quart d'heure est déjà couvert)
        const oldest = tw.length ? Math.min(...tw.map(t => t.timeParsed ? +new Date(t.timeParsed) : Date.now())) : 0;
        if (!r || !r.next || tw.length < 15 || oldest < Date.now() - 45 * 60e3) break;
        cursor = r.next; await new Promise(rr => setTimeout(rr, 800));
      }
      g.forEach(h => covered.add(h.toLowerCase())); okG++;
    } catch (e) { out.errors.balayage = String(e).slice(0, 160); if (/status: 429|Too Many Requests|Rate limit exceeded/i.test(String(e).split('| headers')[0])) break; }
    await new Promise(rr => setTimeout(rr, 900));
  }
  out.sweep = { groups: groups.length, ok: okG, tweets: nT, covered: covered.size };
}
// filet de sécurité : 8 comptes de la rotation sont quand même relus directement (un compte peut être absent de la recherche X)
const direct = covered.size ? [...new Set([...slice.filter(h => !covered.has(h.toLowerCase())), ...order.slice(0, 8)])] : slice;
for (const a of direct) {
  if (!hot.includes(a)) { const k = order.indexOf(a); out.rot_next = order[(k + 1) % order.length]; }
  try {
    let n = 0;
    // connecté : tweets ET réponses. Une réponse est gardée seulement si elle répond à un autre compte de la liste
    // (ex. « agreed », « this will send ») ou si elle cite elle-même un $ticker : c'est une validation du call.
    // getTweetsAndReplies renvoie 404 avec cette version de la bibliothèque : on reste sur getTweets (tweets + citations)
    const gen = s.getTweets(a, LOGGED_OK ? 20 : 10);
    for await (const t of gen) {
      if (!t || !t.text) continue;
      const row = { h: t.username || a, n: t.name || '', t: (t.timeParsed ? new Date(t.timeParsed) : new Date()).toISOString(), eng: (t.likes || 0) + 2 * (t.retweets || 0),
        url: t.permanentUrl || `https://x.com/${a}/status/${t.id}`, fr: t.text.slice(0, 500), rt: !!t.isRetweet, img: (t.photos && t.photos[0] && t.photos[0].url) || null };
      if (t.isReply) {
        const pu = (t.inReplyToStatus && t.inReplyToStatus.username) || ((t.mentions || [])[0] || {}).username || '';
        const inList = pu && INFL.has(pu.toLowerCase()) && pu.toLowerCase() !== String(row.h).toLowerCase();
        if (!inList && !/\$[A-Za-z][A-Za-z0-9]{1,9}\b/.test(t.text)) continue;
        if (inList) row.re = { id: t.inReplyToStatusId || null, h: pu, tx: ((t.inReplyToStatus && t.inReplyToStatus.text) || '').slice(0, 300) };
      }
      if (t.isQuoted && t.quotedStatus && t.quotedStatus.username && INFL.has(t.quotedStatus.username.toLowerCase()) && t.quotedStatus.username.toLowerCase() !== String(row.h).toLowerCase())
        row.q = { id: t.quotedStatusId || null, h: t.quotedStatus.username, tx: (t.quotedStatus.text || '').slice(0, 300) };
      out.influ.push(row);
      if (++n >= (LOGGED_OK ? 20 : 10)) break;
    }
  } catch (e) { out.errors['influ:' + a] = String(e).slice(0, 160); if (/status: 429|Too Many Requests|Rate limit exceeded/i.test(String(e).split('| headers')[0])) { rateHit = true; } }
  if (rateHit) { if (!hot.includes(a)) out.rot_next = a; out.errors.rate_limit = 'limite X atteinte, reprise au prochain passage (à @' + a + ')'; break; }
  await new Promise(r => setTimeout(r, 1200));
}
mergeInflu();
fs.mkdirSync('data', { recursive: true });
fs.writeFileSync('data/x_guest.json', JSON.stringify(out));
console.log('x_guest', out.tweets.length, Object.keys(out.errors).length);
