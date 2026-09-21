# Deploy sheet: Ming on Railway

About fifteen minutes. Keys are pasted into Railway only, never into chat or into the repo.

## 1. Put the code on GitHub
1. Unzip `ming.zip`. You get a folder named `ming`.
2. On github.com create a new private repository named `ming`, empty (no README).
3. In a terminal inside the `ming` folder:
   ```
   git init
   git add .
   git commit -m "Ming v2"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/ming.git
   git push -u origin main
   ```
   (GitHub Desktop works too: Add local repository, publish, keep it private.)

## 2. Create the Railway service
1. railway.app, New Project, Deploy from GitHub repo, pick `ming`. Railway sees the Dockerfile and starts building.
2. Click the service, then Variables. Add these (Raw Editor is fastest):
   ```
   ALPACA_API_KEY=PK...
   ALPACA_SECRET_KEY=...
   OPENAI_API_KEY=sk-...           (add later if you do not have it yet)
   FIRECRAWL_API_KEY=fc-...
   DASHBOARD_PASSWORD=a long password you invent
   DATA_DIR=/data
   ```
3. Settings, Volumes, Add Volume, mount path `/data`. Without this every redeploy forgets the journal and the ideas file.
4. Settings, Networking, Generate Domain. Copy the URL.
5. The plan must be Hobby or higher; the free trial sleeps and misses 6:00 AM.

## 3. First run
1. Open the domain. Username anything, password the one you set.
2. The status line should read `paper` and the activity list should say `Ming online: mode paper, broker alpaca, brain connected` (or `brain NOT connected` until the OpenAI key is in; that is fine).
3. Click `research`. Within a minute the activity list fills. With the brain connected, theses appear on the right.
4. Send the activity list and the theses back to the build session.

## 4. Talk to him
1. Click the microphone next to `Talk to him`. Allow the microphone when the browser asks.
2. Say hello. His words appear as captions under the chat; yours appear on the right.
3. Click the microphone again to hang up. Voice needs the OpenAI key.

## 5. Every day after that
Nothing. He runs at 6:00, 9:00, 9:35, 15:45, 16:05 ET on weekdays. Open the page whenever you want to see or steer. Direction you give him in chat or voice is written to `ideas.md` and read the next morning.

## If something is wrong
- `Load failed` toast: the service is asleep or restarting. Railway, service, Deployments, look at the logs.
- `brain not connected` on the page: OPENAI_API_KEY is missing or wrong.
- A feed shows `failed` in the activity list: that source is down; the others carry on.
- 6:00 did not fire: check the plan (free trial sleeps) and the service logs for `scheduler on`.
- Voice fails to connect: browser must be Chrome, Edge, or Safari on https; check the OpenAI key has credit.
