<h1 align="center">
  <b>Save restricted Content Safe Bot master-V3 branch</b> | Summer Release V2
</h1>


Contact: [Telegram](https://t.me/safe_repo_bot)

---

## ABOUT THIS BRANCH
This branch uses Pyrogram V2. A user session login is required for Telegram message-link cloning; direct media and YouTube website-link generation do not require `/login`.

---
## Commands

- **`start`**: Launch the application.
- **`myplan`**: View your personalized plan.
- **`add`**: Add a new item or entry.
- **`rem`**: Remove an existing item.
- **`gcast`**: Broadcast a message or notification.
- **`stats`**: Display statistics and insights.
- **`speedtest`**: Run a network speed test.
- **`settings`**: Access and modify your settings.
- **`yt`**: Add a YouTube link to the public website as an official embed, or search videos.

The bot owner can use `/yt <YouTube video link>` or send a YouTube video URL directly to the
bot in a private chat to add it to the website's **YouTube** folder and get a website player
URL. The page embeds the original YouTube player: playback still uses YouTube, and the bot does
not download or re-upload the video. YouTube playback speeds depend on the official player;
site-hosted videos in the website player support speeds up to 4×. To search by keywords, set a
YouTube Data API v3 key in the `YOUTUBE_API_KEY` deployment environment variable.
Direct media links use the website cache up to `MAX_STREAM_FILE_SIZE_MB` (default 5000 MB);
configure `PUBLIC_BASE_URL` for the public player address. If downloading or caching fails, the
bot tries posting to the public stream channel; the bot needs permission to post there. Duplicate
uploads return their existing link instead of adding another catalog item.

Use `/archive` to copy a video you sent to the bot into your configured study group (reply to
the video with `/archive`), or `/archive <creator-provided direct HTTPS video-file URL>` to
fetch and upload an authorized video. Configure the destination with **Settings → Set Chat ID**
and make sure the bot can post in that group or channel. During `/batch`, you can choose a
group, supergroup, channel, or forum topic as the destination; add the bot with posting
permission and provide a topic message link when targeting a forum topic. YouTube watch links
are not direct file URLs; for your own uploads, download the file through YouTube Studio before
sending it to the bot.

## Features:

- Able to extarct the content the from private or public entities/channels/group
- direct rename and forward to channel/group/users
- Custome caption/thumbnail
- Automatically generate compact video thumbnails when no custom thumbnail is set
- Twelve progress-bar themes and 40 rotating emoji icons for each transfer, plus a separate
  animated progress display while generating website links
- Allow two concurrent media transfers to reduce waiting during parallel jobs
- auto default thumbnail removal from videos
- Deleting/Replacing words from file name and caption
- Easy to use and deploy
- auto pin messages(if they are pinned)
- login via phone number

## Deploying Guide - [SAFE REPO](https://t.me/safe_repo)

### How to get required vars
 
- API_ID and API_HASH from [telegram.org](https://my.telegram.org/auth)
- BOT_TOKEN: @Botfather on telegram
- OWNER_ID : Go to @missrose_bot on telegram and send `/info` to know your ID
- CHANNEL_ID : This will be used as Force Subscribe channel
- LOG_GROUP: Create a Group or Channel add you bot there and forward any message of that channel/group to @userinfobot to know the ID of you channel/group
- MONGO_DB: It is recommended to use your mongoDB to avoid session hacks and all
Note : You must make bot admin in both channels

### Deploy on `VPS`

Easy Method:
- Fork repo
- Go to ```config.py``` as below
- Fill variables inside the double quoted commas `""`  
- Now run following commands one by one...
```
sudo apt update
sudo apt install ffmpeg git python3-pip
git clone your_repo_link
cd you_repo_name
pip3 install -r requirements.txt
python3 -m safe_repo
```

- if you want bot to be running in background then enter `screen -S safe_repo_bot` before `python3 -m safe_repo` 
- after `python3 -m safe_repo`, click `ctrl+A`, `ctrl+D`
- if you want to stop bot, then enter `screen -r safe_repo_bot` and to kill screen enter `screen -S safe_repo_bot -X quit`.


## Deploy your bot on `heroku`
- Star the repo, and fork it in desktop mode
- Click on  [![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy)
- Fill your values and click deploy ✅

## Deploy on Render
- Fork and star the repo
- edit `config.py` same as guided for VPS deployment (you can edit on render also by filling enviroment variables)
- Go to render.com and singup/signin
- create new web service and select free plan
- connect github and your repository
- Click Deploy
- Done ✅


## Koyeb Deployment

- Fork and star the repo
- edit `config.py` same as guided for VPS deployment (you can edit on koyeb also by filling enviroment variables)
- Go to koyeb.com and singup/signin
- create new web service make sure you must choose build type `Dokerfile` because in Koyeb as a default it is checked to `buildpacks` so you have to change that.
- connect github and your repository
- Click Deploy
- Done ✅

## Terms of USE / Modification 
Visit [Terms](https://github.com/safe-repo/Save-Restricted-Content-Safe-Bot/blob/master/TERMS_OF_USE.md) and accept the guidelines.

# Updates
## Update: 16 Aug 2024

- Added `/logout` command to delete the session files from `mongodb` and `local memory`
- Fixed premium membership auto expiration (after subscription ends).
 
## Update: 7 JULY 2024

- Added `/login` method via phone number
- Added auto pinning of messages
- fixed float division by zero
- Session saving permanently
- Fixed old bugs
- Added /add and /remove along with parameters of `user_id` and `time` for the period of premium subscription.
- All old features along with `button` handler

## Important Note

**Note**: Changing the terms and commands doesn't magically make you a developer. Real development involves understanding the code, writing new functionalities, and debugging issues, not just renaming things. If only it were that easy!

## Contributers
My group members contact... Join @safe_repo to know them.

Thanks!
