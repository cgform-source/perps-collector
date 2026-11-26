// Lecture de X SANS compte (mode invité) avec la bibliothèque open source the-convocation/twitter-scraper.
// Limité aux tweets publics des comptes suivis (la recherche par ticker exige un compte).
import { Scraper } from '@the-convocation/twitter-scraper';
import fs from 'node:fs';
const cfg = JSON.parse(fs.readFileSync('config.json', 'utf8'));
const s = new Scraper();
// Compte secondaire facultatif (cookies) : donne les tweets les plus RÉCENTS au lieu des tweets « populaires » montrés aux visiteurs.
const LOGGED = !!(process.env.X_AUTH_TOKEN && process.env.X_CT0);
if (LOGGED) { try { await s.setCookies([`auth_token=${process.env.X_AUTH_TOKEN}; Domain=.x.com; Path=/; Secure; HttpOnly`, `ct0=${process.env.X_CT0}; Domain=.x.com; Path=/; Secure`]); } catch (e) { console.log('cookies refusés', String(e).slice(0, 120)); } }
const out = { at: new Date().toISOString(), tweets: [], errors: {} };
function mergeInflu() {
  const seen = new Set(out.influ.map(t => t.url)), lim = Date.now() - 30 * 86400e3;
  for (const t of (prevAll.influ || [])) if (!seen.has(t.url)) { seen.add(t.url); out.influ.push(t); }
  out.influ = out.influ.filter(t => Date.parse(t.t) > lim).sort((a, b) => Date.parse(b.t) - Date.parse(a.t)).slice(0, 2500);
}
// garde-fou : au bout de 5 min on enregistre ce qu'on a et on s'arrête
setTimeout(() => { try { mergeInflu(); fs.mkdirSync('data', { recursive: true }); fs.writeFileSync('data/x_guest.json', JSON.stringify(out)); } catch (e) {} console.log('x_guest: arrêt garde-fou'); process.exit(0); }, 600000).unref();
const BULL = /\b(beat|surge|soar|jump|rall(y|ies)|upgrade|record|bullish|breakout|deal|rate cut)\b/i, BEAR = /\b(miss|fall|drop|plunge|crash|downgrade|bearish|tariff|sanction|war|ban|probe|lawsuit|hack|default|recession)\b/i;
const tag = t => { const b = BULL.test(t), r = BEAR.test(t); return b && !r ? 'bull' : r && !b ? 'bear' : 'mix'; };
for (const a of cfg.x_accounts || []) {
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
let prevAll = {}; try { prevAll = JSON.parse(fs.readFileSync('data/x_guest.json', 'utf8')); } catch (e) {}
out.influ = [];
// rotation : on traite une tranche des comptes à chaque passage (toutes les 30 min), et on garde les tweets des passages précédents
const INF = cfg.influencers || [], SL = LOGGED ? 80 : 40, off = (Math.floor(Date.now() / 1800000) * SL) % Math.max(1, INF.length);
// priorité : les comptes qui ont fait des calls ($ticker ou CA) ces 7 derniers jours sont relus à chaque passage
const hot = [...new Set((prevAll.influ || []).filter(t => Date.parse(t.t) > Date.now() - 7 * 86400e3 && /\$[A-Za-z]{2,10}\b|\b0x[a-fA-F0-9]{40}\b/.test(t.fr || '')).map(t => t.h))].filter(h => INF.some(x => x.toLowerCase() === String(h).toLowerCase())).slice(0, Math.round(SL * 0.4));
const rest = INF.filter(h => !hot.some(x => x.toLowerCase() === h.toLowerCase()));
const slice = INF.length <= SL ? INF : [...hot, ...[...rest, ...rest].slice(off % Math.max(1, rest.length), off % Math.max(1, rest.length) + SL - hot.length)];
out.influ_mode = LOGGED ? 'compte' : 'invite';
let prevProf = prevAll.influ_prof || {};
out.influ_prof = { ...prevProf }; let profBudget = 12;
for (const a of slice) {
  if (!prevProf[a.toLowerCase()] && profBudget-- > 0) try { const p = await Promise.race([s.getProfile(a), new Promise((_, r) => setTimeout(() => r(new Error('timeout')), 8000))]); out.influ_prof[a.toLowerCase()] = { h: p.username, n: p.name, fol: p.followersCount || 0, av: p.avatar || null }; } catch (e) {}
  try {
    let n = 0;
    for await (const t of s.getTweets(a, LOGGED ? 20 : 10)) {
      if (!t || !t.text) continue;
      out.influ.push({ h: t.username || a, n: t.name || '', t: (t.timeParsed ? new Date(t.timeParsed) : new Date()).toISOString(), eng: (t.likes || 0) + 2 * (t.retweets || 0),
        url: t.permanentUrl || `https://x.com/${a}/status/${t.id}`, fr: t.text.slice(0, 500), rt: !!t.isRetweet, img: (t.photos && t.photos[0] && t.photos[0].url) || null });
      if (++n >= (LOGGED ? 20 : 10)) break;
    }
  } catch (e) { out.errors['influ:' + a] = String(e).slice(0, 160); }
  await new Promise(r => setTimeout(r, 1200));
}
mergeInflu();
fs.mkdirSync('data', { recursive: true });
fs.writeFileSync('data/x_guest.json', JSON.stringify(out));
console.log('x_guest', out.tweets.length, Object.keys(out.errors).length);
