// Récupère (une fois) la liste publique des comptes suivis par un utilisateur X, sans connexion si X le permet.
import { Scraper } from '@the-convocation/twitter-scraper';
import fs from 'node:fs';
const user = process.argv[2];
const s = new Scraper();
if (process.env.X_AUTH_TOKEN && process.env.X_CT0) await s.setCookies([`auth_token=${process.env.X_AUTH_TOKEN}; Domain=.x.com; Path=/; Secure; HttpOnly`, `ct0=${process.env.X_CT0}; Domain=.x.com; Path=/; Secure`]);
const out = { user, at: new Date().toISOString(), profile: null, following: [], error: null };
try {
  const p = await s.getProfile(user);
  out.profile = { id: p.userId, name: p.name, following: p.followingCount, followers: p.followersCount };
  try {
    for await (const f of s.getFollowing(p.userId, 400)) out.following.push({ h: f.username, n: f.name, fol: f.followersCount, bio: (f.biography || '').slice(0, 140) });
  } catch (e) { out.error = 'following: ' + String(e).slice(0, 300); }
} catch (e) { out.error = 'profile: ' + String(e).slice(0, 300); }
fs.mkdirSync('data', { recursive: true });
fs.writeFileSync('data/following.json', JSON.stringify(out, null, 1));
// ajoute automatiquement les comptes suivis à la liste des influenceurs
if (out.following.length) { const cfg = JSON.parse(fs.readFileSync('config.json', 'utf8')); const low = new Set((cfg.influencers || []).map(h => h.toLowerCase())); for (const f of out.following) if (!low.has(f.h.toLowerCase())) { cfg.influencers.push(f.h); low.add(f.h.toLowerCase()); } fs.writeFileSync('config.json', JSON.stringify(cfg, null, 1)); }
console.log(out.profile, out.following.length, out.error);
