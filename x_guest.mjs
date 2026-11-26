// Lecture de X SANS compte (mode invité) avec la bibliothèque open source the-convocation/twitter-scraper.
// Limité aux tweets publics des comptes suivis (la recherche par ticker exige un compte).
import { Scraper } from '@the-convocation/twitter-scraper';
import fs from 'node:fs';
const cfg = JSON.parse(fs.readFileSync('config.json', 'utf8'));
const s = new Scraper();
const out = { at: new Date().toISOString(), tweets: [], errors: {} };
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
out.influ = [];
out.influ_prof = {};
for (const a of cfg.influencers || []) {
  try { const p = await s.getProfile(a); out.influ_prof[a.toLowerCase()] = { h: p.username, n: p.name, fol: p.followersCount || 0, av: p.avatar || null }; } catch (e) {}
  try {
    let n = 0;
    for await (const t of s.getTweets(a, 10)) {
      if (!t || !t.text) continue;
      out.influ.push({ h: t.username || a, n: t.name || '', t: (t.timeParsed ? new Date(t.timeParsed) : new Date()).toISOString(), eng: (t.likes || 0) + 2 * (t.retweets || 0),
        url: t.permanentUrl || `https://x.com/${a}/status/${t.id}`, fr: t.text.slice(0, 500), rt: !!t.isRetweet, img: (t.photos && t.photos[0] && t.photos[0].url) || null });
      if (++n >= 10) break;
    }
  } catch (e) { out.errors['influ:' + a] = String(e).slice(0, 160); }
  await new Promise(r => setTimeout(r, 1200));
}
fs.mkdirSync('data', { recursive: true });
fs.writeFileSync('data/x_guest.json', JSON.stringify(out));
console.log('x_guest', out.tweets.length, Object.keys(out.errors).length);
