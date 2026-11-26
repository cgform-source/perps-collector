// Lecture de X SANS compte (mode invité) avec la bibliothèque open source the-convocation/twitter-scraper.
// Limité aux tweets publics des comptes suivis (la recherche par ticker exige un compte).
import { Scraper, ErrorRateLimitStrategy } from '@the-convocation/twitter-scraper';
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
// rotation : on traite une tranche des comptes à chaque passage (toutes les 30 min), et on garde les tweets des passages précédents
// préférences réglées depuis le dashboard (étoile = favori relu à chaque passage, croix = compte ignoré)
try { const r = await fetch('https://lqefewiwrecdjommktgm.supabase.co/rest/v1/influ_prefs?id=eq.analyserxxx&select=vip,hidden', { headers: { apikey: 'sb_publishable_IMMxd7lqThb0elf0Z2JwNQ_3Da4tdWq' }, signal: AbortSignal.timeout(8000) }); const j = await r.json(); if (j && j[0]) PREF = j[0]; } catch (e) { out.errors.prefs = String(e).slice(0, 80); }
const HID = new Set((PREF.hidden || []).map(h => String(h).toLowerCase()));
cfg.influ_vip = [...(PREF.vip || []), ...(cfg.influ_vip || [])];
const INF = (cfg.influencers || []).filter(h => !HID.has(h.toLowerCase())), SL = LOGGED_OK ? 80 : 40;
// priorité : les comptes qui ont fait des calls ($ticker ou CA) ces 7 derniers jours sont relus à chaque passage
const VIP = (cfg.influ_vip || []).filter(h => INF.some(x => x.toLowerCase() === String(h).toLowerCase())).slice(0, 15);
const hot0 = [...new Set((prevAll.influ || []).filter(t => Date.parse(t.t) > Date.now() - 7 * 86400e3 && /\$[A-Za-z]{2,10}\b|\b0x[a-fA-F0-9]{40}\b/.test(t.fr || '')).map(t => t.h))].filter(h => INF.some(x => x.toLowerCase() === String(h).toLowerCase())).slice(0, 12);
// favoris (config.json → influ_vip) : toujours relus en premier
const hot = [...new Map([...VIP, ...hot0].map(h => [h.toLowerCase(), h])).values()].slice(0, 20);
const rest = INF.filter(h => !hot.some(x => x.toLowerCase() === h.toLowerCase()));
// rotation : on reprend là où le passage précédent s'est arrêté
let start = Math.max(0, rest.findIndex(h => h.toLowerCase() === String(prevAll.rot_next || '').toLowerCase()));
const order = [...rest.slice(start), ...rest.slice(0, start)];
const slice = [...hot, ...order.slice(0, Math.max(0, SL - hot.length))];
out.rot_next = order[0] || null;
for (const a of slice) {
  if (!hot.includes(a)) { const k = order.indexOf(a); out.rot_next = order[(k + 1) % order.length]; }
  if (!prevProf[a.toLowerCase()] && profBudget-- > 0) try { const p = await Promise.race([s.getProfile(a), new Promise((_, r) => setTimeout(() => r(new Error('timeout')), 8000))]); out.influ_prof[a.toLowerCase()] = { h: p.username, n: p.name, fol: p.followersCount || 0, av: p.avatar || null }; } catch (e) {}
  try {
    let n = 0;
    for await (const t of s.getTweets(a, LOGGED_OK ? 20 : 10)) {
      if (!t || !t.text) continue;
      out.influ.push({ h: t.username || a, n: t.name || '', t: (t.timeParsed ? new Date(t.timeParsed) : new Date()).toISOString(), eng: (t.likes || 0) + 2 * (t.retweets || 0),
        url: t.permanentUrl || `https://x.com/${a}/status/${t.id}`, fr: t.text.slice(0, 500), rt: !!t.isRetweet, img: (t.photos && t.photos[0] && t.photos[0].url) || null });
      if (++n >= (LOGGED_OK ? 20 : 10)) break;
    }
  } catch (e) { out.errors['influ:' + a] = String(e).slice(0, 160); if (/rate|429/i.test(String(e))) { rateHit = true; } }
  if (rateHit) { if (!hot.includes(a)) out.rot_next = a; out.errors.rate_limit = 'limite X atteinte, reprise au prochain passage (à @' + a + ')'; break; }
  await new Promise(r => setTimeout(r, 1200));
}
mergeInflu();
fs.mkdirSync('data', { recursive: true });
fs.writeFileSync('data/x_guest.json', JSON.stringify(out));
console.log('x_guest', out.tweets.length, Object.keys(out.errors).length);
