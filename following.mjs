// Récupère (une fois) la liste publique des comptes suivis par un utilisateur X, sans connexion si X le permet.
import { Scraper } from '@the-convocation/twitter-scraper';
import fs from 'node:fs';
const user = process.argv[2];
const s = new Scraper();
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
console.log(out.profile, out.following.length, out.error);
