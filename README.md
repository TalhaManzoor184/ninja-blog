# PowerApps Ninja blog agent

Roz subah GitHub Actions par chalta hai. Har run mein thepowerappsninja.com ka agla **draft** post uthata hai, use Gemini se ek mukammal step-by-step tutorial mein dobara likhta hai, images banata hai aur **publish** kar deta hai. Post ka URL wahi rehta hai.

## Har post ke saath kya hota hai

1. **Queue.** Jin posts ko Google pehle se jaanta hai, yani jin par Search Console mein impressions the, woh pehle aati hain (`content/priority.txt`). Baqi drafts unke baad aate hain, naye pehle.
2. **Duplicates.** Ek hi topic ki do ya zyada posts milein to ek rakhi jati hai: woh jo Google ko pata hai, warna sab se lambi. Copy draft hi rehti hai aur report mein likhi jati hai. Uska kaam ka content rakhi gayi post mein mila diya jata hai.
3. **Backup.** Purana title aur content `state/backups/<id>.json` mein save hota hai. WordPress ki Revisions bhi rehti hain.
4. **Research.** Gemini Google Search se Microsoft ki maujooda documentation dekhta hai, jaise Azure AD ka naya naam Entra ID hona. Purani ghalat baatein theek ho jati hain.
5. **Likhai.** 900–2200 alfaaz ka article banta hai:
   - seedha jawab, key takeaways aur "Step 1, Step 2…" wale steps
   - code, comparison table, FAQ (FAQ schema ke saath) aur Microsoft Learn references
   - site ke tools aur doosri posts ke internal links
6. **Images.** Ek featured image (AI illustration jisme koi text, logo ya nakli screenshot nahi hota), andar ek illustration aur ek flow diagram (Mermaid).
7. **Quality check.** Lambai, sections, steps, title aur meta ki lambai, aur "As an AI" jaise jumle check hote hain. Fail hone par ek dafa dobara likhta hai. Phir bhi fail ho to post draft rehti hai aur report mein "Need your review" mein aati hai, aur agent agli post utha leta hai.
8. **Publish.** Title, content, excerpt, category, tags, featured image aur SEO title/description set hote hain. Slug aur date wahi rehte hain, kyunki aapke URLs mein date hai (`/2024/06/09/...`).

Har run ka khulasa Actions run ke **Summary** mein aur `state/report.md` mein hota hai.

## Setup (sirf ek dafa)

### 1. WordPress Application Password

1. WordPress admin mein **Users → Profile** kholein. User **Administrator** ya **Editor** hona chahiye.
2. Neeche **Application Passwords** mein naam likhein, jaise `ninja-blog-agent`, aur **Add New Application Password** dabayein.
3. Jo password dikhe use copy kar lein. Woh sirf ek dafa dikhta hai.

Yeh aapka asli login password nahi hai. Ise kabhi bhi wahin se **Revoke** kar sakte hain.

### 2. SEO fields plugin (Yoast / Rank Math)

WordPress REST API Yoast/Rank Math ka title aur description bahar se likhne nahi deti. Yeh chhota plugin is ki ijazat deta hai.

**Plugins → Add New → Upload Plugin** mein `wp-plugin/ninja-blog-agent-seo.zip` upload karke **Activate** karein. Plugin na lagayein to bhi agent chalega; meta description excerpt mein chali jayegi.

### 3. GitHub secrets

Repo mein **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `WP_URL` | `https://thepowerappsninja.com` |
| `WP_USER` | WordPress username (email nahi, username) |
| `WP_APP_PASSWORD` | Step 1 wala password (spaces ke saath ya baghair, dono chalte hain) |
| `GEMINI_API_KEY` | Wahi Gemini key jo Unknown Atlas mein hai |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | Ikhtiyari. Cloudflare Workers AI se free AI images (roz ~170 free). Token: My Profile → API Tokens → "Workers AI" template |

Ikhtiyari **Variables** (isi page par "Variables" tab mein):

| Variable | Kaam |
|---|---|
| `POSTS_PER_DAY` | Roz kitni posts (default 1) |
| `PUBLISH` | `false` = likh kar draft hi chhod de, aap khud publish karein |
| `DUPLICATE_ACTION` | `trash` = duplicate copies ko WordPress trash mein bhej de (default: sirf report) |
| `GEMINI_TEXT_MODEL`, `GEMINI_IMAGE_MODEL` | Model badalna ho to |

### 4. Pehle test karein

1. **Actions → Check setup and scan drafts → Run workflow.** Yeh login aur Gemini key test karta hai, aur Summary mein drafts, duplicates aur queue dikhata hai. Kuch nahi badalta.
2. **Actions → Daily blog post → Run workflow** mein **dry_run** tick karein. Article ka `preview.html` aur images run ke **Artifacts** mein milte hain. WordPress mein kuch nahi badalta.
3. Preview pasand aaye to aage kuch karne ki zaroorat nahi. Roz 9:23 AM (Pakistan) par khud chalega.

Koi khaas post abhi chalani ho to **Daily blog post → Run workflow** mein `post_id` daal dein. Post ID WordPress mein post edit karte waqt URL mein `post=123` hoti hai.

## Troubleshooting

- **401 / "check WP_USER / WP_APP_PASSWORD"**: username aur password dobara check karein. Agar sahi hain to aapka host `Authorization` header hata raha hai. Root `.htaccess` mein WordPress ke `RewriteEngine On` ke neeche yeh line daalein:
  `RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]`
- **403**: Wordfence, koi doosra security plugin ya hosting ka firewall REST API ya GitHub ke servers ko rok raha hai. REST API (`/wp-json/`) ko application passwords ke liye allow karein.
- **Post "Need your review" mein hai**: report mein wajah likhi hoti hai. Post ko WordPress mein khud theek karein, ya `state/progress.json` se uski entry hata dein, agent agli dafa dobara koshish karega.
- **Kisi post ko wapas purana karna ho**: WordPress mein post kholein, **Revisions** se pichla version restore karein. Purana content `state/backups/<id>.json` mein bhi hai.

## Local chalana

```bash
pip install -r requirements.txt
export WP_URL=https://thepowerappsninja.com WP_USER=... WP_APP_PASSWORD=... GEMINI_API_KEY=...
python -m agent.main check
python -m agent.main scan
python -m agent.main run --dry-run
python -m pytest -q   # tests: fake WordPress + fake Gemini
```
